// @ts-check
/**
 * The ONLY module views use to get data. Same function names and shapes whether
 * CONFIG.useMock is true (fixtures) or false (Python backend over /api/v1).
 * Views must never fetch() directly and never parse FITS (contract: frontend rule 5).
 *
 * Operations numbered #n are from docs/FRONTEND_BACKEND_CONTRACT.md §5.
 * Operations marked PROPOSED are not in contract 0.1.0; in real mode they reject with
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
  listObservations: (/** @type {string} */ id, opts = {}) => http(`/objects/${enc(id)}/observations`, { params: opts }),
  getObservation: (/** @type {string} */ id) => http(`/observations/${enc(id)}`),
  getObservationImage: (/** @type {string} */ id, params = {}) => http(`/observations/${enc(id)}/image`, { params }),
  pixelToSky: (/** @type {string} */ id, /** @type {number} */ x, /** @type {number} */ y) => http(`/observations/${enc(id)}/pixel-to-sky`, { params: { x, y } }),
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
  // PROPOSED (contract 0.2.0 proposal):
  listCandidates: notYet("Candidate detection"),
  getObjectExtras: () => Promise.resolve({}),
  getGuidedInvestigation: notYet("Guided investigations"),
  getChatSuggestions: () => Promise.resolve([]),
});

/** True while illustrative mock data is in use; views show the "illustrative" badge. */
export const isMock = () => CONFIG.useMock;

export const api = CONFIG.useMock ? mock : real;
