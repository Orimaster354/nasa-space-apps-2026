// @ts-check
/**
 * Comparison modes of the Investigate workspace: blink, side by side, overlay, difference.
 *
 * The frontend only DISPLAYS what the backend returns (ComparisonResult): compatibility verdict,
 * checks, alignment, aligned images and the difference image are never computed here.
 *
 * Rendering reuses the workspace's main viewer for blink and difference (so zoom/pan carries
 * over from normal viewing), and a second layer of the same size inside the same frame for
 * side-by-side and overlay. Zoom/pan is copied across in both directions.
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, logEvent } from "../core/store.js";
import { esc, shortDate } from "../core/dom.js";
import { createViewer } from "./viewer.js";
import { SPEEDS } from "../core/playback.js";

const errMsg = (/** @type {unknown} */ e) => (e instanceof ApiClientError ? e.message : "Something unexpected went wrong.");
/** Contract ObservationImage ({imageUrl,width,height}) -> the viewer's {url,width,height}. */
const asImage = (/** @type {{imageUrl: string, width: number, height: number}} */ i) => ({ url: i.imageUrl, width: i.width, height: i.height });

const SYMBOL = { pass: "✓", warn: "⚠", fail: "✕", notEvaluated: "–" };
const LABEL = { pass: "pass", warn: "warning", fail: "fail", notEvaluated: "not evaluated" };
const VERDICT = { comparable: "Comparable", comparable_with_caveats: "Comparable with caveats", not_comparable: "Not comparable", unknown: "Unknown" };

/**
 * @typedef {ReturnType<typeof createViewer>} Viewer
 * @typedef {Object} ComparisonHost
 * @property {Viewer} viewer                 the workspace's main viewer
 * @property {HTMLElement} viewerEl          its element (hidden while a split/overlay layer shows)
 * @property {HTMLElement} layerEl           empty element stacked over the main viewer, same size
 * @property {HTMLElement} pairEl            controls row (date pickers + mode tools)
 * @property {HTMLElement} sideEl            compatibility panel
 * @property {() => any[]} observations
 * @property {(observationId: string) => import("./viewer.js").Marker[]} markersFor  candidate markers for one observation
 * @property {(verdict: string|null) => void} [onVerdict]  compatibility verdict for the toolbar badge (null when not comparing)
 */

