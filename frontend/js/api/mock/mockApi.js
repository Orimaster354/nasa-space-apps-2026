// @ts-check
/**
 * Mock backend. Returns ONLY contract-shaped data (docs/FRONTEND_BACKEND_CONTRACT.md §5),
 * so replacing it with the real API changes no view code. Data is ILLUSTRATIVE and
 * must never be shown as real NASA data (contract §9): views display the
 * "illustrative" badge whenever CONFIG.useMock is true.
 *
 * Implemented so far: operations #1-#5 and #18. Images/comparisons/spectra arrive in later milestones.
 */

import { CONFIG } from "../../config.js";
import { ApiClientError } from "../types.js";

/** @typedef {import("../types.js").AstronomicalObject} AstronomicalObject */
/** @typedef {import("../types.js").Observation} Observation */

const delay = () => new Promise(r => setTimeout(r, CONFIG.mockLatencyMs));
const cache = new Map();

/** @param {string} file @returns {Promise<any[]>} */
async function fixture(file) {
  if (!cache.has(file)) {
    const res = await fetch(new URL(`../../../mock/${file}`, import.meta.url));
    if (!res.ok) throw new ApiClientError("Could not load illustrative data.", { code: "INTERNAL", retryable: true });
    cache.set(file, await res.json());
  }
  return structuredClone(cache.get(file));
}

/** #18 */
export async function health() { await delay(); return { status: "ok", contractVersion: CONFIG.contractVersion }; }

/** #1 @param {string} q @param {{limit?: number}} [opts] */
export async function searchObjects(q, opts = {}) {
  await delay();
  if (!q || !q.trim()) throw new ApiClientError("Enter a name or coordinates to search.", { code: "INVALID_REQUEST" });
  const needle = q.trim().toLowerCase();
  /** @type {AstronomicalObject[]} */
  const all = await fixture("objects.json");
  const hits = all.filter(o => [o.name, ...o.aliases].some(n => n.toLowerCase().includes(needle)));
  const limit = Math.min(opts.limit ?? 10, 50);
  return { items: hits.slice(0, limit), total: hits.length, limit, offset: 0,
           ...(hits.length ? {} : { coverageNote: "No illustrative sample matches this name." }) };
}

/** #2 @param {{q?: string, ra?: number, dec?: number}} params */
export async function resolveObject(params) {
  await delay();
  /** @type {AstronomicalObject[]} */
  const all = await fixture("objects.json");
  if (params.q) {
    const page = await searchObjects(params.q, { limit: 1 });
    if (page.items[0]) return page.items[0];
  } else if (Number.isFinite(params.ra) && Number.isFinite(params.dec)) {
    const ra = /** @type {number} */ (params.ra), dec = /** @type {number} */ (params.dec);
    if (ra < 0 || ra >= 360 || dec < -90 || dec > 90)
      throw new ApiClientError("RA must be in [0, 360) and Dec in [-90, 90] degrees.", { code: "INVALID_REQUEST" });
    // Small-angle match within 0.5 degrees (mock only; the real backend does the science).
    const hit = all.find(o => Math.hypot((o.coordinates.raDeg - ra) * Math.cos(dec * Math.PI / 180), o.coordinates.decDeg - dec) < 0.5);
    if (hit) return hit;
  } else {
    throw new ApiClientError("Provide a name, or both RA and Dec.", { code: "INVALID_REQUEST" });
  }
  throw new ApiClientError("No illustrative sample at that position.", { code: "NOT_FOUND" });
}

/** #3 @param {string} objectId */
export async function getObject(objectId) {
  await delay();
  const obj = (await fixture("objects.json")).find(o => o.objectId === objectId);
  if (!obj) throw new ApiClientError("Object not found.", { code: "NOT_FOUND" });
  return obj;
}

/** #4 @param {string} objectId @param {{limit?: number, offset?: number, sort?: "observedAt"|"-observedAt"}} [opts] */
export async function listObservations(objectId, opts = {}) {
  await delay();
  await getObject(objectId); // 404 for unknown ids
  /** @type {Observation[]} */
  const all = (await fixture("observations.json")).filter(o => o.objectIds.includes(objectId));
  all.sort((a, b) => (a.observedAt ?? "").localeCompare(b.observedAt ?? ""));
  if (opts.sort === "-observedAt") all.reverse();
  const limit = Math.min(opts.limit ?? 50, 200), offset = opts.offset ?? 0;
  return { items: all.slice(offset, offset + limit), total: all.length, limit, offset,
           ...(all.length ? {} : { coverageNote: "No SPHEREx observations found for this position (illustrative sample)." }) };
}

/** #5 @param {string} observationId */
export async function getObservation(observationId) {
  await delay();
  const obs = (await fixture("observations.json")).find(o => o.observationId === observationId);
  if (!obs) throw new ApiClientError("Observation not found.", { code: "NOT_FOUND" });
  return obs;
}
