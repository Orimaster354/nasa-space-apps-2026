// @ts-check
/**
 * Mock backend. Returns ONLY contract-shaped data (docs/FRONTEND_BACKEND_CONTRACT.md §5) plus,
 * clearly separated at the bottom, PROPOSED operations that are not in contract 0.1.0.
 * Data is ILLUSTRATIVE and must never be shown as real NASA data (contract §9): views show
 * the "illustrative" badge whenever CONFIG.useMock is true.
 *
 * Replacing this module with real HTTP calls is the backend integration step.
 */

import { CONFIG } from "../../config.js";
import { ApiClientError } from "../types.js";
import { loadFixture, delay, hash } from "./fixtures.js";
import { renderObservation, renderDifference, candidatePosition } from "./imagery.js";
export { sendChatMessage, getChatSuggestions } from "./chat.js";
export { listInvestigations, createInvestigation, getInvestigation, updateInvestigation, deleteInvestigation } from "./investigations.js";

/** @typedef {import("../types.js").AstronomicalObject} AstronomicalObject */
/** @typedef {import("../types.js").Observation} Observation */

const PIXEL_SCALE = 6.2; // arcsec/pixel, ILLUSTRATIVE
const imageRequests = new Map();   // observationId -> number of requests (drives the "processing" demo)
const comparisons = new Map();

/** #18 */
export async function health() { await delay(); return { status: "ok", contractVersion: CONFIG.contractVersion }; }

/** #1 */
export async function searchObjects(/** @type {string} */ q, /** @type {{limit?: number}} */ opts = {}) {
  await delay();
  if (!q || !q.trim()) throw new ApiClientError("Enter a name or coordinates to search.", { code: "INVALID_REQUEST" });
  const needle = q.trim().toLowerCase();
  /** @type {AstronomicalObject[]} */
  const all = await loadFixture("objects.json");
  const hits = all.filter(o => [o.name, ...o.aliases].some(n => n.toLowerCase().includes(needle)));
  const limit = Math.min(opts.limit ?? 10, 50);
  return { items: hits.slice(0, limit), total: hits.length, limit, offset: 0,
           ...(hits.length ? {} : { coverageNote: "No illustrative sample matches this name." }) };
}

/** #2 */
export async function resolveObject(/** @type {{q?: string, ra?: number, dec?: number}} */ params) {
  await delay();
  /** @type {AstronomicalObject[]} */
  const all = await loadFixture("objects.json");
  if (params.q) {
    const page = await searchObjects(params.q, { limit: 1 });
    if (page.items[0]) return page.items[0];
  } else if (Number.isFinite(params.ra) && Number.isFinite(params.dec)) {
    const ra = /** @type {number} */ (params.ra), dec = /** @type {number} */ (params.dec);
    if (ra < 0 || ra >= 360 || dec < -90 || dec > 90)
      throw new ApiClientError("RA must be in [0, 360) and Dec in [-90, 90] degrees.", { code: "INVALID_REQUEST" });
    // Mock-only small-angle match; the real backend does the science.
    const hit = all.find(o => Math.hypot((o.coordinates.raDeg - ra) * Math.cos(dec * Math.PI / 180), o.coordinates.decDeg - dec) < 0.5);
    if (hit) return hit;
  } else {
    throw new ApiClientError("Provide a name, or both RA and Dec.", { code: "INVALID_REQUEST" });
  }
  throw new ApiClientError("No illustrative sample at that position.", { code: "NOT_FOUND" });
}

/** #3 */
export async function getObject(/** @type {string} */ objectId) {
  await delay();
  const obj = (await loadFixture("objects.json")).find((/** @type {any} */ o) => o.objectId === objectId);
  if (!obj) throw new ApiClientError("Object not found.", { code: "NOT_FOUND" });
  return obj;
}

/** Helper for the sample picker (not a contract operation: lists every sample object). */
export async function listSampleObjects() {
  await delay(0.4);
  return /** @type {AstronomicalObject[]} */ (await loadFixture("objects.json"));
}

