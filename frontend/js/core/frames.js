// @ts-check
/**
 * Frame loading for the viewer and comparisons. Fetches an observation's display image through
 * the API client and honours the contract's async rule: if `availability.status` is
 * "processing", re-GET after `retryAfterSeconds` until it changes (contract §5, "Async work").
 * Results are cached so blinking between dates is instant after the first load.
 */

import { api } from "../api/client.js";

/** @typedef {import("../api/types.js").Availability} Availability */
/** @typedef {{ observationId: string, availability: Availability, imageUrl?: string, width?: number, height?: number, wcs?: any }} Frame */

/** @type {Map<string, Promise<Frame>>} */
const cache = new Map();

/** @param {string} observationId @returns {Promise<Frame>} */
export function loadFrame(observationId) {
  if (!cache.has(observationId)) {
    const p = fetchWithPolling(observationId);
    cache.set(observationId, p);
    // Do not cache failures, so a retry actually retries.
    p.catch(() => cache.delete(observationId));
  }
  return /** @type {Promise<Frame>} */ (cache.get(observationId));
}

/** Drop a cached frame (used by the Retry button for "failed" frames). */
export function forgetFrame(/** @type {string} */ id) { cache.delete(id); }

/** @param {string} id @param {number} [attempts] */
async function fetchWithPolling(id, attempts = 6) {
  /** @type {Frame} */
  let frame = await api.getObservationImage(id);
  while (frame.availability.status === "processing" && attempts-- > 0) {
    await new Promise(r => setTimeout(r, (frame.availability.retryAfterSeconds ?? 2) * 1000));
    frame = await api.getObservationImage(id);
  }
  return frame;
}

/** Warm the cache for a whole sequence so blinking is smooth. Errors are ignored here (shown per frame). */
export function preload(/** @type {string[]} */ ids) { ids.forEach(id => loadFrame(id).catch(() => {})); }
