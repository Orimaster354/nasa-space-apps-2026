// @ts-check
/**
 * Investigate: the main workspace (candidates | viewer + timeline | object details).
 * One continuous workspace: the viewer's mode bar switches between normal viewing (playback
 * through dates) and the comparison modes (blink, side by side, overlay, difference) without
 * leaving the page. Object, candidate, dates and zoom/pan are kept across modes.
 * In "view" mode viewer and timeline are both driven by ONE value, `store.observationId`.
 * All data arrives through the API client; nothing is fetched or computed here.
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, subscribe, logEvent } from "../core/store.js";
import { esc, formatRa, formatDec, shortDate } from "../core/dom.js";
import { createPlayback } from "../core/playback.js";
import { loadFrame, forgetFrame, preload } from "../core/frames.js";
import { createViewer } from "../components/viewer.js";
import { createTimeline } from "../components/timeline.js";
import { renderObjectDetails } from "../components/objectDetails.js";
import { mountGuided } from "../components/guided.js";
import { createComparison } from "../components/comparison.js";
import { emit } from "../core/events.js";

const MODES = /** @type {const} */ ([["view", "View"], ["blink", "Blink"], ["sidebyside", "Side by side"], ["overlay", "Overlay"], ["difference", "Difference"]]);

const errMsg = (/** @type {unknown} */ e) => (e instanceof ApiClientError ? e.message : "Something unexpected went wrong.");

