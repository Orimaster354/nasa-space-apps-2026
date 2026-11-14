// @ts-check
/**
 * Investigate: the main workspace (candidates | viewer + timeline | object details).
 * Viewer and timeline are both driven by ONE value, `store.observationId`, so they cannot
 * disagree. All data arrives through the API client; nothing is fetched or computed here.
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

const errMsg = (/** @type {unknown} */ e) => (e instanceof ApiClientError ? e.message : "Something unexpected went wrong.");

/** @param {HTMLElement} root @returns {() => void} unmount */
export function mount(root) {
  root.innerHTML = `
    <div class="workspace">
      <aside class="col col-left">
        <section class="panel" id="cand-panel" aria-labelledby="cand-h"></section>
        <section id="guided-panel"></section>
      </aside>
      <section class="stage">
        <form class="target-bar" id="target-form" role="search">
          <label class="sr-only" for="target-select">Sample target</label>
          <select class="select" id="target-select"></select>
          <label class="sr-only" for="target-q">Search by name or RA Dec</label>
          <input class="input" id="target-q" placeholder="Name, or RA Dec in degrees (e.g. 269.45 4.69)" autocomplete="off">
          <button class="btn" type="submit">Go</button>
        </form>
        <div id="target-status" role="status"></div>
        <div class="viewer-wrap"><div id="viewer"></div></div>
        <div class="panel timeline-panel"><div id="timeline"></div></div>
      </section>
      <aside class="col col-right"><section class="panel" id="details"></section></aside>
    </div>`;

  const $ = (/** @type {string} */ id) => /** @type {HTMLElement} */ (root.querySelector(id));
  const statusEl = $("#target-status"), candEl = $("#cand-panel"), detailsEl = $("#details");

  // ---- local view state (data only; selection state lives in the store) -------
  /** @type {any} */ let object = null, extras = {};
  /** @type {any[]} */ let observations = [], candidates = [];
  let alive = true, renderToken = 0, measureToken = 0, hoverTimer = 0;
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
    onToggle: () => playback.toggle(),
    onStop: () => playback.stop(),
    onStep: d => { playback.pause(); playback.step(d); },
    onSpeed: s => playback.setSpeed(s),
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
      renderCandidates(); renderDetails(); await renderFrame();
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
    if (!id || !obs) return;
    const token = ++renderToken;
    timeline.setActive(activeIndex());
    hud(obs);
    // Only show the loading overlay if the frame takes noticeable time (cached frames resolve instantly).
    const slow = window.setTimeout(() => { if (token === renderToken && !playback.playing) viewer.setState({ kind: "loading", message: "Loading image…" }); }, 150);
    try {
      const frame = await loadFrame(id);
      clearTimeout(slow);
      if (!alive || token !== renderToken) return;           // a newer frame was requested
      const st = frame.availability;
      if (st.status === "available" && frame.imageUrl && frame.width && frame.height) {
        await viewer.setImage({ url: frame.imageUrl, width: frame.width, height: frame.height });
        logEvent("observation_viewed", id);
        syncMarkers();
      } else if (st.status === "processing") {
        viewer.setState({ kind: "processing", message: st.statusReason ?? "Image is still being prepared." , onRetry: () => { forgetFrame(id); renderFrame(); } });
      } else {
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
    const i = activeIndex(), sel = getState().candidateId;
    viewer.setMarkers(candidates.map(c => {
      const p = c.track[i]?.pixel; return p ? { id: c.candidateId, x: p.x, y: p.y, r: 20, label: c.label, selected: c.candidateId === sel } : null;
    }).filter(Boolean));
    viewer.setMarkersVisible(!(guided?.hidingMarkers()));
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

  return () => { alive = false; playback.destroy(); unsub(); guided?.destroy(); viewer.destroy(); clearTimeout(hoverTimer); clearTimeout(measureTimer); };
}
