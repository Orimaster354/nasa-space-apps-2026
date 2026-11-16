// @ts-check
/**
 * Achievements: a light game layer over the real actions a visitor takes (finishing Navi's tour, completing a comparison, reading a spectrum...).
 * Everything is stored in this browser only (localStorage), nothing is sent anywhere, and nothing here changes what the science tools do.
 *
 * It listens to the shared store (what the visitor selected) and to the app event bus (what they did), and only counts actions the visitor
 * started themselves (a click or a key press just before), so restoring a saved session or loading a shared link never unlocks anything.
 */

import { getState, subscribe } from "./store.js";
import { on } from "./events.js";
import { loadJson, saveJson } from "./storage.js";

const KEY = "spherex.achievements.v1";

/** @typedef {{ id: string, title: string, text: string, glyph: string, goal?: number, counter?: string }} Achievement */

/** @type {Achievement[]} */
export const ACHIEVEMENTS = [
  { id: "first-light", title: "First Light", text: "Open your first target.", glyph: "✦" },
  { id: "globetrotter", title: "Globetrotter", text: "Open 3 different targets.", glyph: "◉", goal: 3, counter: "objects" },
  { id: "time-traveller", title: "Time Traveller", text: "View 5 different dates of one target.", glyph: "⌛", goal: 5, counter: "dates" },
  { id: "blink-master", title: "Blink Master", text: "Flip between two dates with Blink mode.", glyph: "⇄" },
  { id: "every-angle", title: "Every Angle", text: "Try Blink, Side by side, Overlay and Difference.", glyph: "◫", goal: 4, counter: "modes" },
  { id: "full-comparison", title: "Full Comparison", text: "Complete a comparison of two observations.", glyph: "⚖" },
  { id: "tutorial-graduate", title: "Tutorial Graduate", text: "Finish Navi's tour of the site.", glyph: "✎" },
  { id: "shift-spotter", title: "Shift Spotter", text: "Complete the Spot the shift activity.", glyph: "⌖" },
  { id: "candidate-hunter", title: "Candidate Hunter", text: "Open a candidate and look at its evidence.", glyph: "☄" },
  { id: "spectrum-explorer", title: "Spectrum Explorer", text: "Open the Spectrum page and view a spectrum.", glyph: "≋" },
  { id: "rainbow-reader", title: "Rainbow Reader", text: "Look at 5 different spectral bands.", glyph: "❖", goal: 5, counter: "bands" },
  { id: "time-detective", title: "Time Detective", text: "Compare a spectrum from earlier and later observations.", glyph: "◷" },
  { id: "curious-mind", title: "Curious Mind", text: "Ask Navi a question.", glyph: "?" },
  { id: "note-taker", title: "Note Taker", text: "Write down something you noticed.", glyph: "✐" },
  { id: "archivist", title: "Archivist", text: "Save an investigation.", glyph: "▣" },
  { id: "librarian", title: "Librarian", text: "Visit the Library of stored targets.", glyph: "▤" },
  { id: "deep-stacker", title: "Deep Stacker", text: "Stack several observations into one deeper picture.", glyph: "▦" },
  { id: "completionist", title: "Completionist", text: "Unlock every other achievement.", glyph: "★" },
];

/** @typedef {{ unlocked: Record<string, string>, sets: Record<string, string[]> }} Saved */

/** @type {Saved} */
let saved = { unlocked: {}, sets: {} };
const listeners = new Set();
let lastUserAct = 0, started = false;

function load() {
  const s = loadJson(KEY, /** @type {any} */ (null));
  saved = s && typeof s === "object" ? { unlocked: s.unlocked ?? {}, sets: s.sets ?? {} } : { unlocked: {}, sets: {} };
}
function persist() { saveJson(KEY, saved); }