/** @param {HTMLElement} root @returns {() => void} unmount */
export function mount(root) {
  // Image-first layout: the viewer spans the workspace; candidates (left) and details/compatibility
  // (right) are drawers that push the viewer aside only while open (and slide over it on narrow screens).
  root.innerHTML = `
    <div class="ws" id="ws">
      <aside class="ws-drawer ws-left" id="drawer-left" aria-label="Candidates and guided investigation">
        <div class="drawer-head"><span class="label">Candidates</span><button type="button" class="drawer-close" data-drawer="left" aria-label="Close candidates">✕</button></div>
        <section class="panel" id="cand-panel" aria-labelledby="cand-h"></section>
        <section id="guided-panel"></section>
      </aside>
      <section class="ws-main">
        <form class="ws-context target-bar" id="target-form" role="search">
          <button type="button" class="ctx-toggle" data-drawer="left" aria-controls="drawer-left" aria-expanded="false">
            <span aria-hidden="true">◧</span> Candidates <span class="ctx-count" id="cand-count"></span></button>
          <label class="sr-only" for="target-select">Sample target</label>
          <select class="select" id="target-select"></select>
          <label class="sr-only" for="target-q">Search by name or RA Dec</label>
          <input class="input" id="target-q" placeholder="Name, or RA Dec (deg)" autocomplete="off">
          <button class="btn" type="submit">Go</button>
          <p class="ctx-summary mono" id="ctx-summary" aria-live="polite"></p>
          <button type="button" class="ctx-toggle" data-drawer="right" aria-controls="drawer-right" aria-expanded="false">
            Details <span aria-hidden="true">◨</span></button>
        </form>
        <div id="target-status" role="status"></div>
        <div class="ws-toolbar" role="toolbar" aria-label="Viewer modes and tools">
          <div class="modebar" role="radiogroup" aria-label="Viewer mode">${MODES.map(([k, t]) =>
            `<button type="button" class="mode" role="radio" data-mode="${k}" aria-checked="false">${t}</button>`).join("")}</div>
          <button type="button" class="verdict-chip" id="verdict-chip" hidden aria-controls="drawer-right"></button>
          <span class="ws-spacer"></span>
          <button type="button" class="tool-btn" id="btn-full" title="Fullscreen (both panes when comparing)"><span aria-hidden="true">⛶</span> Fullscreen</button>
          <button type="button" class="tool-btn" id="btn-focus" aria-pressed="false" title="Distraction-free viewing (Esc to exit)"><span aria-hidden="true">◎</span> Focus</button>
        </div>
        <div class="viewer-wrap" id="viewer-wrap"><div id="viewer"></div><div id="cmp-layer" class="cmp-layer" hidden></div></div>
        <div class="panel timeline-panel"><div id="timeline"></div><div id="pair" class="pair-row"></div></div>
      </section>
      <aside class="ws-drawer ws-right" id="drawer-right" aria-label="Object details and comparison checks">
        <div class="drawer-head"><span class="label">Details</span><button type="button" class="drawer-close" data-drawer="right" aria-label="Close details">✕</button></div>
        <section class="panel" id="cmp-side" aria-live="polite" hidden></section>
        <section class="panel" id="details"></section>
      </aside>
      <div class="drawer-scrim" id="drawer-scrim" hidden></div>
    </div>`;

  const $ = (/** @type {string} */ id) => /** @type {HTMLElement} */ (root.querySelector(id));
  const ws = $("#ws");

  // ---- drawers -------------------------------------------------------------------
  // Wide screens: drawers are columns that push the viewer (state persisted in the store).
  // Narrow screens: drawers slide over the page and close on Escape / outside click.
  const wide = () => matchMedia("(min-width: 1000px)").matches;
  /** @type {"left"|"right"|null} */ let overlayOpen = null;
  /** @type {{left: boolean, right: boolean}|null} */ let beforeSideBySide = null;
  function applyPanels() {
    const p = getState().panels, w = wide();
    const left = w ? p.left : overlayOpen === "left", right = w ? p.right : overlayOpen === "right";
    ws.classList.toggle("left-open", left); ws.classList.toggle("right-open", right);
    ws.classList.toggle("is-overlay", !w);
    /** @type {HTMLElement} */ ($("#drawer-scrim")).hidden = w || !overlayOpen;
    root.querySelectorAll(".ctx-toggle[data-drawer]").forEach(b => b.setAttribute("aria-expanded", String(/** @type {HTMLElement} */ (b).dataset.drawer === "left" ? left : right)));
    /** @type {HTMLElement} */ ($("#drawer-left")).inert = !left; /** @type {HTMLElement} */ ($("#drawer-right")).inert = !right;
  }
  function toggleDrawer(/** @type {"left"|"right"} */ side, /** @type {boolean} */ open = !ws.classList.contains(`${side}-open`)) {
    if (wide()) update({ panels: { ...getState().panels, [side]: open } });
    else { overlayOpen = open ? side : null; applyPanels(); }
    if (open) queueMicrotask(() => /** @type {HTMLElement|null} */ ($(`#drawer-${side} .drawer-close`))?.focus());
  }
  root.addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target);
    const d = t.closest("[data-drawer]"); if (d) { const side = /** @type {"left"|"right"} */ (d.getAttribute("data-drawer")); toggleDrawer(side, d.classList.contains("drawer-close") ? false : undefined); }
    if (t.id === "drawer-scrim") { overlayOpen = null; applyPanels(); }
  });
  const mq = matchMedia("(min-width: 1000px)"), onMq = () => { overlayOpen = null; applyPanels(); };
  mq.addEventListener("change", onMq);

  // ---- toolbar: fullscreen + distraction-free focus mode ---------------------------
  $("#btn-full").addEventListener("click", () => {
    const wrap = $("#viewer-wrap");
    if (document.fullscreenElement) document.exitFullscreen?.();
    else wrap.requestFullscreen?.().catch(() => wrap.classList.toggle("viewer-expanded"));
  });
  function setFocus(/** @type {boolean} */ on) {
    document.body.classList.toggle("focus-mode", on);
    $("#btn-focus").setAttribute("aria-pressed", String(on));
    emit("focus_mode", { on });
  }
  $("#btn-focus").addEventListener("click", () => setFocus(!document.body.classList.contains("focus-mode")));
  const onKey = (/** @type {KeyboardEvent} */ e) => {
    if (e.key !== "Escape") return;
    if (overlayOpen) { overlayOpen = null; applyPanels(); }
    else if (document.body.classList.contains("focus-mode")) setFocus(false);
  };
  document.addEventListener("keydown", onKey);
  $("#verdict-chip").addEventListener("click", () => toggleDrawer("right", true));
  const statusEl = $("#target-status"), candEl = $("#cand-panel"), detailsEl = $("#details");

  // ---- local view state (data only; selection state lives in the store) -------
  /** @type {any} */ let object = null, extras = {};
  /** @type {any[]} */ let observations = [], candidates = [];
  let alive = true, renderToken = 0, measureToken = 0, hoverTimer = 0, viewRestored = false;
  /** @type {any} */ let measurement = null;
  /** @type {"idle"|"loading"|"error"|"playing"} */ let measureState = "idle";
  let measureError = "";

  const activeIndex = () => Math.max(0, observations.findIndex(o => o.observationId === getState().observationId));

  // ---- components -----------------------------------------------------------
  const viewer = createViewer($("#viewer"), {
    label: "Sky image of the selected target",
    onMarkerClick: id => update({ candidateId: getState().candidateId === id ? null : id }),
    onClick: (x, y) => { guided?.handleViewerClick(x, y); },
    onHover: (x, y) => {
      if (x === null || y === null) return viewer.setReadout("");
      clearTimeout(hoverTimer);
      hoverTimer = window.setTimeout(async () => {
        const id = getState().observationId; if (!id) return;
        try { const { sky } = await api.pixelToSky(id, x, y); viewer.setReadout(`RA ${formatRa(sky.raDeg)}  Dec ${formatDec(sky.decDeg)}${isMock() ? "  (sample)" : ""}`); } catch { viewer.setReadout(""); }
      }, 80);
    },
  });

  const playback = createPlayback({
    count: () => observations.length,
    get: activeIndex,
    set: i => update({ observationId: observations[i]?.observationId ?? null }),
    onChange: () => { timeline.setPlaying(playback.playing); if (!playback.playing) refreshMeasurement(); },
  });

  const timeline = createTimeline($("#timeline"), {
    onSelect: i => { playback.pause(); update({ observationId: observations[i]?.observationId ?? null }); },
    onToggle: () => { playback.toggle(); emit(playback.playing ? "play" : "pause"); },
    onStop: () => { playback.stop(); emit("stop"); },
    onStep: d => { playback.pause(); playback.step(d); emit("step", { delta: d }); },
    onSpeed: s => playback.setSpeed(s),
  });

  const isView = () => getState().compareMode === "view";

  const comparison = createComparison({
    viewer, viewerEl: $("#viewer"), layerEl: $("#cmp-layer"), pairEl: $("#pair"), sideEl: $("#cmp-side"),
    observations: () => observations,
    markersFor: id => markersAt(observations.findIndex(o => o.observationId === id)),
    onVerdict: v => {
      const chip = $("#verdict-chip"), label = { comparable: "Comparable", comparable_with_caveats: "Comparable with caveats", not_comparable: "Not comparable", unknown: "Compatibility unknown" };
      chip.hidden = !v; if (!v) return;
      chip.className = `verdict-chip verdict-${v}`;
      chip.innerHTML = `${esc(label[/** @type {keyof typeof label} */ (v)] ?? v)} <span aria-hidden="true">›</span><span class="sr-only">, show all checks</span>`;
      if (v === "not_comparable") emit("not_comparable");
    },
  });

  /** Side by side needs every pixel: collapse both drawers on entry and restore them on exit. */
  function sideBySidePanels() {
    const sbs = getState().compareMode === "sidebyside";
    if (sbs && !beforeSideBySide) { beforeSideBySide = { ...getState().panels }; update({ panels: { left: false, right: false } }); }
    else if (!sbs && beforeSideBySide) { const p = beforeSideBySide; beforeSideBySide = null; update({ panels: p }); }
  }

  /** Switch the single viewer between normal viewing and a comparison mode (no navigation, no reload). */
  function applyMode() {
    sideBySidePanels();
    if (isView()) /** @type {HTMLElement} */ ($("#verdict-chip")).hidden = true;
    const view = isView();
    root.querySelectorAll(".modebar [data-mode]").forEach(b => {
      const on = /** @type {HTMLElement} */ (b).dataset.mode === getState().compareMode;
      b.setAttribute("aria-checked", String(on)); b.setAttribute("tabindex", on ? "0" : "-1");
    });
    /** @type {HTMLElement} */ ($("#timeline")).hidden = !view;
    if (!object || !observations.length) return;
    if (view) { comparison.hide(); renderFrame(); }
    else { playback.pause(); comparison.show(); }
  }
  const modebar = /** @type {HTMLElement} */ (root.querySelector(".modebar"));
  modebar.addEventListener("click", e => {
    const b = /** @type {HTMLElement} */ (e.target).closest("[data-mode]"); if (!b) return;
    update({ compareMode: /** @type {any} */ (b.getAttribute("data-mode")) });
  });
  modebar.addEventListener("keydown", e => {
    const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0; if (!dir) return;
    e.preventDefault();
    const i = MODES.findIndex(([k]) => k === getState().compareMode), next = MODES[(i + dir + MODES.length) % MODES.length][0];
    update({ compareMode: next });
    queueMicrotask(() => /** @type {HTMLElement|null} */ (modebar.querySelector(`[data-mode="${next}"]`))?.focus());
  });

  /** @type {Awaited<ReturnType<typeof mountGuided>>|null} */ let guided = null;
  mountGuided($("#guided-panel"), {
    ensureTarget: async id => { if (getState().objectId !== id) selectObject(id); },
    getCandidates: () => candidates,
  }).then(g => { if (alive) { guided = g; syncMarkers(); } else g.destroy(); });

  // ---- target selection -------------------------------------------------------
  /** Fills the sample picker. Returns the samples so boot can open the first one. */
  async function loadSamples() {
    const sel = /** @type {HTMLSelectElement} */ ($("#target-select"));
    try {
      const list = await api.listSampleObjects();
      sel.hidden = list.length === 0;
      sel.innerHTML = list.map((/** @type {any} */ o) => `<option value="${esc(o.objectId)}">${esc(o.name)}</option>`).join("");
      sel.value = getState().objectId ?? "";
      return list;
    } catch { sel.hidden = true; return []; }
  }

  /** @param {string} objectId */
  function selectObject(objectId) {
    playback.pause();
    update({ objectId, observationId: null, candidateId: null });   // the store subscriber loads the object
  }

  async function loadObject() {
    const { objectId } = getState();
    if (!objectId) return;
    statusEl.innerHTML = ""; viewer.setState({ kind: "loading", message: "Loading observations…" });
    candEl.innerHTML = `<h2 id="cand-h">Candidates</h2><div class="spinner" aria-label="Loading"></div>`;
    try {
      const [obj, obsPage, ex, candPage] = await Promise.all([
        api.getObject(objectId), api.listObservations(objectId), api.getObjectExtras(objectId),
        api.listCandidates(objectId).catch(e => /** @type {any} */ ({ items: [], coverageNote: errMsg(e), unavailable: true })),
      ]);
      if (!alive) return;
      object = obj; observations = obsPage.items; extras = ex; candidates = candPage.items;
      comparison.reset();
      update({ coordinates: obj.coordinates });
      logEvent("object_opened", objectId);
      /** @type {HTMLSelectElement} */ ($("#target-select")).value = objectId;

      if (!observations.length) {
        timeline.set([], 0); renderCandidates(); renderDetails();
        viewer.setMarkers([]);
        viewer.setState({ kind: "empty", message: obsPage.coverageNote ?? "No observations for this target." });
        return;
      }
      const st = getState();
      if (!observations.some(o => o.observationId === st.observationId)) update({ observationId: observations[0].observationId });
      if (st.candidateId && !candidates.some(c => c.candidateId === st.candidateId)) update({ candidateId: null });
      timeline.set(observations, activeIndex());
      preload(observations.map(o => o.observationId));
      renderCandidates(); renderDetails(); applyMode();
    } catch (e) {
      if (!alive) return;
      object = null; observations = []; candidates = [];
      timeline.set([], 0); renderCandidates(); renderDetails();
      viewer.setState({ kind: "failed", message: errMsg(e), ...(e instanceof ApiClientError && e.retryable ? { onRetry: loadObject } : {}) });
    }
  }

  // ---- frame display ----------------------------------------------------------
  async function renderFrame() {
    const id = getState().observationId, obs = observations[activeIndex()];
    if (!id || !obs || !isView()) return;
    const token = ++renderToken;
    timeline.setActive(activeIndex());
    hud(obs);
    // Only show the loading overlay if the frame takes noticeable time (cached frames resolve instantly).
    const slow = window.setTimeout(() => { if (token === renderToken && !playback.playing) viewer.setState({ kind: "loading", message: "Loading image…" }); }, 150);
    try {
      const frame = await loadFrame(id);
      clearTimeout(slow);
      if (!alive || token !== renderToken || !isView()) return;   // a newer frame was requested, or a comparison took over
      const st = frame.availability;
      if (st.status === "available" && frame.imageUrl && frame.width && frame.height) {
        await viewer.setImage({ url: frame.imageUrl, width: frame.width, height: frame.height });
        // Coming back from another tool: restore the zoom/pan the user left this object at.
        const vs = getState().viewState;
        if (!viewRestored && vs && vs.objectId === object?.objectId) viewer.setView(vs);
        viewRestored = true;
        logEvent("observation_viewed", id);
        syncMarkers();
      } else if (st.status === "processing") {
        emit("image_processing");
        viewer.setState({ kind: "processing", message: st.statusReason ?? "Image is still being prepared." , onRetry: () => { forgetFrame(id); renderFrame(); } });
      } else {
        emit("image_missing", { status: st.status });
        viewer.setState({ kind: st.status === "failed" ? "failed" : "unavailable", message: st.statusReason ?? "No image for this observation.",
          ...(st.status === "failed" ? { onRetry: () => { forgetFrame(id); renderFrame(); } } : {}) });
      }
    } catch (e) {
      clearTimeout(slow);
      if (token === renderToken) viewer.setState({ kind: "failed", message: errMsg(e), onRetry: () => { forgetFrame(id); renderFrame(); } });
    }
  }

  function hud(/** @type {any} */ obs) {
    const flags = obs.qualitySummary !== "good" && obs.qualitySummary !== "unknown" ? `<span class="badge badge-illustrative">quality: ${esc(obs.qualitySummary)}</span>` : "";
    viewer.setHud(`<span class="badge">${esc(shortDate(obs.observedAt))} · ${activeIndex() + 1} of ${observations.length}</span>${flags}${isMock() ? `<span class="badge badge-illustrative">illustrative</span>` : ""}`);
  }

  /** Candidate markers for the CURRENT observation (positions come from the backend `track`). */
  function syncMarkers() {
    if (!isView()) return;
    viewer.setMarkers(markersAt(activeIndex()));
    viewer.setMarkersVisible(!(guided?.hidingMarkers()));
  }
  /** @param {number} i observation index @returns {import("../components/viewer.js").Marker[]} */
  function markersAt(i) {
    const sel = getState().candidateId;
    if (i < 0 || guided?.hidingMarkers()) return [];
    return /** @type {any[]} */ (candidates.map(c => {
      const p = c.track[i]?.pixel; return p ? { id: c.candidateId, x: p.x, y: p.y, r: 20, label: c.label, selected: c.candidateId === sel } : null;
    }).filter(Boolean));
  }

  // ---- candidates panel ---------------------------------------------------------
  function renderCandidates() {
    const sel = getState().candidateId, c = candidates.find(x => x.candidateId === sel);
    candEl.innerHTML = `<h2 id="cand-h">Candidates (${candidates.length})</h2>
      ${candidates.length ? `<ul class="list" role="listbox" aria-labelledby="cand-h">${candidates.map(x => `
        <li role="presentation"><button class="list-item" role="option" aria-selected="${x.candidateId === sel}" data-id="${esc(x.candidateId)}">
          <span class="cand-main"><strong>${esc(x.label)}</strong><span class="muted fine">${esc(x.description)}</span></span>
          <span class="score" title="Ranking score, not a probability">${esc(x.score.toFixed(2))}</span></button></li>`).join("")}</ul>
        <p class="muted fine">Sample ranking scores. Not a probability or confidence.</p>`
        : `<p class="muted">${esc(candidateNote())}</p>`}
      ${c ? `<div class="evidence"><h3 class="label group">Why it is listed</h3>
        <ul>${c.evidence.map((/** @type {string} */ e) => `<li>${esc(e)}</li>`).join("")}</ul>
        <p class="muted fine">Score basis: ${esc(c.scoreBasis)}</p>
        ${c.warnings.map((/** @type {string} */ w) => `<p class="notice notice-warn fine">${esc(w)}</p>`).join("")}
        <p class="muted fine">A candidate is not a confirmed object.</p></div>` : ""}`;
  }
  const candidateNote = () => object ? "No candidates for this target." : "Choose a target to list candidates.";

  candEl.addEventListener("click", e => {
    const b = /** @type {HTMLElement} */ (e.target).closest("[data-id]"); if (!b) return;
    const id = b.getAttribute("data-id");
    update({ candidateId: getState().candidateId === id ? null : id });
  });

  // ---- details + derived measurement ----------------------------------------------
  function renderDetails() {
    renderObjectDetails(detailsEl, { object, extras, observation: observations[activeIndex()],
      candidate: candidates.find(c => c.candidateId === getState().candidateId), measurement, measurementState: measureState, measurementError: measureError });
    // One-line context in the top bar, so the details drawer can stay closed.
    const pm = extras?.properMotion;
    $("#ctx-summary").innerHTML = object
      ? `<strong>${esc(object.name)}</strong> <span class="muted">RA</span> ${esc(formatRa(object.coordinates.raDeg))} <span class="muted">Dec</span> ${esc(formatDec(object.coordinates.decDeg))}${pm ? ` <span class="muted">μ</span> ${esc(pm.value)} ${esc(pm.unit)}` : ""}`
      : `<span class="muted">No target selected</span>`;
    $("#cand-count").textContent = object ? `(${candidates.length})` : "";
  }

  let measureTimer = 0;
  function refreshMeasurement() {
    clearTimeout(measureTimer);
    const cand = candidates.find(c => c.candidateId === getState().candidateId), obs = observations[activeIndex()];
    if (!cand || !obs) { measurement = null; measureState = "idle"; renderDetails(); return; }
    if (playback.playing) { measureState = "playing"; renderDetails(); return; }
    measureState = "loading"; renderDetails();
    const token = ++measureToken;
    measureTimer = window.setTimeout(async () => {
      try {
        const p = cand.track[activeIndex()];
        const m = await api.createMeasurement({ kind: "aperture_flux", observationIds: [obs.observationId], region: { center: p.sky, radiusArcsec: 12 } });
        if (!alive || token !== measureToken) return;
        measurement = m; measureState = "idle"; logEvent("measurement_created", m.measurementId);
      } catch (e) { if (token !== measureToken) return; measurement = null; measureState = "error"; measureError = errMsg(e); }
      if (alive) renderDetails();
    }, 250);
  }

  // ---- store wiring ---------------------------------------------------------------
  const unsub = subscribe((_s, patch) => {
    if ("compareMode" in patch) applyMode();
    if ("panels" in patch) applyPanels();
    if ("observationId" in patch && object) { renderFrame(); renderDetails(); refreshMeasurement(); }
    if ("candidateId" in patch) { renderCandidates(); syncMarkers(); refreshMeasurement(); }
    if ("guided" in patch) syncMarkers();
    if ("objectId" in patch && patch.objectId && patch.objectId !== object?.objectId) loadObject();
  });

  // ---- target form -----------------------------------------------------------------
  /** @type {HTMLSelectElement} */ ($("#target-select")).addEventListener("change", e => selectObject(/** @type {HTMLSelectElement} */ (e.target).value));
  $("#target-form").addEventListener("submit", async e => {
    e.preventDefault();
    const q = /** @type {HTMLInputElement} */ ($("#target-q")).value.trim(); if (!q) return;
    statusEl.innerHTML = `<div class="spinner" aria-label="Searching"></div>`;
    try {
      const m = q.match(/^(-?\d+(?:\.\d+)?)[\s,]+(-?\d+(?:\.\d+)?)$/);
      const obj = m ? await api.resolveObject({ ra: Number(m[1]), dec: Number(m[2]) }) : await api.resolveObject({ q });
      statusEl.innerHTML = ""; selectObject(obj.objectId);
    } catch (err) { statusEl.innerHTML = `<p class="notice notice-error">${esc(errMsg(err))}</p>`; }
  });

  // ---- boot ----------------------------------------------------------------------------
  const samplesReady = loadSamples();
  if (getState().objectId) loadObject();
  else {
    viewer.setState({ kind: "empty", message: "Search for a target to begin." });
    // Open on the first sample so the workspace is never empty (mock mode only); the store subscriber loads it.
    samplesReady.then(list => { if (alive && !getState().objectId && isMock() && list[0]) update({ objectId: list[0].objectId }); });
  }

  applyPanels(); applyMode();
  return () => {
    // Remember zoom/pan for this object so returning from Spectrum or Notes doesn't start over.
    if (object && isView() && viewRestored) update({ viewState: { objectId: object.objectId, ...viewer.getView() } });
    if (beforeSideBySide) update({ panels: beforeSideBySide });
    setFocus(false); document.removeEventListener("keydown", onKey); mq.removeEventListener("change", onMq);
    alive = false; playback.destroy(); unsub(); guided?.destroy(); comparison.destroy(); viewer.destroy(); clearTimeout(hoverTimer); clearTimeout(measureTimer);
  };
}
