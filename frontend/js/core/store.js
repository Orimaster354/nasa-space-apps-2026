// @ts-check
/**
 * Shared investigation state (Phase H).
 *
 * ONE store feeds Investigate, Compare, Spectrum and Notes, so switching tools
 * never loses the selected object, coordinates, dates, band, bookmarks or notes.
 * Views read with getState() and subscribe(); they change state only via update().
 *
 * Persisted parts are written through storage.js. Only the fields in
 * URL_KEYS are mirrored into the URL hash query so a link restores the view.
 */

import { loadJson, saveJson } from "./storage.js";

/**
 * @typedef {Object} InvestigationState
 * @property {string|null} objectId       Selected AstronomicalObject id (opaque).
 * @property {{raDeg:number, decDeg:number}|null} coordinates  Search / view centre, ICRS degrees.
 * @property {string|null} observationId  Current observation shown in the viewer.
 * @property {string|null} compareBaseId  Comparison base observation.
 * @property {string|null} compareTargetId Comparison target observation.
 * @property {"blink"|"sidebyside"|"overlay"|"difference"} compareMode  How Compare shows the pair.
 * @property {string|null} candidateId    Selected candidate (mock-only until contract agreed).
 * @property {number|null} bandIndex      Selected spectral band (1-based) in the Spectral Explorer.
 * @property {"beginner"|"advanced"} naviMode
 * @property {boolean} naviOpen
 * @property {Array<Object>} notes        Contract `Note[]` shape.
 * @property {Array<Object>} bookmarks    Contract `Bookmark[]` shape.
 * @property {Array<Object>} history      Contract `InvestigationEvent[]` shape.
 * @property {Array<{role: "user"|"assistant", content: string, response?: Object, at: string}>} messages  Companion conversation.
 * @property {{id: string, step: number, hints: number, done: boolean, base?: number}|null} guided  Guided investigation progress.
 * @property {string} title               Working investigation title.
 */

/** @type {InvestigationState} */
const DEFAULTS = {
  objectId: null,
  coordinates: null,
  observationId: null,
  compareBaseId: null,
  compareTargetId: null,
  compareMode: "blink",
  candidateId: null,
  bandIndex: null,
  naviMode: "beginner",
  naviOpen: false,
  notes: [],
  bookmarks: [],
  history: [],
  messages: [],
  guided: null,
  title: "Untitled investigation",
};

/** State keys that survive reloads (localStorage). */
const PERSIST_KEYS = Object.keys(DEFAULTS);
/** Shareable subset mirrored into the URL (short query names). */
const URL_KEYS = { objectId: "o", observationId: "obs", compareBaseId: "a", compareTargetId: "b", compareMode: "m", candidateId: "c", bandIndex: "band" };
const STORAGE_KEY = "spherex.investigation.v1";

let state = { ...DEFAULTS, ...pick(loadJson(STORAGE_KEY, {}), PERSIST_KEYS) };
const listeners = new Set();

/** @param {Object} obj @param {string[]} keys */
function pick(obj, keys) {
  /** @type {Record<string, any>} */ const out = {};
  for (const k of keys) if (obj && k in obj) out[k] = obj[k];
  return out;
}

/** @returns {Readonly<InvestigationState>} */
export function getState() { return state; }

/**
 * Merge a patch, persist, notify subscribers.
 * @param {Partial<InvestigationState>} patch
 */
export function update(patch) {
  state = { ...state, ...patch };
  saveJson(STORAGE_KEY, pick(state, PERSIST_KEYS));
  for (const fn of listeners) fn(state, patch);
}

/**
 * @param {(s: InvestigationState, patch: Partial<InvestigationState>) => void} fn
 * @returns {() => void} unsubscribe
 */
export function subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); }

/** Reset everything (used by "New investigation"). */
export function reset() { update({ ...DEFAULTS }); }

/** Serialise the shareable subset to a query string like "o=obj1&band=12". */
export function toQuery() {
  const q = new URLSearchParams();
  for (const [key, short] of Object.entries(URL_KEYS)) {
    const v = /** @type {any} */ (state)[key];
    if (v !== null && v !== undefined) q.set(short, String(v));
  }
  return q.toString();
}

/** Apply shareable state from a query string (called by the router on load). */
export function fromQuery(/** @type {string} */ query) {
  const q = new URLSearchParams(query);
  /** @type {Record<string, any>} */ const patch = {};
  for (const [key, short] of Object.entries(URL_KEYS)) {
    if (!q.has(short)) continue;
    const raw = /** @type {string} */ (q.get(short));
    patch[key] = key === "bandIndex" ? Number(raw) : raw;
  }
  if (Object.keys(patch).length) update(/** @type {any} */ (patch));
}

/**
 * Append a contract `InvestigationEvent` (object_opened, observation_viewed, ...).
 * Consecutive duplicates are skipped so autoplay does not flood the history.
 * @param {"object_opened"|"observation_viewed"|"comparison_created"|"spectrum_viewed"|"measurement_created"} type
 * @param {string} [targetId]
 */
export function logEvent(type, targetId) {
  const last = state.history[state.history.length - 1];
  if (last && last.type === type && last.targetId === targetId) return;
  const ev = { eventId: `evt-${Date.now().toString(36)}-${state.history.length}`, type, ...(targetId ? { targetId } : {}), at: new Date().toISOString() };
  update({ history: [...state.history.slice(-199), ev] });
}
