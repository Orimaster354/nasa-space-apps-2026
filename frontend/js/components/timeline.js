// @ts-check
/**
 * Observation timeline + transport controls (play / pause / stop / step / speed).
 * Dumb component: shows the observations it is given and reports user actions.
 * The active index lives in the shared store, so viewer and timeline never disagree.
 */

import { esc, shortDate } from "../core/dom.js";
import { SPEEDS } from "../core/playback.js";

/** @typedef {import("../api/types.js").Observation} Observation */
/**
 * @typedef {Object} TimelineHandlers
 * @property {(index:number)=>void} onSelect
 * @property {()=>void} onToggle
 * @property {()=>void} onStop
 * @property {(delta:number)=>void} onStep
 * @property {(speed:number)=>void} onSpeed
 */

/** @param {HTMLElement} root @param {TimelineHandlers} h */
export function createTimeline(root, h) {
  /** @type {Observation[]} */ let obs = [];
  let active = 0, playing = false, speed = 1;

  root.classList.add("timeline");

  function render() {
    root.innerHTML = `
      <div class="transport" role="group" aria-label="Playback controls">
        <button class="btn btn-icon" data-a="prev" aria-label="Previous observation" title="Previous">⏮</button>
        <button class="btn btn-icon" data-a="toggle" aria-label="${playing ? "Pause" : "Play"}" title="${playing ? "Pause" : "Play"} (blink through dates)">${playing ? "⏸" : "▶"}</button>
        <button class="btn btn-icon" data-a="stop" aria-label="Stop and return to first observation" title="Stop">■</button>
        <button class="btn btn-icon" data-a="next" aria-label="Next observation" title="Next">⏭</button>
        <label class="speed"><span class="muted">Speed</span>
          <select class="select" data-a="speed" aria-label="Playback speed">
            ${SPEEDS.map(s => `<option value="${s}" ${s === speed ? "selected" : ""}>${s}×</option>`).join("")}
          </select></label>
      </div>
      <div class="track" role="radiogroup" aria-label="Observation dates">
        ${obs.length ? obs.map((o, i) => dot(o, i)).join("") : `<span class="muted">No observations</span>`}
      </div>`;
  }

  /** @param {Observation} o @param {number} i */
  function dot(o, i) {
    const flags = [];
    if (o.qualitySummary === "degraded" || o.qualitySummary === "poor") flags.push(`quality ${o.qualitySummary}`);
    if (o.image.status !== "available") flags.push(`image ${o.image.status}`);
    const cls = ["dot", o.qualitySummary === "degraded" || o.qualitySummary === "poor" ? "dot-warn" : "", o.image.status !== "available" ? "dot-missing" : ""].join(" ");
    return `<button class="${cls}" role="radio" aria-checked="${i === active}" tabindex="${i === active ? 0 : -1}" data-i="${i}"
        aria-label="${esc(shortDate(o.observedAt))}${flags.length ? ", " + esc(flags.join(", ")) : ""}" ${flags.length ? `title="${esc(flags.join(", "))}"` : ""}>
        <span class="dot-mark" aria-hidden="true"></span><span class="dot-date">${esc(shortDate(o.observedAt))}</span></button>`;
  }

  root.addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target).closest("[data-a],[data-i]");
    if (!t) return;
    const el = /** @type {HTMLElement} */ (t);
    if (el.dataset.i !== undefined) return h.onSelect(Number(el.dataset.i));
    switch (el.dataset.a) {
      case "toggle": h.onToggle(); break;
      case "stop": h.onStop(); break;
      case "prev": h.onStep(-1); break;
      case "next": h.onStep(1); break;
    }
  });
  root.addEventListener("change", e => {
    const el = /** @type {HTMLSelectElement} */ (e.target);
    if (el.dataset.a === "speed") { speed = Number(el.value); h.onSpeed(speed); }
  });
  // Arrow keys move between dates (roving tabindex, like a radio group).
  root.addEventListener("keydown", e => {
    if (!(/** @type {HTMLElement} */ (e.target)).closest(".track")) return;
    const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    h.onSelect(Math.min(obs.length - 1, Math.max(0, active + dir)));
    queueMicrotask(() => /** @type {HTMLElement|null} */ (root.querySelector(`[data-i="${active}"]`))?.focus());
  });

  return {
    /** @param {Observation[]} list @param {number} index */
    set(list, index) { obs = list; active = index; render(); },
    setActive(/** @type {number} */ index) {
      active = index;
      root.querySelectorAll(".dot").forEach((d, i) => { d.setAttribute("aria-checked", String(i === index)); d.setAttribute("tabindex", i === index ? "0" : "-1"); });
    },
    setPlaying(/** @type {boolean} */ p) {
      playing = p;
      const b = root.querySelector('[data-a="toggle"]');
      if (b) { b.textContent = p ? "⏸" : "▶"; b.setAttribute("aria-label", p ? "Pause" : "Play"); }
    },
    /** Move keyboard focus to the active dot after an arrow-key selection. */
    focusActive() { /** @type {HTMLElement|null} */ (root.querySelector(`[data-i="${active}"]`))?.focus(); },
  };
}
