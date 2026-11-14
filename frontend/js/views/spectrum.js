// @ts-check
/**
 * Spectral explorer. Shows the SpectralData the backend returns: a band strip (count from
 * `expectedBandCount`, never hard-coded), the observation image behind the selected band,
 * and a collapsible flux-vs-wavelength chart. Gaps stay gaps (no interpolation) and error bars
 * are drawn ONLY where `fluxUncertainty` exists. Colours are display-only (false colour).
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, logEvent } from "../core/store.js";
import { esc, shortDate } from "../core/dom.js";
import { loadFrame } from "../core/frames.js";
import { createViewer } from "../components/viewer.js";

const errMsg = (/** @type {unknown} */ e) => (e instanceof ApiClientError ? e.message : "Something unexpected went wrong.");

// Muted cosmic gradient (matches --spectrum-gradient in tokens.css; placeholder stops).
const STOPS = ["#5b6fb0", "#5a8fc0", "#5fb0b8", "#8fc08a", "#d6b56a", "#e08f60", "#c8605a"];
/** Display colour for position t in [0,1] along the gradient. */
function gradientColor(/** @type {number} */ t) {
  const x = Math.min(0.9999, Math.max(0, t)) * (STOPS.length - 1), i = Math.floor(x), f = x - i;
  const a = STOPS[i], b = STOPS[i + 1];
  const mix = (/** @type {number} */ k) => Math.round(parseInt(a.slice(k, k + 2), 16) * (1 - f) + parseInt(b.slice(k, k + 2), 16) * f);
  return `rgb(${mix(1)},${mix(3)},${mix(5)})`;
}

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
          <label class="inline"><input type="checkbox" id="cmp-toggle"> <span>Compare a second band</span></label></div>
        <div class="strip" id="strip" role="listbox" aria-label="Spectral bands, left to right from shorter to longer wavelength"></div>
        <p class="muted fine">False-colour display of band position. ${isMock() ? "Spectrum is a synthetic illustration, not a measurement." : ""} Hatched bands have no usable data.</p>
      </div>
      <details class="panel chart-panel" id="chart-details" open>
        <summary><h2 style="display:inline">Intensity vs wavelength</h2></summary>
        <div id="chart"></div>
      </details>
    </div>
  </div>`;

  const $ = (/** @type {string} */ s) => /** @type {HTMLElement} */ (root.querySelector(s));
  let alive = true, token = 0, second = /** @type {number|null} */ (null), comparing = false;
  /** @type {any} */ let spec = null;
  /** @type {any[]} */ let observations = [];

  const viewer = createViewer($("#sp-viewer"), { label: "Image behind the selected band" });

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
      observations = (await api.listObservations(objectId)).items;
      if (!alive) return;
      spec = s;
    } catch (e) { $("#sp-empty").innerHTML = `<p class="notice notice-error">${esc(errMsg(e))}</p>`; return; }

    if (spec.availability.status !== "available" || !spec.points.length) {
      $("#sp-empty").innerHTML = `<p class="notice notice-warn">${esc(spec.availability.statusReason ?? "No spectrum is available for this target.")}</p>`;
      return;
    }
    $("#sp-empty").innerHTML = ""; /** @type {HTMLElement} */ ($(".sp-body")).hidden = false;
    logEvent("spectrum_viewed", spec.spectrumId);
    if (!spec.points.some((/** @type {any} */ p) => p.bandIndex === getState().bandIndex)) {
      update({ bandIndex: spec.points.find((/** @type {any} */ p) => p.available)?.bandIndex ?? spec.points[0].bandIndex });
    }
    renderStrip(); renderChart(); renderSelection();
  }

  const pointOf = (/** @type {number|null} */ band) => spec.points.find((/** @type {any} */ p) => p.bandIndex === band);
  const n = () => spec.expectedBandCount ?? spec.points.length;   // never hard-coded

  // ---- band strip --------------------------------------------------------------
  function renderStrip() {
    $("#band-count").textContent = `(${n()} bands${spec.points.length !== n() ? `, ${spec.points.length} listed` : ""})`;
    const sel = getState().bandIndex;
    $("#strip").innerHTML = spec.points.map((/** @type {any} */ p, /** @type {number} */ i) => {
      const color = gradientColor(i / Math.max(1, spec.points.length - 1));
      const cls = ["band", p.available ? "" : "band-gap", p.bandIndex === sel ? "is-sel" : "", p.bandIndex === second ? "is-sel2" : ""].join(" ");
      return `<button class="${cls}" role="option" aria-selected="${p.bandIndex === sel}" tabindex="${p.bandIndex === sel ? 0 : -1}" data-band="${p.bandIndex}"
        style="--c:${color}" aria-label="Band ${p.bandIndex}, ${p.wavelengthUm.toFixed(2)} micrometres${p.available ? "" : ", no data"}" title="Band ${p.bandIndex} · ${p.wavelengthUm.toFixed(2)} µm${p.available ? "" : " · no data"}"></button>`;
    }).join("");
  }

  function selectBand(/** @type {number} */ band) {
    if (comparing && band !== getState().bandIndex) second = band; else { update({ bandIndex: band }); }
    renderStrip(); renderChart(); renderSelection();
  }
  $("#strip").addEventListener("click", e => { const b = /** @type {HTMLElement} */ (e.target).closest("[data-band]"); if (b) selectBand(Number(b.getAttribute("data-band"))); });
  $("#strip").addEventListener("keydown", e => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0; if (!dir) return;
    e.preventDefault();
    const i = spec.points.findIndex((/** @type {any} */ p) => p.bandIndex === getState().bandIndex), next = spec.points[Math.min(spec.points.length - 1, Math.max(0, i + dir))];
    selectBand(next.bandIndex);
    queueMicrotask(() => /** @type {HTMLElement|null} */ ($(`[data-band="${next.bandIndex}"]`))?.focus());
  });
  $("#cmp-toggle").addEventListener("change", e => { comparing = /** @type {HTMLInputElement} */ (e.target).checked; if (!comparing) second = null; renderStrip(); renderChart(); renderSelection(); });

  // ---- selection: image + readout ------------------------------------------------
  async function renderSelection() {
    const p = pointOf(getState().bandIndex); if (!p) return;
    const t = ++token;
    renderInfo(p);
    if (!p.available) { viewer.setState({ kind: "unavailable", message: p.unavailableReason ?? "No data for this band." }); viewer.setHud(""); return; }
    const obs = observations.find(o => o.observationId === p.observationId);
    viewer.setHud(`<span class="badge">Band ${p.bandIndex} · ${esc(p.wavelengthUm.toFixed(2))} µm</span><span class="badge">${esc(shortDate(p.observedAt))}</span>${isMock() ? `<span class="badge badge-illustrative">illustrative · false colour</span>` : ""}`);
    if (!obs) { viewer.setState({ kind: "unavailable", message: "The observation behind this band is not listed." }); return; }
    viewer.setState({ kind: "loading", message: "Loading image…" });
    try {
      const f = await loadFrame(obs.observationId);
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
      ${p.qualityFlags.map((/** @type {any} */ f) => `<p class="notice fine">${esc(f.description)}</p>`).join("")}
      ${p.available ? "" : `<p class="notice notice-warn fine">${esc(p.unavailableReason ?? "No usable observation.")}</p>`}`;
  }

  function renderInfo(/** @type {any} */ p) {
    const p2 = second !== null ? pointOf(second) : null;
    $("#sp-info").innerHTML = `<h2>Selected band</h2>${readout(p)}
      ${p2 ? `<h3 class="label group">Compared band</h3>${readout(p2)}` : comparing ? `<p class="muted fine">Pick a second band on the strip or chart.</p>` : ""}
      <h3 class="label group">About this spectrum</h3>
      <p class="fine muted">${esc(spec.extractionMethod)}. Aperture ${spec.apertureRadiusArcsec !== undefined ? esc(spec.apertureRadiusArcsec) + " arcsec" : "n/a"}.</p>
      ${spec.warnings.map((/** @type {string} */ w) => `<p class="notice notice-warn fine">${esc(w)}</p>`).join("")}`;
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
