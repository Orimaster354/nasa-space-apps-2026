// @ts-check
/**
 * Spectral explorer. Shows the SpectralData the backend returns: a band selector that places
 * every band at its real wavelength (edges from the API; spacing never assumed uniform), the observation image behind the selected band,
 * and a collapsible flux-vs-wavelength chart. Gaps stay gaps (no interpolation) and error bars
 * are drawn ONLY where `fluxUncertainty` exists. Colours are display-only (false colour).
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, logEvent } from "../core/store.js";
import { esc, shortDate } from "../core/dom.js";
import { loadFrame } from "../core/frames.js";
import { createViewer } from "../components/viewer.js";
import { createBandSelector, STOPS } from "../components/bandSelector.js";
import { emit } from "../core/events.js";

const errMsg = (/** @type {unknown} */ e) => (e instanceof ApiClientError ? e.message : "Something unexpected went wrong.");

/** @param {HTMLElement} root @returns {() => void} */
export function mount(root) {
  root.innerHTML = `<div class="spectrum">
    <div id="sp-empty"></div>
    <div class="sp-body" hidden>
      <div class="sp-top">
        <div class="viewer-wrap sp-viewer"><div id="sp-viewer"></div></div>
        <aside class="panel" id="sp-info"></aside>
      </div>
      <div class="panel">
        <div class="sp-strip-head"><h2>Spectral bands <span class="muted fine" id="band-count"></span></h2>
          <label class="inline fine"><input type="checkbox" id="cmp-toggle"> <span>Compare a second band</span></label></div>
        <div id="bands"></div>
        ${isMock() ? `<p class="muted fine">Band layout and spectrum are illustrative, not SPHEREx measurements.</p>` : ""}
      </div>
      <details class="panel chart-panel" id="chart-details" open>
        <summary><h2 style="display:inline">Intensity vs wavelength</h2></summary>
        <div id="chart"></div>
      </details>
      <details class="panel" id="about-details">
        <summary><h2 style="display:inline">About this spectrum and its limits</h2> <span class="badge" id="about-count"></span></summary>
        <div id="about"></div>
      </details>
      ${isMock() ? "" : `<details class="panel" id="epoch-details">
        <summary><h2 style="display:inline">Compare earlier and later observations</h2></summary>
        <div id="epoch"><p class="muted fine">Opens a band-by-band comparison of this object's spectrum before and after a date, using only observations stored on the server and only bands where the object fell at matching wavelengths.</p></div>
      </details>`}
    </div>
  </div>`;

  const $ = (/** @type {string} */ s) => /** @type {HTMLElement} */ (root.querySelector(s));
  let alive = true, token = 0, second = /** @type {number|null} */ (null), comparing = false;
  /** @type {any} */ let spec = null;
  /** @type {any[]} */ let observations = [];

  // ---- earlier vs later comparison (frontend-only operation, real backend only) ------------------------
  let epochLoaded = false;
  async function loadEpoch() {
    const box = root.querySelector("#epoch"), id = getState().objectId;
    if (!box || !id || epochLoaded) return;
    epochLoaded = true;
    box.innerHTML = `<div class="stage-msg"><div class="spinner" aria-hidden="true"></div><p>Comparing…</p></div>`;
    try {
      const j = await api.getSpectrumComparison(id);
      if (!alive) return;
      if (j.availability.status !== "available") { box.innerHTML = `<p class="notice notice-warn">${esc(j.availability.statusReason)}</p>${(j.warnings ?? []).map((/** @type {string} */ w) => `<p class="fine muted">${esc(w)}</p>`).join("")}`; return; }
      const rows = j.bands.filter((/** @type {any} */ b) => b.comparable).map((/** @type {any} */ b) => `<tr>
        <td>${esc(b.bandIndex)}</td><td>${esc(b.wavelengthUm.toFixed(3))} µm</td>
        <td>${esc(b.early.flux)} <span class="muted">± ${esc(b.early.fluxUncertainty)}</span><br><span class="muted fine">${esc(shortDate(b.early.observedAt))}</span></td>
        <td>${esc(b.late.flux)} <span class="muted">± ${esc(b.late.fluxUncertainty)}</span><br><span class="muted fine">${esc(shortDate(b.late.observedAt))}</span></td>
        <td>${esc(b.difference)} <span class="muted">± ${esc(b.differenceUncertainty)}</span></td><td>${esc(b.significanceSigma)}</td></tr>`).join("");
      emit("epoch_compared");
      const skipped = j.bands.filter((/** @type {any} */ b) => !b.comparable).length;
      box.innerHTML = `<p><strong>${esc(j.summary.text)}</strong></p>
        <table class="data-table"><thead><tr><th>Band</th><th>Wavelength</th><th>Earlier (${esc(spec.fluxUnit)})</th><th>Later</th><th>Later minus earlier</th><th>Times its uncertainty</th></tr></thead><tbody>${rows}</tbody></table>
        ${skipped ? `<p class="fine muted">${skipped} other band${skipped > 1 ? "s were" : " was"} not compared: only one stretch of time has a measurement there, or the object fell at a different wavelength in the two.</p>` : ""}
        ${(j.warnings ?? []).map((/** @type {string} */ w) => `<p class="notice fine">${esc(w)}</p>`).join("")}`;
    } catch (e) { epochLoaded = false; box.innerHTML = `<p class="notice notice-error">${esc(errMsg(e))}</p>`; }
  }
  root.querySelector("#epoch-details")?.addEventListener("toggle", e => { if (/** @type {HTMLDetailsElement} */ (e.target).open) loadEpoch(); });

  const viewer = createViewer($("#sp-viewer"), { label: "Image behind the selected band" });
  const selector = createBandSelector($("#bands"), { onSelect: band => selectBand(band), label: "Spectral band, shorter to longer wavelength" });

  async function boot() {
    const { objectId } = getState();
    if (!objectId) { $("#sp-empty").innerHTML = `<p class="notice">Pick a target in <a href="#/investigate">Investigate</a> first. Your selection is shared across tools.</p>`; return; }
    $("#sp-empty").innerHTML = `<div class="stage-msg"><div class="spinner" aria-hidden="true"></div><p>Loading spectrum…</p></div>`;
    try {
      let s = await api.getSpectrum(objectId), tries = 6;
      while (s.availability.status === "processing" && tries-- > 0) {          // contract: poll the same GET
        await new Promise(r => setTimeout(r, (s.availability.retryAfterSeconds ?? 2) * 1000));
        s = await api.getSpectrum(objectId);
      }
      if (!alive) return;
      spec = s;
    } catch (e) { $("#sp-empty").innerHTML = `<p class="notice notice-error">${esc(errMsg(e))}</p>`; return; }

    if (spec.availability.status !== "available" || !spec.points.length) {
      $("#sp-empty").innerHTML = `<p class="notice notice-warn">${esc(spec.availability.statusReason ?? "No spectrum is available for this target.")}</p>`;
      return;
    }
    $("#sp-empty").innerHTML = ""; /** @type {HTMLElement} */ ($(".sp-body")).hidden = false; renderAbout();
    logEvent("spectrum_viewed", spec.spectrumId);
    if (!spec.points.some((/** @type {any} */ p) => p.bandIndex === getState().bandIndex)) {
      update({ bandIndex: spec.points.find((/** @type {any} */ p) => p.available)?.bandIndex ?? spec.points[0].bandIndex });
    }
    renderStrip(); renderChart(); renderSelection();
  }

  const pointOf = (/** @type {number|null} */ band) => spec.points.find((/** @type {any} */ p) => p.bandIndex === band);
  const n = () => spec.expectedBandCount ?? spec.points.length;   // never hard-coded

  // ---- band selector -----------------------------------------------------------------
  function renderStrip() {
    $("#band-count").textContent = `(${n()} bands${spec.points.length !== n() ? `, ${spec.points.length} listed` : ""})`;
    selector.set(spec.points, /** @type {number} */ (getState().bandIndex), second);
  }

  function selectBand(/** @type {number} */ band) {
    if (pointOf(band)?.available === false) emit("gap_band", { band });
    if (comparing && band !== getState().bandIndex) second = band; else { update({ bandIndex: band }); }
    renderStrip(); renderChart(); renderSelection();
  }
  $("#cmp-toggle").addEventListener("change", e => { comparing = /** @type {HTMLInputElement} */ (e.target).checked; if (!comparing) second = null; renderStrip(); renderChart(); renderSelection(); });

  // ---- selection: image + readout ------------------------------------------------
  async function renderSelection() {
    const p = pointOf(getState().bandIndex); if (!p) return;
    const t = ++token;
    renderInfo(p);
    if (!p.available) { viewer.setState({ kind: "unavailable", message: p.unavailableReason ?? "No data for this band." }); viewer.setHud(""); return; }
    viewer.setHud(`<span class="badge">Band ${p.bandIndex} · ${esc(p.wavelengthUm.toFixed(2))} <span class="unit">µm</span></span><span class="badge">${esc(shortDate(p.observedAt))}</span>${isMock() ? `<span class="badge badge-illustrative">illustrative · false colour</span>` : ""}`);
    if (!p.observationId) { viewer.setState({ kind: "unavailable", message: "No observation is recorded for this band." }); return; }
    viewer.setState({ kind: "loading", message: "Loading image…" });
    try {
      const f = await loadFrame(p.observationId);
      if (!alive || t !== token) return;
      if (f.availability.status === "available" && f.imageUrl && f.width && f.height) await viewer.setImage({ url: f.imageUrl, width: f.width, height: f.height });
      else viewer.setState({ kind: f.availability.status === "failed" ? "failed" : "unavailable", message: f.availability.statusReason ?? "Image not available." });
    } catch (e) { if (t === token) viewer.setState({ kind: "failed", message: errMsg(e) }); }
  }

  /** @param {any} p */
  function readout(p) {
    if (!p) return "";
    const flux = p.available ? `${esc(p.flux)}${p.fluxUncertainty !== undefined ? ` ± ${esc(p.fluxUncertainty)}` : ""} <span class="muted">${esc(spec.fluxUnit)}</span>` : `<span class="muted">no data</span>`;
    return `<dl class="kv">
      <dt>Band</dt><dd>${esc(p.bandIndex)}</dd>
      <dt>Wavelength</dt><dd>${esc(p.wavelengthUm.toFixed(3))} µm${p.wavelengthMinUm !== undefined ? ` <span class="muted">(${esc(p.wavelengthMinUm.toFixed(2))}–${esc(p.wavelengthMaxUm.toFixed(2))})</span>` : ""}</dd>
      <dt>Flux</dt><dd>${flux}</dd>
      <dt>Uncertainty</dt><dd>${p.available ? (p.fluxUncertainty !== undefined ? "provided" : "not available") : "n/a"}</dd>
      <dt>Observed</dt><dd>${esc(shortDate(p.observedAt))}</dd></dl>
      ${p.qualityFlags.length ? `<details class="flags fine"><summary>${p.qualityFlags.length} quality note${p.qualityFlags.length > 1 ? "s" : ""}</summary>${p.qualityFlags.map((/** @type {any} */ f) => `<p class="notice fine">${esc(f.description)}</p>`).join("")}</details>` : ""}
      ${p.available ? "" : `<p class="notice notice-warn fine">${esc(p.unavailableReason ?? "No usable observation.")}</p>`}`;
  }

  function renderInfo(/** @type {any} */ p) {
    const p2 = second !== null ? pointOf(second) : null;
    $("#sp-info").innerHTML = `<h2>Selected band</h2>${readout(p)}
      ${p2 ? `<h3 class="label group">Compared band</h3>${readout(p2)}` : comparing ? `<p class="muted fine">Pick a second band on the selector or the chart.</p>` : ""}
      <p class="fine muted"><button type="button" class="linklike" id="open-about">${spec.warnings.length} notes on how this spectrum was made and its limits</button> are below the chart.</p>`;
    root.querySelector("#open-about")?.addEventListener("click", () => { const d = /** @type {HTMLDetailsElement} */ ($("#about-details")); d.open = true; d.scrollIntoView({ behavior: "smooth", block: "start" }); });
  }

  /** The long explanations live in a collapsible section under the chart, so they never stretch the side column. */
  function renderAbout() {
    $("#about-count").textContent = `${spec.warnings.length} notes`;
    $("#about").innerHTML = `<p class="fine muted">${esc(spec.extractionMethod)}. Aperture ${spec.apertureRadiusArcsec !== undefined ? esc(spec.apertureRadiusArcsec) + " arcsec" : "n/a"}.</p>
      <div class="about-notes">${spec.warnings.map((/** @type {string} */ w) => `<p class="notice notice-warn fine">${esc(w)}</p>`).join("")}</div>`;
  }

  // ---- chart ---------------------------------------------------------------------
  function renderChart() {
    const W = 800, H = 280, m = { l: 62, r: 16, t: 14, b: 44 };
    const pts = spec.points, ok = pts.filter((/** @type {any} */ p) => p.available);
    if (!ok.length) { $("#chart").innerHTML = `<p class="muted">No flux values to plot.</p>`; return; }
    const x0 = pts[0].wavelengthUm, x1 = pts[pts.length - 1].wavelengthUm;
    const lo = Math.min(...ok.map((/** @type {any} */ p) => p.flux - (p.fluxUncertainty ?? 0))), hi = Math.max(...ok.map((/** @type {any} */ p) => p.flux + (p.fluxUncertainty ?? 0)));
    const pad = (hi - lo) * 0.08 || 1, y0 = lo - pad, y1 = hi + pad;
    const X = (/** @type {number} */ v) => m.l + ((v - x0) / (x1 - x0)) * (W - m.l - m.r);
    const Y = (/** @type {number} */ v) => H - m.b - ((v - y0) / (y1 - y0)) * (H - m.t - m.b);
    // Line segments only between neighbouring available points: gaps are not bridged.
    let path = "", pen = false;
    for (const p of pts) { if (!p.available) { pen = false; continue; } path += `${pen ? "L" : "M"}${X(p.wavelengthUm).toFixed(1)},${Y(p.flux).toFixed(1)} `; pen = true; }
    const xt = [1, 2, 3, 4, 5].filter(v => v >= x0 && v <= x1), yt = [0, 1, 2, 3, 4].map(i => y0 + ((y1 - y0) * i) / 4);
    const sel = getState().bandIndex;
    const marker = (/** @type {number|null} */ band, /** @type {string} */ cls) => { const p = pointOf(band); return p ? `<line class="${cls}" x1="${X(p.wavelengthUm)}" x2="${X(p.wavelengthUm)}" y1="${m.t}" y2="${H - m.b}"></line>` : ""; };
    $("#chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Flux versus wavelength for ${spec.points.length} spectral bands. Selected band ${sel}.">
      <rect x="${m.l}" y="${H - m.b + 6}" width="${W - m.l - m.r}" height="6" fill="url(#g)"></rect>
      <defs><linearGradient id="g">${STOPS.map((c, i) => `<stop offset="${(i / (STOPS.length - 1)) * 100}%" stop-color="${c}"></stop>`).join("")}</linearGradient></defs>
      ${yt.map(v => `<line class="grid" x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"></line><text class="tick" x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${v.toFixed(0)}</text>`).join("")}
      ${xt.map(v => `<text class="tick" x="${X(v)}" y="${H - m.b + 28}" text-anchor="middle">${v}</text>`).join("")}
      <text class="axis" x="${(W + m.l) / 2}" y="${H - 6}" text-anchor="middle">Wavelength (µm)</text>
      <text class="axis" transform="translate(14 ${(H - m.b + m.t) / 2}) rotate(-90)" text-anchor="middle">Flux (${esc(spec.fluxUnit)})</text>
      ${pts.filter((/** @type {any} */ p) => !p.available).map((/** @type {any} */ p) => `<line class="gapmark" x1="${X(p.wavelengthUm)}" x2="${X(p.wavelengthUm)}" y1="${H - m.b - 10}" y2="${H - m.b}"><title>Band ${p.bandIndex}: no data</title></line>`).join("")}
      <path class="curve" d="${path}"></path>
      ${ok.map((/** @type {any} */ p) => `${p.fluxUncertainty !== undefined ? `<line class="err" x1="${X(p.wavelengthUm)}" x2="${X(p.wavelengthUm)}" y1="${Y(p.flux - p.fluxUncertainty)}" y2="${Y(p.flux + p.fluxUncertainty)}"></line>` : ""}
        <circle class="pt" cx="${X(p.wavelengthUm)}" cy="${Y(p.flux)}" r="2.4"><title>Band ${p.bandIndex} · ${p.wavelengthUm.toFixed(2)} µm · ${p.flux}${p.fluxUncertainty !== undefined ? " ± " + p.fluxUncertainty : " (no uncertainty)"}</title></circle>`).join("")}
      ${marker(second, "mark2")}${marker(sel, "mark")}
      <rect class="hit" x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent"></rect>
    </svg>
    <p class="muted fine">Error bars appear only where an uncertainty was provided. ${isMock() ? "Illustrative units and values." : ""} Click the chart to pick the nearest band.</p>`;
    $("#chart").querySelector(".hit")?.addEventListener("click", e => {
      const svg = /** @type {SVGSVGElement} */ ($("#chart").querySelector("svg")), r = svg.getBoundingClientRect();
      const wl = x0 + ((((/** @type {MouseEvent} */ (e)).clientX - r.left) / r.width) * W - m.l) / (W - m.l - m.r) * (x1 - x0);
      let best = pts[0]; for (const p of pts) if (Math.abs(p.wavelengthUm - wl) < Math.abs(best.wavelengthUm - wl)) best = p;
      selectBand(best.bandIndex);
    });
  }

  boot();
  return () => { alive = false; token++; viewer.destroy(); };
}
