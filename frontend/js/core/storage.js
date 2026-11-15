// @ts-check
/**
 * Safe localStorage wrapper. Storage can throw or be empty (private window,
 * blocked site data), so every access is guarded and the app must still work
 * without it.
 */

/** @template T @param {string} key @param {T} fallback @returns {T} */
export function loadJson(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch { return fallback; }
}

/** @param {string} key @param {unknown} value @returns {boolean} true if saved */
export function saveJson(key, value) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); return true; }
  catch { return false; }
}
