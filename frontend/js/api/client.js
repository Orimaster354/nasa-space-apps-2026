// @ts-check
/**
 * The ONLY module views use to get data. Same function names and shapes whether
 * CONFIG.useMock is true (fixtures) or false (Python backend over /api/v1).
 * Views must never fetch() directly and never parse FITS (contract: frontend rule 5).
 *
 * Operations numbered #n are from docs/FRONTEND_BACKEND_CONTRACT.md §5.
 * Operations marked FRONTEND-ONLY are not in the contract; in real mode they reject with
 * DATA_UNAVAILABLE so the UI shows an "unavailable" state instead of breaking.
 */

import { CONFIG } from "../config.js";
import { ApiClientError } from "./types.js";
import * as mock from "./mock/mockApi.js";

/**
 * Real HTTP call. Converts every failure into ApiClientError:
 *  - no response at all  -> network: true (contract §6: separate case)
 *  - non-2xx             -> the backend's ApiError envelope (message is user-safe)
 * @param {string} path
 * @param {{ params?: Record<string, string|number|undefined>, method?: string, body?: unknown }} [opts]
 */
async function http(path, opts = {}) {
  const url = new URL(CONFIG.apiBase + path, window.location.origin);
  for (const [k, v] of Object.entries(opts.params ?? {})) if (v !== undefined) url.searchParams.set(k, String(v));
  let res;
  try {
    res = await fetch(url, { method: opts.method ?? "GET",
      ...(opts.body !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body) } : {}) });
  } catch { throw new ApiClientError("Can't reach the server. Check your connection and try again.", { network: true, retryable: true }); }
  if (!res.ok) {
    /** @type {import("./types.js").ApiErrorBody|null} */
    const body = await res.json().catch(() => null);
    throw new ApiClientError(body?.error.message ?? "Something went wrong.", { code: body?.error.code, retryable: body?.error.retryable ?? false });
  }
  return res.status === 204 ? undefined : res.json();
}

const enc = encodeURIComponent;

/**
 * Real-backend view choices (data layer only, no UI): field of view and wavelength band come from the page URL
 * (?fov=15&band=SPHEREx-D2, or band=all) and fall back to CONFIG. Read per call, so they follow the URL.
 * `undefined` values are skipped by http(), so the backend default applies.
 * @returns {{ fovArcmin: number|undefined, bandpass: string|undefined }}
 */
function viewParams() {
  let q = new URLSearchParams("");
  try { q = new URLSearchParams(window.location.search); } catch { /* no window (tests) */ }
  const fov = Number(q.get("fov"));
  const band = q.get("band") ?? CONFIG.bandpass;
  return { fovArcmin: fov > 0 ? fov : CONFIG.fovArcmin, bandpass: band && band !== "all" ? band : undefined };
}
const notYet = (/** @type {string} */ what) => () =>
  Promise.reject(new ApiClientError(`${what} is not available from the backend yet.`, { code: "DATA_UNAVAILABLE" }));

/** @type {typeof mock} */
const real = /** @type {any} */ ({
  health: () => http("/health"),
  searchObjects: (/** @type {string} */ q, opts = {}) => http("/objects/search", { params: { q, ...opts } }),
  resolveObject: (/** @type {any} */ p) => http("/objects/resolve", { params: p }),
  getObject: (/** @type {string} */ id) => http(`/objects/${enc(id)}`),
  // Sample picker: with a real backend the user searches instead.
  listSampleObjects: () => Promise.resolve([]),
  listObservations: (/** @type {string} */ id, opts = {}) => http(`/objects/${enc(id)}/observations`, { params: { bandpass: viewParams().bandpass, ...opts } }),
  getObservation: (/** @type {string} */ id) => http(`/observations/${enc(id)}`),
  getObservationImage: (/** @type {string} */ id, params = {}) => http(`/observations/${enc(id)}/image`, { params: { fovArcmin: viewParams().fovArcmin, ...params } }),
  pixelToSky: (/** @type {string} */ id, /** @type {number} */ x, /** @type {number} */ y) => http(`/observations/${enc(id)}/pixel-to-sky`, { params: { x, y, fovArcmin: viewParams().fovArcmin } }),
  createComparison: (/** @type {any} */ body) => http("/comparisons", { method: "POST", body }),
  getComparison: (/** @type {string} */ id) => http(`/comparisons/${enc(id)}`),
  getSpectrum: (/** @type {string} */ id, params = {}) => http(`/objects/${enc(id)}/spectrum`, { params }),
  createMeasurement: (/** @type {any} */ body) => http("/measurements", { method: "POST", body }),
  sendChatMessage: (/** @type {any} */ body) => http("/chat/messages", { method: "POST", body }),
  listInvestigations: (opts = {}) => http("/investigations", { params: opts }),
  createInvestigation: (/** @type {any} */ body) => http("/investigations", { method: "POST", body }),
  getInvestigation: (/** @type {string} */ id) => http(`/investigations/${enc(id)}`),
  updateInvestigation: (/** @type {string} */ id, /** @type {any} */ body) => http(`/investigations/${enc(id)}`, { method: "PUT", body }),
  deleteInvestigation: (/** @type {string} */ id) => http(`/investigations/${enc(id)}`, { method: "DELETE" }),
  // Contract 0.2.0 #19: candidates ranked by the backend.
  listCandidates: (/** @type {string} */ id, opts = {}) => http(`/objects/${enc(id)}/candidates`, { params: opts }),
  // Catalogue extras come from AstronomicalObject (objectType, properMotion); there is no separate operation.
  getObjectExtras: async (/** @type {string} */ id) => {
    const o = await http(`/objects/${enc(id)}`);
    return { ...(o.objectType ? { classification: o.objectType } : {}), ...(o.properMotion ? { properMotion: o.properMotion } : {}) };
  },
  // FRONTEND-ONLY (not in the contract): guided content and chat suggestions are scripted in the frontend.
  // Frontend-only operation (not in the contract). The backend builds the definition from a real candidate; if none exists it answers 404 and the card says so.
  getGuidedInvestigation: (/** @type {string} */ id) => http(`/guided/${enc(id)}`),
  getChatSuggestions: () => Promise.resolve([]),
  // Frontend-only operations (not in the contract): what is stored on the server, the required credits, and a derived stack of stored frames.
  getLibrary: () => http("/library"),
  getCredits: () => http("/credits"),
  getCoadd: (/** @type {string} */ id, opts = {}) => http(`/objects/${enc(id)}/coadd`, { params: opts }),
  getSpectrumComparison: (/** @type {string} */ id, opts = {}) => http(`/objects/${enc(id)}/spectrum-comparison`, { params: opts }),
  getChallenges: (/** @type {string} */ id) => http(`/objects/${enc(id)}/challenges`),
});

/** True while illustrative mock data is in use; views show the "illustrative" badge. */
export const isMock = () => CONFIG.useMock;

export const api = CONFIG.useMock ? mock : real;