/** @param {ComparisonHost} host */
export function createComparison(host) {
  const { viewer } = host;
  /** @type {Map<string, Promise<any>>} */ const results = new Map();
  /** @type {any} */ let result = null;
  /** @type {Viewer[]} */ let layerViewers = [];
  let token = 0, blinkTimer = 0, blinkShowTarget = false, blinkPlaying = true, blinkSpeed = 1, opacity = 0.5, active = false;
  let linked = true, split = 50;          // side by side: synchronised zoom/pan, divider position (%)

  const mode = () => getState().compareMode;
  const obsLabel = (/** @type {string} */ id) => shortDate(host.observations().find(o => o.observationId === id)?.observedAt);
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  /** Pick a sensible pair if none is stored for this object: the current date and the next one. */
  function ensurePair() {
    const list = host.observations(), ids = list.map(o => o.observationId), s = getState();
    if (list.length < 2) return false;
    let a = ids.includes(/** @type {string} */ (s.compareBaseId)) ? s.compareBaseId : null;
    let b = ids.includes(/** @type {string} */ (s.compareTargetId)) ? s.compareTargetId : null;
    if (!a || !b || a === b) {
      const i = Math.max(0, ids.indexOf(/** @type {string} */ (s.observationId)));
      a = ids[i === ids.length - 1 ? i - 1 : i]; b = ids[i === ids.length - 1 ? i : i + 1];
      update({ compareBaseId: a, compareTargetId: b });
    }
    return true;
  }

  /** One request per pair and kind; switching between blink/side/overlay reuses it. */
  function fetchResult(/** @type {string} */ base, /** @type {string} */ target, /** @type {boolean} */ diff) {
    const key = `${base}|${target}|${diff ? "d" : "o"}`;
    if (!results.has(key)) {
      const p = (async () => {
        let r = await api.createComparison({ baseObservationId: base, targetObservationId: target, mode: diff ? "difference" : "overlay" });
        let tries = 6;
        while (r.availability.status === "processing" && tries-- > 0) {          // contract: poll the same resource
          await new Promise(res => setTimeout(res, (r.availability.retryAfterSeconds ?? 2) * 1000));
          r = await api.getComparison(r.comparisonId);
        }
        logEvent("comparison_created", r.comparisonId);
        return r;
      })();
      results.set(key, p);
      p.catch(() => results.delete(key));
    }
    return /** @type {Promise<any>} */ (results.get(key));
  }

  // ---- layer viewers (side by side, overlay) ----------------------------------------------
  function disposeLayer() {
    layerViewers.forEach(v => v.destroy()); layerViewers = [];
    host.layerEl.innerHTML = ""; host.layerEl.hidden = true; host.viewerEl.hidden = false;
  }
  /** Leaving a split/overlay: hand its zoom/pan back to the main viewer. */
  function handBackView() { const v = layerViewers[0]?.getView(); disposeLayer(); if (v) viewer.setView(v); }

  function makeLayerViewer(/** @type {HTMLElement} */ el, /** @type {string} */ label, controls = true) {
    /** @type {Array<(v: any) => void>} */ const followers = [];
    const v = createViewer(el, { label, controls, onView: view => followers.forEach(f => f(view)) });
    layerViewers.push(v);
    // Followers apply only while "Link views" is on, so each pane can also be inspected on its own.
    return Object.assign(v, { follow(/** @type {Viewer} */ other) { followers.push(view => { if (linked) other.setView(view); }); } });
  }

  /** Draggable / keyboard-resizable divider between the side-by-side panes. */
  function wireSplit(/** @type {HTMLElement} */ el) {
    const handle = /** @type {HTMLElement} */ (el.querySelector(".split-handle"));
    const set = (/** @type {number} */ v) => { split = Math.round(Math.min(80, Math.max(20, v))); el.style.setProperty("--split", `${split}%`); handle.setAttribute("aria-valuenow", String(split)); };
    handle.addEventListener("pointerdown", e => {
      handle.setPointerCapture(e.pointerId);
      const move = (/** @type {PointerEvent} */ ev) => { const r = el.getBoundingClientRect(); set(((ev.clientX - r.left) / r.width) * 100); };
      const up = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); };
      handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
    });
    handle.addEventListener("keydown", e => {
      const d = e.key === "ArrowLeft" ? -5 : e.key === "ArrowRight" ? 5 : 0;
      if (d) { e.preventDefault(); set(split + d); } else if (e.key === "Home") set(50);
    });
    handle.addEventListener("dblclick", () => set(50));
  }

  // ---- rendering ---------------------------------------------------------------------------
  function stopBlink() { clearTimeout(blinkTimer); }

  async function render() {
    const m = mode(), t = ++token;
    stopBlink();
    if (!ensurePair()) {
      disposeLayer(); viewer.clear(); viewer.setHud("");
      viewer.setState({ kind: "empty", message: "At least two observations are needed to compare." });
      host.pairEl.innerHTML = ""; host.sideEl.hidden = true; return;
    }
    renderPair();
    const { compareBaseId: base, compareTargetId: target } = getState();
    if (base === target) { viewer.clear(); viewer.setState({ kind: "empty", message: "Choose two different dates." }); return; }

    const startView = layerViewers[0]?.getView() ?? viewer.getView();
    viewer.setState({ kind: "loading", message: "Comparing…" });
    try {
      result = await fetchResult(/** @type {string} */ (base), /** @type {string} */ (target), m === "difference");
    } catch (e) {
      if (t !== token) return;
      disposeLayer(); viewer.clear();
      viewer.setState({ kind: "failed", message: errMsg(e), ...(e instanceof ApiClientError && e.retryable ? { onRetry: render } : {}) });
      return;
    }
    if (t !== token || !active) return;
    renderSide();
    const verdict = result.compatibility.verdict;
    host.onVerdict?.(verdict);
    if (verdict === "not_comparable") {
      disposeLayer(); viewer.clear(); viewer.setHud("");
      viewer.setState({ kind: "unavailable", message: "These dates are not comparable, so the images are not shown. The failing check is listed beside the viewer." });
      return;
    }
    if (result.availability.status !== "available" || !result.baseImage?.imageUrl) {
      disposeLayer(); viewer.clear();
      viewer.setState({ kind: "unavailable", message: result.availability.statusReason ?? "The comparison images are not available." }); return;
    }
    const b = result.baseImage, tg = result.targetImage, bl = obsLabel(b.observationId), tl = obsLabel(tg.observationId);
    const ill = isMock() ? `<span class="badge badge-illustrative">illustrative</span>` : "";

    if (m === "sidebyside" || m === "overlay") {
      host.viewerEl.hidden = true; host.layerEl.hidden = false;
      layerViewers.forEach(v => v.destroy()); layerViewers = [];
      if (m === "sidebyside") {
        host.layerEl.innerHTML = `<div class="split" style="--split:${split}%"><div class="pane" id="cmp-va"></div>
          <div class="split-handle" role="separator" tabindex="0" aria-orientation="vertical" aria-label="Resize the two panes" aria-valuemin="20" aria-valuemax="80" aria-valuenow="${split}"></div>
          <div class="pane" id="cmp-vb"></div></div>`;
        wireSplit(/** @type {HTMLElement} */ (host.layerEl.querySelector(".split")));
        const vA = makeLayerViewer(/** @type {HTMLElement} */ (host.layerEl.querySelector("#cmp-va")), "Date A");
        const vB = makeLayerViewer(/** @type {HTMLElement} */ (host.layerEl.querySelector("#cmp-vb")), "Date B");
        vA.setHud(`<span class="badge">A · ${esc(bl)}</span>`); vB.setHud(`<span class="badge">B · ${esc(tl)}</span>${ill}`);
        await Promise.all([vA.setImage(asImage(b)), vB.setImage(asImage(tg))]);
        vA.setMarkers(host.markersFor(b.observationId)); vB.setMarkers(host.markersFor(tg.observationId));
        vA.follow(vB); vB.follow(vA);
        if (startView) { vA.setView(startView); vB.setView(startView); }
      } else {
        host.layerEl.innerHTML = `<div class="stack"><div id="cmp-va"></div><div id="cmp-vb" class="top"></div></div>`;
        const vA = makeLayerViewer(/** @type {HTMLElement} */ (host.layerEl.querySelector("#cmp-va")), "Overlay of both dates");
        const vB = makeLayerViewer(/** @type {HTMLElement} */ (host.layerEl.querySelector("#cmp-vb")), "", false);
        vA.setHud(`<span class="badge">A ${esc(bl)} + B ${esc(tl)}</span>${ill}`);
        await Promise.all([vA.setImage(asImage(b)), vB.setImage(asImage(tg))]);
        vA.follow(vB); vB.setFilterOpacity(opacity);
        if (startView) { vA.setView(startView); vB.setView(startView); }
      }
      return;
    }

    // Blink and difference use the main viewer, so the user's zoom/pan stays where it was.
    if (layerViewers.length) handBackView();
    if (m === "difference") {
      if (!result.differenceImage?.imageUrl) { viewer.clear(); viewer.setState({ kind: "unavailable", message: "No difference image was supplied for this pair." }); return; }
      await viewer.setImage(asImage(result.differenceImage));
      viewer.setMarkers([]);
      viewer.setHud(`<span class="badge">Difference · B ${esc(tl)} − A ${esc(bl)}</span>
        <span class="badge ${isMock() ? "badge-illustrative" : ""}">${isMock() ? "illustrative difference image" : "calculated by the backend"}</span>`);
      return;
    }
    // blink
    blinkShowTarget = false;
    const show = async () => {
      const im = blinkShowTarget ? tg : b;
      viewer.setHud(`<span class="badge">${blinkShowTarget ? "B" : "A"} · ${esc(blinkShowTarget ? tl : bl)}</span>${ill}`);
      await viewer.setImage(asImage(im));
      viewer.setMarkers(host.markersFor(im.observationId));
    };
    const tick = async () => { if (t !== token) return; blinkShowTarget = !blinkShowTarget; await show(); if (blinkPlaying && t === token) blinkTimer = window.setTimeout(tick, 1000 / blinkSpeed); };
    await show();
    flip = async () => { clearTimeout(blinkTimer); blinkShowTarget = !blinkShowTarget; await show(); if (blinkPlaying) blinkTimer = window.setTimeout(tick, 1000 / blinkSpeed); };
    restartBlink = () => { clearTimeout(blinkTimer); if (blinkPlaying) blinkTimer = window.setTimeout(tick, 1000 / blinkSpeed); };
    if (reduced()) blinkPlaying = false;
    restartBlink();
    renderPair();
  }
  /** @type {() => void} */ let flip = () => {};
  /** @type {() => void} */ let restartBlink = () => {};

  /** Date pickers + the current mode's small tools (replaces the timeline while comparing). */
  function renderPair() {
    const list = host.observations(), s = getState(), m = mode();
    const opts = (/** @type {string|null} */ sel) => list.map(o => `<option value="${esc(o.observationId)}" ${o.observationId === sel ? "selected" : ""}>${esc(shortDate(o.observedAt))}${o.qualitySummary !== "good" && o.qualitySummary !== "unknown" ? ` · ${esc(o.qualitySummary)}` : ""}</option>`).join("");
    const tools = m === "blink"
      ? `<button class="btn" type="button" data-c="play" aria-pressed="${blinkPlaying}">${blinkPlaying ? "Pause" : "Blink"}</button>
         <button class="btn" type="button" data-c="flip">Flip once</button>
         <label class="speed"><span class="muted">Speed</span><select class="select" data-c="speed" aria-label="Blink speed">${SPEEDS.map(v => `<option value="${v}" ${v === blinkSpeed ? "selected" : ""}>${v}×</option>`).join("")}</select></label>`
      : m === "overlay"
      ? `<label class="speed"><span class="muted">A</span><input class="range" type="range" data-c="opacity" min="0" max="1" step="0.01" value="${opacity}" aria-label="Fade between date A and date B" style="width:140px"><span class="muted">B</span></label>`
      : m === "sidebyside" ? `<button class="btn" type="button" data-c="link" aria-pressed="${linked}" title="Synchronise zoom and pan between the panes">${linked ? "⛓ Views linked" : "Views independent"}</button>
         <span class="muted fine">Drag the divider to resize.</span>`
      : `<span class="muted fine">Differences can come from noise, alignment or calibration, not only real change.</span>`;
    host.pairEl.innerHTML = `
      <div class="pair" role="group" aria-label="Dates to compare">
        <label class="pair-pick"><span class="label">A</span><select class="select" data-c="a" aria-label="Date A (base)">${opts(s.compareBaseId)}</select></label>
        <button class="btn btn-icon" type="button" data-c="swap" aria-label="Swap dates A and B" title="Swap">⇄</button>
        <label class="pair-pick"><span class="label">B</span><select class="select" data-c="b" aria-label="Date B (target)">${opts(s.compareTargetId)}</select></label>
      </div>
      <div class="pair-tools">${tools}</div>`;
  }

  host.pairEl.addEventListener("change", e => {
    const el = /** @type {HTMLSelectElement} */ (e.target), c = el.dataset.c;
    if (c === "a") { update({ compareBaseId: el.value }); render(); }
    else if (c === "b") { update({ compareTargetId: el.value }); render(); }
    else if (c === "speed") { blinkSpeed = Number(el.value); restartBlink(); }
  });
  host.pairEl.addEventListener("input", e => {
    const el = /** @type {HTMLInputElement} */ (e.target);
    if (el.dataset.c === "opacity") { opacity = Number(el.value); layerViewers[1]?.setFilterOpacity(opacity); }
  });
  host.pairEl.addEventListener("click", e => {
    const c = /** @type {HTMLElement} */ (e.target).closest("[data-c]")?.getAttribute("data-c");
    if (c === "swap") { const s = getState(); update({ compareBaseId: s.compareTargetId, compareTargetId: s.compareBaseId }); render(); }
    else if (c === "flip") flip();
    else if (c === "link") {
      linked = !linked;
      if (linked && layerViewers[0]) layerViewers[1]?.setView(layerViewers[0].getView());   // re-sync B to A
      renderPair();
    }
    else if (c === "play") { blinkPlaying = !blinkPlaying; restartBlink(); renderPair(); }
  });

  /** Compatibility verdict and every check, always visible while comparing. */
  function renderSide() {
    const c = result.compatibility;
    host.sideEl.hidden = false;
    host.sideEl.innerHTML = `<h2>Compatibility</h2>
      <p><span class="verdict verdict-${esc(c.verdict)}">${esc(VERDICT[/** @type {keyof typeof VERDICT} */ (c.verdict)] ?? c.verdict)}</span></p>
      <ul class="checks">${c.checks.map((/** @type {any} */ k) => `<li class="check check-${esc(k.result)}"><span class="cs" aria-hidden="true">${SYMBOL[/** @type {keyof typeof SYMBOL} */ (k.result)] ?? "?"}</span>
        <div><strong>${esc(k.name)}</strong> <span class="muted">${esc(LABEL[/** @type {keyof typeof LABEL} */ (k.result)] ?? k.result)}</span><br><span class="fine muted">${esc(k.detail)}</span></div></li>`).join("")}</ul>
      ${result.alignment ? `<h3 class="label group">Alignment</h3><p class="fine">${esc(result.alignment.method)}${result.alignment.residualArcsec !== undefined ? ` · residual ${esc(result.alignment.residualArcsec)} arcsec` : ""}</p>` : ""}
      ${result.warnings.length ? `<h3 class="label group">Caveats</h3>${result.warnings.map((/** @type {string} */ w) => `<p class="notice notice-warn fine">${esc(w)}</p>`).join("")}` : ""}
      ${isMock() ? `<p class="muted fine">Verdicts and images here are illustrative.</p>` : ""}`;
  }

  return {
    /** Enter (or switch between) comparison modes. */
    show() { active = true; return render(); },
    /** Back to normal viewing: stop blinking, hand zoom/pan back, hide comparison panels. */
    hide() { active = false; token++; stopBlink(); if (layerViewers.length) handBackView(); host.pairEl.innerHTML = ""; host.sideEl.hidden = true; host.sideEl.innerHTML = ""; host.onVerdict?.(null); },
    /** Observations changed (new target): cached results no longer apply. */
    reset() { results.clear(); result = null; },
    destroy() { active = false; token++; stopBlink(); disposeLayer(); },
  };
}