/** #4 */
export async function listObservations(/** @type {string} */ objectId, /** @type {{limit?: number, offset?: number, sort?: "observedAt"|"-observedAt"}} */ opts = {}) {
  await delay();
  await getObject(objectId); // 404 for unknown ids
  /** @type {Observation[]} */
  const all = (await loadFixture("observations.json")).filter((/** @type {Observation} */ o) => o.objectIds.includes(objectId));
  all.sort((a, b) => (a.observedAt ?? "").localeCompare(b.observedAt ?? ""));
  if (opts.sort === "-observedAt") all.reverse();
  const limit = Math.min(opts.limit ?? 50, 200), offset = opts.offset ?? 0;
  return { items: all.slice(offset, offset + limit), total: all.length, limit, offset,
           ...(all.length ? {} : { coverageNote: "No SPHEREx observations found for this position (illustrative sample)." }) };
}

/** #5 */
export async function getObservation(/** @type {string} */ observationId) {
  await delay(0.4);
  return findObservation(observationId);
}

async function findObservation(/** @type {string} */ observationId) {
  const obs = (await loadFixture("observations.json")).find((/** @type {Observation} */ o) => o.observationId === observationId);
  if (!obs) throw new ApiClientError("Observation not found.", { code: "NOT_FOUND" });
  return /** @type {Observation} */ (obs);
}

/** Position of an observation in its object's time order (drives where mock sources are drawn). */
async function frameIndex(/** @type {Observation} */ obs) {
  const same = (await loadFixture("observations.json")).filter((/** @type {Observation} */ o) => o.objectIds[0] === obs.objectIds[0])
    .sort((/** @type {Observation} */ a, /** @type {Observation} */ b) => (a.observedAt ?? "").localeCompare(b.observedAt ?? ""));
  return same.findIndex((/** @type {Observation} */ o) => o.observationId === obs.observationId);
}

/** Approximate WCS summary (ILLUSTRATIVE). */
function wcsFor(/** @type {Observation} */ obs, w = 1000, h = 600) {
  const s = PIXEL_SCALE / 3600;
  return { ctype1: "RA---TAN", ctype2: "DEC--TAN", crval: /** @type {[number, number]} */ ([obs.center.raDeg, obs.center.decDeg]),
           crpix: /** @type {[number, number]} */ ([w / 2, h / 2]), cd: /** @type {[[number, number],[number, number]]} */ ([[-s, 0], [0, s]]),
           pixelScaleArcsec: PIXEL_SCALE, approximate: true };
}

/** #6 */
export async function getObservationImage(/** @type {string} */ observationId, /** @type {Record<string, any>} */ _params = {}) {
  await delay();
  const obs = await findObservation(observationId);
  const generatedAt = new Date().toISOString();
  if (obs.image.status === "unavailable") return { observationId, availability: obs.image, generatedAt };
  if (obs.image.status === "processing") {
    const n = (imageRequests.get(observationId) ?? 0) + 1; imageRequests.set(observationId, n);
    if (n === 1) return { observationId, availability: { status: "processing", statusReason: obs.image.statusReason ?? "Image being prepared.", retryAfterSeconds: 1 }, generatedAt };
  }
  const img = await renderObservation(obs, obs.objectIds[0], await frameIndex(obs));
  return { observationId, availability: { status: "available" }, imageUrl: img.url, width: img.width, height: img.height,
           stretch: { mode: "asinh", lowValue: 0, highValue: 255, unit: "display units (illustrative)" }, wcs: wcsFor(obs, img.width, img.height), generatedAt };
}

/** #7 Mock-only tangent-plane maths; the real backend does this server-side (OD-6). */
export async function pixelToSky(/** @type {string} */ observationId, /** @type {number} */ x, /** @type {number} */ y) {
  const obs = await findObservation(observationId);
  const w = wcsFor(obs), s = PIXEL_SCALE / 3600;
  const dec = obs.center.decDeg - (y - w.crpix[1]) * s;
  const ra = obs.center.raDeg - ((x - w.crpix[0]) * s) / Math.cos(obs.center.decDeg * Math.PI / 180);
  return { sky: { raDeg: (ra + 360) % 360, decDeg: dec } };
}