/** @param {(unlockedNow?: Achievement) => void} fn @returns {() => void} */
export function subscribeAchievements(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function notify(/** @type {Achievement|undefined} */ now) { listeners.forEach(fn => fn(now)); }

/** @returns {Array<Achievement & { unlockedAt: string|null, progress: number|null }>} */
export function getAchievements() {
  return ACHIEVEMENTS.map(a => ({ ...a, unlockedAt: saved.unlocked[a.id] ?? null, progress: a.counter ? (saved.sets[a.counter]?.length ?? 0) : null }));
}
export function unlockedCount() { return Object.keys(saved.unlocked).length; }

export function resetAchievements() { saved = { unlocked: {}, sets: {} }; persist(); notify(); }

/** @param {string} id */
function unlock(id) {
  if (saved.unlocked[id]) return;
  saved.unlocked[id] = new Date().toISOString();
  persist();
  notify(ACHIEVEMENTS.find(x => x.id === id));
  if (id !== "completionist" && ACHIEVEMENTS.filter(x => x.id !== "completionist").every(x => saved.unlocked[x.id])) unlock("completionist");
}

/** Add a value to a counted set and unlock when the goal is reached. @param {string} name @param {string} value */
function count(name, value) {
  const set = saved.sets[name] ??= [];
  if (value && !set.includes(value)) { set.push(value); if (set.length > 60) set.shift(); persist(); notify(); }
  for (const a of ACHIEVEMENTS) if (a.counter === name && a.goal && set.length >= a.goal) unlock(a.id);
}

const byUser = (/** @type {number} */ ms = 2000) => Date.now() - lastUserAct < ms;      // a click or key press just before

/** Start listening (once). */
export function initAchievements() {
  if (started) return;
  started = true;
  load();
  ["pointerdown", "keydown", "click"].forEach(t => document.addEventListener(t, e => {
    if (!/** @type {Element} */ (e.target).closest?.(".ach-toast")) lastUserAct = Date.now();
  }, { capture: true, passive: true }));

  let prev = getState();
  subscribe((s, patch) => {
    const last = prev; prev = s;
    // The visitor's own selections.
    if (byUser()) {
      if ("objectId" in patch && patch.objectId && patch.objectId !== last.objectId) { unlock("first-light"); count("objects", patch.objectId); }
      if ("observationId" in patch && patch.observationId && patch.observationId !== last.observationId) count("dates", patch.observationId);
      if ("compareMode" in patch && patch.compareMode && patch.compareMode !== "view") {
        count("modes", patch.compareMode);
        if (patch.compareMode === "blink") unlock("blink-master");
      }
      if ("candidateId" in patch && patch.candidateId) unlock("candidate-hunter");
      if ("bandIndex" in patch && patch.bandIndex) count("bands", String(patch.bandIndex));
      if ("notes" in patch && (patch.notes?.length ?? 0) > (last.notes?.length ?? 0)) unlock("note-taker");
    }
    // Things the app records or the page finishes by itself after the visitor acted.
    if ("guided" in patch && s.guided?.done && !last.guided?.done) unlock("shift-spotter");
    if ("messages" in patch) {
      const users = (m) => (m ?? []).filter((x) => x.role === "user").length;
      if (users(s.messages) > users(last.messages)) unlock("curious-mind");
    }
    if ("history" in patch) {
      const ev = s.history[s.history.length - 1];
      if (ev && s.history.length >= (last.history?.length ?? 0)) {
        // A comparison or a spectrum finishes a second or two after the click that asked for it, so these allow a longer window.
        if (ev.type === "comparison_created" && byUser(15000)) unlock("full-comparison");
        if (ev.type === "spectrum_viewed" && byUser(150000)) unlock("spectrum-explorer");      // the first spectrum of a target can take a minute to appear
      }
    }
  });

  on(e => {
    switch (e.type) {
      case "tour_finished": unlock("tutorial-graduate"); break;
      case "save": if (byUser(15000)) unlock("archivist"); break;
      case "epoch_compared": if (byUser(60000)) unlock("time-detective"); break;
      case "stack_viewed": if (byUser(60000)) unlock("deep-stacker"); break;
    }
  });

  const visit = () => { if (byUser() && /^#\/library/.test(window.location.hash)) unlock("librarian"); };
  window.addEventListener("hashchange", visit);
  visit();
}
