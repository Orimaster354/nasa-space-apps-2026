// @ts-check
/** Central fixture loader. ALL mock data comes through here (from /frontend/mock/*.json). */

import { CONFIG } from "../../config.js";
import { ApiClientError } from "../types.js";

const cache = new Map();

/** Simulated network latency. */
export const delay = (/** @type {number} */ factor = 1) =>
  CONFIG.mockLatencyMs ? new Promise(r => setTimeout(r, CONFIG.mockLatencyMs * factor)) : Promise.resolve();

/**
 * @param {string} file  e.g. "objects.json"
 * @returns {Promise<any>} a fresh deep copy, so callers may mutate freely
 */
export async function loadFixture(file) {
  if (!cache.has(file)) {
    const res = await fetch(new URL(`../../../mock/${file}`, import.meta.url));
    if (!res.ok) throw new ApiClientError("Could not load illustrative data.", { code: "INTERNAL", retryable: true });
    cache.set(file, await res.json());
  }
  return structuredClone(cache.get(file));
}

/** Small deterministic string hash (mock values only). */
export function hash(/** @type {string} */ s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

/** Deterministic PRNG in [0,1). */
export function rng(/** @type {number} */ seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