/** @param {Observation} a @param {Observation} b */
function overlap(a, b) {
  const x = a.wavelengthRangeUm, y = b.wavelengthRangeUm;
  if (!x || !y) return null;
  return Math.min(x.max, y.max) > Math.max(x.min, y.min);
}

/** #8 The compatibility verdict is decided here in the MOCK; the real backend decides it (pipeline §5). */
export async function createComparison(/** @type {{baseObservationId: string, targetObservationId: string, mode: "blink"|"overlay"|"difference"}} */ req) {
  await delay(1.5);
  const [base, target] = await Promise.all([findObservation(req.baseObservationId), findObservation(req.targetObservationId)]);
  const wl = overlap(base, target);
  const bad = (o) => o.qualitySummary === "degraded" || o.qualitySummary === "poor";
  /** @type {Array<{name: string, result: "pass"|"warn"|"fail"|"notEvaluated", detail: string}>} */
  const checks = [
    { name: "footprint", result: base.objectIds.some(id => target.objectIds.includes(id)) ? "pass" : "fail", detail: "Both observations cover the target position." },
    { name: "wavelength", result: wl === null ? "notEvaluated" : wl ? "pass" : "fail",
      detail: wl ? "Wavelength ranges overlap." : "Wavelength ranges do not overlap, so these are different bands, not a time-series comparison." },
    { name: "calibration", result: "notEvaluated", detail: "Calibration comparability is not evaluated in the sample." },
    { name: "alignment", result: "pass", detail: "Aligned onto a common grid (illustrative residual)." },
    { name: "quality", result: bad(base) || bad(target) ? "warn" : "pass", detail: bad(base) || bad(target) ? "One observation has quality flags near this region." : "No quality flags affect the region." },
    { name: "dataRelease", result: base.dataRelease === target.dataRelease ? "pass" : "warn", detail: base.dataRelease === target.dataRelease ? "Same data release." : "Different data releases; calibration may differ." },
  ];
  const verdict = checks.some(c => c.result === "fail") ? "not_comparable"
    : checks.some(c => c.result === "warn" || c.result === "notEvaluated") ? "comparable_with_caveats" : "comparable";
  const warnings = checks.filter(c => c.result === "warn" || c.result === "fail").map(c => c.detail);

  const id = `mock-cmp-${comparisons.size + 1}`;
  /** @type {Record<string, any>} */
  const result = { comparisonId: id, mode: req.mode, baseObservationId: base.observationId, targetObservationId: target.observationId,
    availability: { status: "available" }, compatibility: { verdict, checks }, measurements: [], warnings, createdAt: new Date().toISOString() };

  if (verdict !== "not_comparable") {
    if (base.image.status === "unavailable" || target.image.status === "unavailable") {
      result.availability = { status: "unavailable", statusReason: "One of the images is not available." };
    } else {
      result.alignment = { method: "ILLUSTRATIVE mock alignment", residualArcsec: 0.4 };
      const [ib, it] = await Promise.all([renderObservation(base, base.objectIds[0], await frameIndex(base)), renderObservation(target, target.objectIds[0], await frameIndex(target))]);
      const wrap = (/** @type {Observation} */ o, /** @type {any} */ i) => ({ observationId: o.observationId, availability: { status: "available" }, imageUrl: i.url, width: i.width, height: i.height, wcs: wcsFor(o), generatedAt: result.createdAt });
      result.baseImage = wrap(base, ib); result.targetImage = wrap(target, it);
      if (req.mode === "difference") {
        const d = await renderDifference(ib, it, `diff-${base.observationId}-${target.observationId}`);
        result.differenceImage = wrap(target, d);
        result.warnings.push("A difference can come from noise, alignment or calibration as well as real change.");
      }
    }
  }
  comparisons.set(id, result);
  return result;
}

/** #9 */
export async function getComparison(/** @type {string} */ id) {
  await delay(0.4);
  const c = comparisons.get(id);
  if (!c) throw new ApiClientError("Comparison not found.", { code: "NOT_FOUND" });
  return c;
}

