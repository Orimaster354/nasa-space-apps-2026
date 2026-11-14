// @ts-check
/**
 * The ONLY module views use to get data. Same function names and shapes whether
 * CONFIG.useMock is true (fixtures) or false (Python backend over /api/v1).
 * Views must never fetch() directly and never parse FITS (contract: frontend rule 5).
 */

import { CONFIG } from "../config.js";
import { ApiClientError } from "./types.js";
import * as mock from "./mock/mockApi.js";

/**
 * Real HTTP call. Converts every failure into ApiClientError:
 *  - no response at all  -> network: true (contract §6: separate case)
 *  - non-2xx             -> the backend's ApiError envelope (message is user-safe)
 * @param {string} path @param {Record<string, string|number|undefined>} [params]
 */
async function http(path, params = {}) {
  const url = new URL(CONFIG.apiBase + path, window.location.origin);
  for (const [k, v] of Object.entries(params)) if (v !== undefined) url.searchParams.set(k, String(v));
  let res;
  try { res = await fetch(url); }
  catch { throw new ApiClientError("Can't reach the server. Check your connection and try again.", { network: true, retryable: true }); }
  if (!res.ok) {
    /** @type {import("./types.js").ApiErrorBody|null} */
    const body = await res.json().catch(() => null);
    throw new ApiClientError(body?.error.message ?? "Something went wrong.", { code: body?.error.code, retryable: body?.error.retryable ?? false });
  }
  return res.json();
}

const real = {
  health: () => http("/health"),
  searchObjects: (/** @type {string} */ q, opts = {}) => http("/objects/search", { q, ...opts }),
  resolveObject: (/** @type {{q?: string, ra?: number, dec?: number}} */ p) => http("/objects/resolve", p),
  getObject: (/** @type {string} */ id) => http(`/objects/${encodeURIComponent(id)}`),
  listObservations: (/** @type {string} */ id, opts = {}) => http(`/objects/${encodeURIComponent(id)}/observations`, opts),
  getObservation: (/** @type {string} */ id) => http(`/observations/${encodeURIComponent(id)}`),
};

/** True while illustrative mock data is in use; views show the "illustrative" badge. */
export const isMock = () => CONFIG.useMock;

export const api = CONFIG.useMock ? mock : real;
