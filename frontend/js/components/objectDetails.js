// @ts-check
/**
 * Object details panel. Keeps three kinds of information visibly separate (pipeline doc §1):
 *   1. Catalogue: provided by the archive/resolver.
 *   2. Derived by this app: measurements computed by the backend, with uncertainty and quality.
 *   3. Observation: metadata for the observation currently shown.
 */

import { esc, formatRa, formatDec, shortDate } from "../core/dom.js";

/**
 * @param {HTMLElement} root
 * @param {{ object?: any, extras?: any, observation?: any, candidate?: any,
 *           measurement?: any, measurementState?: "idle"|"loading"|"error"|"playing", measurementError?: string }} d
 */
export function renderObjectDetails(root, d) {
  const o = d.object, ex = d.extras ?? {}, ob = d.observation;
  if (!o) { root.innerHTML = `<h2>Object details</h2><p class="muted">Choose a target to see its details.</p>`; return; }

  const pm = ex.properMotion;
  const range = ob?.wavelengthRangeUm;
  root.innerHTML = `
    <h2>Object details</h2>
    <h3 class="label group">Catalogue</h3>
    <dl class="kv">
      <dt>Target</dt><dd>${esc(o.name)}</dd>
      <dt>RA</dt><dd>${esc(formatRa(o.coordinates.raDeg))}</dd>
      <dt>Dec</dt><dd>${esc(formatDec(o.coordinates.decDeg))}</dd>
      <dt>Type</dt><dd>${esc(ex.classification ?? o.objectType ?? "not provided")}</dd>
      <dt>Proper motion</dt><dd>${pm ? `${esc(pm.value)} ${esc(pm.unit)}` : "not provided"}</dd>
    </dl>
    <p class="muted fine">${esc(pm?.source ?? o.resolver.name)}</p>

    <h3 class="label group">Derived by this app</h3>
    ${derivedBlock(d)}

    <h3 class="label group">Observation</h3>
    ${ob ? `<dl class="kv">
      <dt>Date (UTC)</dt><dd>${esc(shortDate(ob.observedAt))}</dd>
      <dt>Release</dt><dd>${esc(ob.dataRelease ?? "not provided")}</dd>
      <dt>Detector</dt><dd>${esc(ob.detector ?? "not provided")}</dd>
      <dt>Wavelength</dt><dd>${range ? `${esc(range.min)}–${esc(range.max)} µm` : "not provided"}</dd>
      <dt>Quality</dt><dd><span class="q q-${esc(ob.qualitySummary)}">${esc(ob.qualitySummary)}</span></dd>
    </dl>
    ${ob.qualityFlags.length ? `<ul class="flags">${ob.qualityFlags.map((/** @type {any} */ f) => `<li class="flag flag-${esc(f.severity)}"><strong>${esc(f.code)}</strong> ${esc(f.description)}</li>`).join("")}</ul>` : `<p class="muted fine">No quality flags.</p>`}`
    : `<p class="muted">No observation selected.</p>`}`;
}

/** Derived measurement block: value ± uncertainty with unit, basis and flags; never a bare number. */
function derivedBlock(/** @type {any} */ d) {
  if (!d.candidate) return `<p class="muted fine">Select a candidate to request a measurement of its region on this date.</p>`;
  if (d.measurementState === "playing") return `<p class="muted fine">Measurement paused while playing.</p>`;
  if (d.measurementState === "loading") return `<div class="spinner" aria-label="Measuring"></div>`;
  if (d.measurementState === "error") return `<p class="notice notice-error">${esc(d.measurementError ?? "Could not get a measurement.")}</p>`;
  const m = d.measurement;
  if (!m) return `<p class="muted fine">No measurement yet.</p>`;
  return `<dl class="kv">
      <dt>Brightness</dt><dd>${esc(m.value)} ± ${m.uncertainty !== undefined ? esc(m.uncertainty) : "n/a"}</dd>
      <dt>Unit</dt><dd>${esc(m.unit)}</dd>
    </dl>
    <p class="muted fine">${esc(m.method)}${m.uncertaintyBasis ? ` · uncertainty: ${esc(m.uncertaintyBasis)}` : ""}</p>
    ${m.uncertainty === undefined ? `<p class="muted fine">No uncertainty available.</p>` : ""}
    ${m.warnings.map((/** @type {string} */ w) => `<p class="notice notice-warn fine">${esc(w)}</p>`).join("")}`;
}