/** #10 */
export async function getSpectrum(/** @type {string} */ objectId, /** @type {Record<string, any>} */ _params = {}) {
  await delay();
  await getObject(objectId);
  const spectra = await loadFixture("spectra.json");
  if (!spectra[objectId]) {
    return { spectrumId: `mock-spec-none-${objectId}`, objectId, availability: { status: "unavailable", statusReason: "No spectrum is available for this target (illustrative sample)." },
      position: (await getObject(objectId)).coordinates, extractionMethod: "n/a", wavelengthUnit: "um", fluxUnit: "n/a", points: [], warnings: [], generatedAt: new Date().toISOString() };
  }
  return spectra[objectId];
}

/** #11 Deterministic ILLUSTRATIVE values; real values are computed by the backend from data arrays. */
export async function createMeasurement(/** @type {{kind: string, observationIds: string[], region?: {center: {raDeg: number, decDeg: number}, radiusArcsec: number}}} */ req) {
  await delay();
  const observations = await Promise.all(req.observationIds.map(findObservation));
  const seed = hash(req.kind + req.observationIds.join("|"));
  const value = Math.round((20 + (seed % 6000) / 100) * 100) / 100;
  const flags = observations.flatMap(o => o.qualityFlags);
  return { measurementId: `mock-meas-${seed.toString(36)}`, kind: req.kind, value, unit: "arb. units (illustrative)",
    uncertainty: Math.round((1.5 + (seed % 200) / 100) * 100) / 100, uncertaintyBasis: "illustrative random scatter, not propagated from data",
    observationIds: req.observationIds, ...(req.region ? { region: req.region } : {}),
    method: `ILLUSTRATIVE mock ${req.kind} (aperture ${req.region?.radiusArcsec ?? 12} arcsec)`, qualityFlags: flags,
    warnings: ["Illustrative value. Not computed from data."], createdAt: new Date().toISOString() };
}

// ===========================================================================
// PROPOSED operations: NOT in contract 0.1.0. See docs/proposals/CONTRACT_PROPOSAL_0.2.0.md.
// In real mode these report "unavailable" until the backend developer agrees them.
// ===========================================================================

/** Contract 0.2.0 #19: candidates for an object, with a `track` per observation. */
export async function listCandidates(/** @type {string} */ objectId) {
  await delay();
  const [{ candidates }, { items: observations }] = await Promise.all([loadFixture("proposed.json"), listObservations(objectId)]);
  const defs = candidates[objectId] ?? [];
  const items = await Promise.all(defs.map(async (/** @type {any} */ c) => {
    const track = await Promise.all(observations.map(async (o, i) => {
      const p = candidatePosition(c, i);
      return { observationId: o.observationId, pixel: p, sky: (await pixelToSky(o.observationId, p.x, p.y)).sky };
    }));
    return { candidateId: c.candidateId, label: c.label, objectId, observationIds: observations.map(o => o.observationId), kind: c.kind, track,
      score: c.score, scoreBasis: c.scoreBasis, isProbability: false, qualityFlags: [], warnings: c.warnings, status: "candidate", createdAt: "2026-03-30T00:00:00Z",
      /* UI-only extras proposed alongside Candidate: */ description: c.description, evidence: c.evidence };
  }));
  items.sort((a, b) => b.score - a.score);
  return { items, total: items.length, limit: 50, offset: 0,
           ...(items.length ? {} : { coverageNote: "No candidates for this target (illustrative sample)." }) };
}

/** Proposed: catalogue-provided extras (classification, proper motion) for the details panel. */
export async function getObjectExtras(/** @type {string} */ objectId) {
  await delay(0.4);
  return (await loadFixture("proposed.json")).catalogue[objectId] ?? {};
}

/** Proposed: guided investigation definition. */
export async function getGuidedInvestigation(/** @type {string} */ guidedId) {
  await delay(0.4);
  const g = (await loadFixture("proposed.json")).guided[guidedId];
  if (!g) throw new ApiClientError("Guided investigation not found.", { code: "NOT_FOUND" });
  return g;
}
