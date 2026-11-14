// @ts-check
/**
 * Compare: blink, side-by-side, overlay, difference.
 * The frontend only DISPLAYS what the backend returns (ComparisonResult). Compatibility
 * verdicts, alignment and the difference image are never computed here.
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, logEvent } from "../core/store.js";
import { esc, shortDate } from "../core/dom.js";
import { createViewer } from "../components/viewer.js";
import { SPEEDS } from "../core/playback.js";

const errMsg = (/** @type {unknown} */ e) => (e instanceof ApiClientError ? e.message : "Something unexpected went wrong.");
const MODES = /** @type {const} */ ([["blink", "Blink"], ["sidebyside", "Side by side"], ["overlay", "Overlay"], ["difference", "Difference"]]);
const SYMBOL = { pass: "✓", warn: "⚠", fail: "✕", notEvaluated: "–" };
const LABEL = { pass: "pass", warn: "warning", fail: "fail", notEvaluated: "not evaluated" };
const VERDICT = { comparable: "Comparable", comparable_with_caveats: "Comparable with caveats", not_comparable: "Not comparable", unknown: "Unknown" };

/** @param {HTMLElement} root @returns {() => void} */
export function mount(root) {
  root.innerHTML = `<div class="compare">
    <div id="cmp-empty"></div>
    <form class="panel cmp-controls" id="cmp-form" hidden>
      <div class="field"><label for="cmp-a">Base observation</label><select class="select" id="cmp-a"></select></div>
      <div class="field"><label for="cmp-b">Target observation</label><select class="select" id="cmp-b"></select></div>
      <div class="field"><span class="label" id="mode-l">View</span>
        <div class="seg" role="radiogroup" aria-labelledby="mode-l">${MODES.map(([k, t]) => `<button type="button" class="tab" role="radio" data-mode="${k}" aria-checked="false">${t}</button>`).join("")}</div></div>
      <button class="btn" type="submit">Compare</button>
    </form>
    <div class="cmp-body" hidden>
      <div class="cmp-main">
        <div class="cmp-tools" id="cmp-tools"></div>
        <div class="cmp-stage" id="cmp-stage"></div>
        <p class="muted fine" id="cmp-caption"></p>
      </div>
      <aside class="panel" id="cmp-side"></aside>
    </div>
  </div>`;

  const $ = (/** @type {string} */ s) => /** @type {HTMLElement} */ (root.querySelector(s));
  const selA = /** @type {HTMLSelectElement} */ ($("#cmp-a")), selB = /** @type {HTMLSelectElement} */ ($("#cmp-b"));
  let alive = true;
  /** @type {any[]} */ let observations = [];
  /** @type {any} */ let result = null;
  let blinkTimer = 0, blinkShowTarget = false, blinkPlaying = true, blinkSpeed = 1, runToken = 0;
  /** @type {Array<ReturnType<typeof createViewer>>} */ let viewers = [];

  const mode = () => getState().compareMode;

  async function boot() {
    const { objectId } = getState();
    if (!objectId) { $("#cmp-empty").innerHTML = `<p class="notice">Pick a target in <a href="#/investigate">Investigate</a> first. Your selection is shared across tools.</p>`; return; }
    try {
      observations = (await api.listObservations(objectId)).items;
    } catch (e) { $("#cmp-empty").innerHTML = `<p class="notice notice-error">${esc(errMsg(e))}</p>`; return; }
    if (!alive) return;
    if (observations.length < 2) { $("#cmp-empty").innerHTML = `<p class="notice">At least two observations are needed to compare.</p>`; return; }
    $("#cmp-empty").innerHTML = "";
    const opts = observations.map(o => `<option value="${esc(o.observationId)}">${esc(shortDate(o.observedAt))} · ${esc(o.dataRelease ?? "release n/a")}${o.qualitySummary !== "good" ? ` · ${esc(o.qualitySummary)}` : ""}</option>`).join("");
    selA.innerHTML = opts; selB.innerHTML = opts;
    const st = getState();
    selA.value = observations.some(o => o.observationId === st.compareBaseId) ? /** @type {string} */ (st.compareBaseId) : observations[0].observationId;
    selB.value = observations.some(o => o.observationId === st.compareTargetId) ? /** @type {string} */ (st.compareTargetId) : observations[1].observationId;
    /** @type {HTMLElement} */ ($("#cmp-form")).hidden = false;
    syncModeButtons();
    run();
  }

  function syncModeButtons() {
    root.querySelectorAll("[data-mode]").forEach(b => b.setAttribute("aria-checked", String(/** @type {HTMLElement} */ (b).dataset.mode === mode())));
  }

  /** Request a comparison from the API. Difference needs its own request; the other views share one. */
  async function run() {
    const base = selA.value, target = selB.value, token = ++runToken;
    update({ compareBaseId: base, compareTargetId: target });
    if (base === target) { showMessage("Choose two different observations.", "warn"); return; }
    $("#cmp-body-wait")?.remove();
    showBody(); stopBlink(); disposeViewers();
    $("#cmp-stage").innerHTML = `<div class="stage-msg"><div class="spinner" aria-hidden="true"></div><p>Comparing…</p></div>`;
    $("#cmp-side").innerHTML = "";
    try {
      let r = await api.createComparison({ baseObservationId: base, targetObservationId: target, mode: mode() === "difference" ? "difference" : "overlay" });
      let tries = 6;
      while (r.availability.status === "processing" && tries-- > 0) {
        await new Promise(res => setTimeout(res, (r.availability.retryAfterSeconds ?? 2) * 1000));
        r = await api.getComparison(r.comparisonId);
      }
      if (!alive || token !== runToken) return;
      result = r; logEvent("comparison_created", r.comparisonId);
      renderResult();
    } catch (e) {
      if (token !== runToken) return;
      showMessage(errMsg(e), "error", e instanceof ApiClientError && e.retryable ? run : undefined);
    }
  }

  function showBody() { /** @type {HTMLElement} */ ($(".cmp-body")).hidden = false; }
  function showMessage(/** @type {string} */ text, /** @type {"warn"|"error"} */ kind, /** @type {(() => void)=} */ retry) {
    showBody();
    $("#cmp-stage").innerHTML = `<div class="stage-msg"><p class="notice notice-${kind}">${esc(text)}</p>${retry ? `<button class="btn" id="retry">Try again</button>` : ""}</div>`;
    $("#retry")?.addEventListener("click", () => retry?.());
    $("#cmp-tools").innerHTML = ""; $("#cmp-caption").textContent = "";
  }

  function renderResult() {
    renderSide();
    const stage = $("#cmp-stage"), tools = $("#cmp-tools");
    stopBlink(); disposeViewers(); tools.innerHTML = ""; $("#cmp-caption").textContent = "";
    const verdict = result.compatibility.verdict, avail = result.availability.status;

    if (verdict === "not_comparable") {
      stage.innerHTML = `<div class="stage-msg"><p class="notice notice-error"><strong>These observations are not comparable.</strong> Images are not shown because a comparison would not be scientifically meaningful. See the failing check on the right.</p></div>`;
      return;
    }
    if (avail !== "available" || !result.baseImage?.imageUrl) {
      stage.innerHTML = `<div class="stage-msg"><p class="notice notice-warn">${esc(result.availability.statusReason ?? "The comparison images are not available.")}</p></div>`;
      return;
    }
    const m = mode();
    const b = result.baseImage, t = result.targetImage;
    const baseLabel = shortDate(observations.find(o => o.observationId === b.observationId)?.observedAt);
    const targetLabel = shortDate(observations.find(o => o.observationId === t.observationId)?.observedAt);

    if (m === "sidebyside") {
      stage.innerHTML = `<div class="split"><div class="pane"><div id="vA"></div></div><div class="pane"><div id="vB"></div></div></div>`;
      const vA = makeViewer("#vA", "Base observation"), vB = makeViewer("#vB", "Target observation");
      vA.setHud(`<span class="badge">Base · ${esc(baseLabel)}</span>`); vB.setHud(`<span class="badge">Target · ${esc(targetLabel)}</span>`);
      vA.setImage(b); vB.setImage(t);
      // linked zoom and pan
      vA.link(vB); vB.link(vA);
      $("#cmp-caption").textContent = "Zoom and pan are linked between the two images.";
    } else if (m === "overlay") {
      stage.innerHTML = `<div class="stack"><div id="vA"></div><div id="vB" class="top"></div></div>`;
      const vA = makeViewer("#vA", "Overlay of base and target"), vB = makeViewer("#vB", "", false);
      vA.setImage(b); vB.setImage(t); vA.link(vB);
      vB.setFilterOpacity(0.5);
      vA.setHud(`<span class="badge">Base ${esc(baseLabel)} + Target ${esc(targetLabel)}</span>`);
      tools.innerHTML = `<label class="inline"><span class="label">Target opacity</span><input class="range" id="opacity" type="range" min="0" max="1" step="0.01" value="0.5" aria-label="Target image opacity"></label>`;
      $("#opacity").addEventListener("input", e => vB.setFilterOpacity(Number(/** @type {HTMLInputElement} */ (e.target).value)));
      $("#cmp-caption").textContent = "Drag the slider to fade between the base (0) and target (1) images.";
    } else if (m === "difference") {
      if (!result.differenceImage?.imageUrl) { stage.innerHTML = `<div class="stage-msg"><p class="notice notice-warn">No difference image was supplied for this pair.</p></div>`; return; }
      stage.innerHTML = `<div id="vA"></div>`;
      const v = makeViewer("#vA", "Difference image");
      v.setImage(result.differenceImage);
      v.setHud(`<span class="badge">Difference · ${esc(targetLabel)} − ${esc(baseLabel)}</span>`);
      $("#cmp-caption").textContent = "A difference can come from noise, alignment or calibration as well as real change. Check the compatibility checks.";
    } else {
      stage.innerHTML = `<div id="vA"></div>`;
      const v = makeViewer("#vA", "Blink comparison");
      blinkShowTarget = false; v.setImage(b);
      const tick = () => {
        blinkShowTarget = !blinkShowTarget;
        v.setImage(blinkShowTarget ? t : b);
        v.setHud(`<span class="badge">${blinkShowTarget ? "Target" : "Base"} · ${esc(blinkShowTarget ? targetLabel : baseLabel)}</span>`);
        if (blinkPlaying) blinkTimer = window.setTimeout(tick, 1000 / blinkSpeed);
      };
      v.setHud(`<span class="badge">Base · ${esc(baseLabel)}</span>`);
      tools.innerHTML = `<button class="btn" id="blink-toggle" aria-pressed="${blinkPlaying}">${blinkPlaying ? "Pause" : "Play"}</button>
        <button class="btn" id="blink-flip">Flip once</button>
        <label class="inline"><span class="label">Speed</span><select class="select" id="blink-speed" aria-label="Blink speed">${SPEEDS.map(s => `<option value="${s}" ${s === blinkSpeed ? "selected" : ""}>${s}×</option>`).join("")}</select></label>`;
      $("#blink-toggle").addEventListener("click", () => {
        blinkPlaying = !blinkPlaying; const bt = $("#blink-toggle"); bt.textContent = blinkPlaying ? "Pause" : "Play"; bt.setAttribute("aria-pressed", String(blinkPlaying));
        clearTimeout(blinkTimer); if (blinkPlaying) blinkTimer = window.setTimeout(tick, 1000 / blinkSpeed);
      });
      $("#blink-flip").addEventListener("click", () => { clearTimeout(blinkTimer); const was = blinkPlaying; blinkPlaying = false; tick(); blinkPlaying = was; if (was) blinkTimer = window.setTimeout(tick, 1000 / blinkSpeed); });
      $("#blink-speed").addEventListener("change", e => { blinkSpeed = Number(/** @type {HTMLSelectElement} */ (e.target).value); });
      if (blinkPlaying && !matchMedia("(prefers-reduced-motion: reduce)").matches) blinkTimer = window.setTimeout(tick, 1000 / blinkSpeed); else blinkPlaying = false;
      $("#cmp-caption").textContent = "Blinking alternates the aligned base and target images. Things that jump may have moved.";
    }
  }

  /** Viewer with a tiny linking helper for synchronised zoom/pan. */
  function makeViewer(/** @type {string} */ sel, /** @type {string} */ label, controls = true) {
    const el = $(sel); /** @type {Array<(v: any) => void>} */ const followers = [];
    const v = createViewer(el, { label, controls, onView: view => followers.forEach(f => f(view)) });
    viewers.push(v);
    return Object.assign(v, { link(/** @type {ReturnType<typeof createViewer>} */ other) { followers.push(view => other.setView(view)); } });
  }
  function disposeViewers() { viewers.forEach(v => v.destroy()); viewers = []; }
  function stopBlink() { clearTimeout(blinkTimer); }

  /** Compatibility verdict and every check, always visible. */
  function renderSide() {
    const c = result.compatibility;
    $("#cmp-side").innerHTML = `<h2>Compatibility</h2>
      <p><span class="verdict verdict-${esc(c.verdict)}">${esc(VERDICT[/** @type {keyof typeof VERDICT} */ (c.verdict)] ?? c.verdict)}</span></p>
      <ul class="checks">${c.checks.map((/** @type {any} */ k) => `<li class="check check-${esc(k.result)}"><span class="cs" aria-hidden="true">${SYMBOL[/** @type {keyof typeof SYMBOL} */ (k.result)] ?? "?"}</span>
        <div><strong>${esc(k.name)}</strong> <span class="muted">${esc(LABEL[/** @type {keyof typeof LABEL} */ (k.result)] ?? k.result)}</span><br><span class="fine muted">${esc(k.detail)}</span></div></li>`).join("")}</ul>
      ${result.alignment ? `<h3 class="label group">Alignment</h3><p class="fine">${esc(result.alignment.method)}${result.alignment.residualArcsec !== undefined ? ` · residual ${esc(result.alignment.residualArcsec)} arcsec` : ""}</p>` : ""}
      ${result.warnings.length ? `<h3 class="label group">Caveats</h3>${result.warnings.map((/** @type {string} */ w) => `<p class="notice notice-warn fine">${esc(w)}</p>`).join("")}` : ""}
      ${isMock() ? `<p class="muted fine">Verdicts and images here are illustrative.</p>` : ""}`;
  }

  // ---- events -------------------------------------------------------------------
  $("#cmp-form").addEventListener("submit", e => { e.preventDefault(); run(); });
  root.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => {
    const next = /** @type {any} */ (/** @type {HTMLElement} */ (b).dataset.mode), prev = mode();
    update({ compareMode: next }); syncModeButtons();
    if (!result) return;
    // Difference needs a different request; the other three views reuse the same result.
    if ((next === "difference") !== (prev === "difference")) run(); else renderResult();
  }));

  boot();
  return () => { alive = false; runToken++; stopBlink(); disposeViewers(); };
}
