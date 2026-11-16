// @ts-check
/**
 * Tiny app-wide event bus for things that are not shared state but that the companion reacts to
 * (play/pause, a save, missing data, a wrong guess...). Views emit; Navi listens. Nothing depends
 * on a listener being present, so views stay independent of the companion.
 *
 * @typedef {"play"|"pause"|"stop"|"step"|"save"|"note_added"|"image_missing"|"image_processing"|
 *   "not_comparable"|"gap_band"|"guided_wrong"|"guided_right"|"study"|"tour_target"|"focus_mode"|
 *   "tour_finished"|"epoch_compared"|"stack_viewed"} AppEventType
 */

const NAME = "sx:event";

/** @param {AppEventType} type @param {Record<string, any>} [data] */
export function emit(type, data = {}) {
  window.dispatchEvent(new CustomEvent(NAME, { detail: { type, ...data } }));
}

/** @param {(e: { type: AppEventType } & Record<string, any>) => void} fn @returns {() => void} */
export function on(fn) {
  const h = (/** @type {Event} */ e) => fn(/** @type {CustomEvent} */ (e).detail);
  window.addEventListener(NAME, h);
  return () => window.removeEventListener(NAME, h);
}
