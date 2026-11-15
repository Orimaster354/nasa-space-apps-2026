// @ts-check
/**
 * Navi's personality: what she says, when, and the optional guided tour.
 *
 * Voice: curious, playful, a little sarcastic, occasionally dramatic, encouraging when it helps,
 * and scientifically careful. Jokes are about situations, never about the person; questions are
 * never mocked; uncertainty is stated honestly ("candidate", "could be noise"). All lines are
 * predefined here (no LLM). Unsolicited comments obey the "Comments" setting (On / Fewer / Off);
 * the assistant always answers questions.
 *
 * Tour: small contextual whispers, not a modal. It starts when the user arrives from "Begin
 * exploring" (or on a first visit), advances on Next, on dismissal, or when the user does the
 * thing being explained (e.g. presses play). Skippable at any step; "Replay tour" in the assistant.
 */

import { getState, update, subscribe } from "../core/store.js";
import { on } from "../core/events.js";

/** Lines by situation. `{x}` placeholders are filled from context. */
const LINES = /** @type {Record<string, string[]>} */ ({
  object: ["Ooh, {name}. Good taste.", "{name}? Let's see what it's been up to.", "{name}. Fresh sky, fresh suspicions."],
  date: ["Try moving through the dates. The universe isn't exactly known for sitting still.", "New date, same sky. Mostly.", "Time travel achieved. Well, data travel.", "Different night. Spot anything shifty?"],
  play: ["Rolling the footage. Watch the same patch of sky.", "Here we go. Eyes on anything that jumps.", "Playback on. Try not to blink. Actually, there's a mode for that."],
  pause: ["Congratulations, you've discovered the pause button.", "Paused. The universe can wait. It's had practice.", "Freeze frame. Very dramatic."],
  stop: ["Back to the first night. Rewind complete."],
  blink: ["See that? Switching between images makes moving objects easier to spot. You're welcome.", "Blink mode. If something jumps, it's either moving or misbehaving."],
  sidebyside: ["Two nights, side by side. Drag the divider if one deserves more attention.", "Side by side. Unlink the views if you want to wander separately."],
  overlay: ["Slide between the two dates and watch for a ghost.", "Overlay. Anything that smears is worth a second look."],
  difference: ["Bright or dark patches are differences. Could be change. Could be noise. Check the caveats.", "Difference image: where the nights disagree. Disagreement isn't proof, though."],
  candidate: ["That looks suspicious. Let's investigate before you start celebrating.", "Want to investigate this source? Blink two dates and see if it jumps.", "Ooh, a candidate. Candidate, not discovery. We still need evidence."],
  // A strong candidate: excitement, but scientifically careful (possible detection ≠ confirmed discovery).
  discovery: ["Wait. That actually looks interesting. I was beginning to lose hope. Still only a candidate, though.", "Okay, that one is promising. Promising, not proven. Blink it before anyone writes a press release."],
  degraded: ["Heads up: this observation is flagged for quality. Treat anything odd here with extra suspicion.", "This date has a quality flag. A jump here could be the detector, not the sky."],
  band: ["Band {n}: {wl} µm. Invisible to you, delicious to SPHEREx.", "{wl} µm. Different wavelength, different secrets.", "Band {n}. Cooler things glow brighter out here in the infrared."],
  gap: ["No data in this band. Even telescopes have off days.", "That band is a gap. I won't invent numbers for it. Promise."],
  save: ["Saved. Future you says thanks.", "Investigation saved. Very organised. I'm impressed."],
  note: ["Noted. Literally.", "Good note. Scientists live and die by these."],
  missing: ["No image for this date. The archive is quiet here.", "This observation has no usable image. Not every night cooperates."],
  processing: ["Still processing. Space is big; files are bigger.", "That image is still being prepared. Patience, explorer."],
  notComparable: ["These two nights don't match up. Comparing them would be fiction, so I won't.", "Not comparable. Different wavelengths or coverage. Pick another pair."],
  guidedWrong: ["That star isn't moving. Your imagination, however, is thriving.", "Close, but that one's sitting still. Try another."],
  guidedRight: ["Wait, that one really did move. Nice eye.", "Got it! That's the one that shifted."],
  repeated: ["You clicked the same button three times. Bold strategy.", "Third time's the charm? It does the same thing every time. I checked."],
  spectrum: ["Different infrared wavelengths reveal different things. Yes, there's more to space than pretty stars."],
  notes: ["Write down what you notice. Science is mostly careful notes and coffee."],
  learn: ["Questions are the whole point. Ask me anything."],
  welcomeBack: ["Alright, explorer. Let's see if you can find something interesting.", "Back again? Good. The sky kept changing without you."],
});

/** Spontaneous IDLE_DIALOGUE remarks. Playful, sometimes cheeky, never actually mean; never about a mistake. */
const IDLE_LINES = [
  "The universe is expanding. My patience isn't.",
  "You know, some of these stars are older than your entire species.",
  "Another day, another suspiciously bright dot.",
  "I wonder if anyone's looking back at us.",
  "Fun fact: SPHEREx sees the sky in 102 colours. You see three. No offence.",
  "Infrared is just light that's too cool for your eyes.",
  "Somewhere out there, a rock is minding its own business. For now.",
  "If a star moves and nobody blinks, did it really move?",
  "Every one of those dots is a sun or a whole galaxy. Try not to think about it too hard.",
  "I'd offer you coffee, but I'm made of pixels and optimism.",
];
const IDLE_CONTEXT = /** @type {Record<string, string[]>} */ ({
  staring: ["You've been staring at that star for a while. Should I be jealous?", "Maybe try changing the observation date. Just a revolutionary idea.", "Still there? The stars are patient. I'm slightly less so."],
  repeated: ["Space is infinite, and somehow you've managed to click the same thing twice."],
  landing: ["Go on, press the button. The universe won't explore itself."],
  spectrum: ["Somewhere in those bands is a story. It's usually about dust. It's always dust."],
  notes: ["Good scientists write things down. Great ones can read their own handwriting."],
});
const IDLE_HISTORY_KEY = "spherex.navi.idleHistory";
/**
 * One idle remark, avoiding the last few (remembered across pages for this session).
 * @param {{ route?: string, staring?: boolean, repeated?: boolean }} [ctx]
 */
export function pickIdleLine(ctx = {}) {
  /** @type {string[]} */ let recent = [];
  try { recent = JSON.parse(sessionStorage.getItem(IDLE_HISTORY_KEY) ?? "[]"); } catch { /* storage blocked */ }
  const pools = [];
  if (ctx.staring) pools.push(...IDLE_CONTEXT.staring);
  if (ctx.repeated) pools.push(...IDLE_CONTEXT.repeated);
  if (ctx.route === "/landing") pools.push(...IDLE_CONTEXT.landing);
  if (ctx.route === "/spectrum") pools.push(...IDLE_CONTEXT.spectrum);
  if (ctx.route === "/notes") pools.push(...IDLE_CONTEXT.notes);
  // Context lines are preferred when they apply; otherwise the general pool.
  const fresh = (/** @type {string[]} */ l) => l.filter(x => !recent.includes(x));
  const choice = fresh(pools).length && Math.random() < 0.6 ? fresh(pools) : fresh(IDLE_LINES).length ? fresh(IDLE_LINES) : IDLE_LINES;
  const text = choice[Math.floor(Math.random() * choice.length)];
  recent = [text, ...recent].slice(0, 8);
  try { sessionStorage.setItem(IDLE_HISTORY_KEY, JSON.stringify(recent)); } catch { /* ignore */ }
  return text;
}

/** Tour steps. `near` is the element Navi moves beside (she never flies onto it). */
const TOUR = [
  { id: "welcome", route: "/investigate", near: null, text: "Alright, explorer. Let's see if you can find something interesting." },
  { id: "viewer", route: "/investigate", near: "#viewer-wrap", text: "This is your observation window. Try not to get lost staring into the void." },
  { id: "timeline", route: "/investigate", near: ".timeline-panel", text: "These are observations taken at different times. Try playing them and watch what changes." },
  { id: "candidates", route: "/investigate", near: ".ctx-toggle[data-drawer=left]", text: "Those little circles mark possible objects worth investigating. Don't get too excited yet. We still need evidence." },
  { id: "modes", route: "/investigate", near: ".modebar", text: "Up there: Blink, Side by side, Overlay and Difference. Blink is my favourite. Try it." },
  { id: "blink", route: "/investigate", near: ".modebar", text: "See that? Switching between images makes moving objects easier to spot. You're welcome.", waitFor: "blink" },
  { id: "spectrum", route: "/spectrum", near: ".band-selector", text: "Different infrared wavelengths reveal different things. Yes, there's more to space than pretty stars.", waitFor: "route" },
];
const MAIN_FLOW = ["welcome", "viewer", "timeline", "candidates", "modes"];

/** @param {ReturnType<typeof import("./navi.js").mountNavi>} navi */
export function attachBrain(navi) {
  /** @type {Record<string, number>} */ const lastIdx = {};
  /** Pick a line without repeating the previous one for that situation. */
  function line(/** @type {string} */ key, /** @type {Record<string, any>} */ vars = {}) {
    const list = LINES[key]; if (!list?.length) return "";
    let i = Math.floor(Math.random() * list.length);
    if (list.length > 1 && i === lastIdx[key]) i = (i + 1) % list.length;
    lastIdx[key] = i;
    return list[i].replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
  }
  const react = (/** @type {string} */ key, vars = {}) => !tourActive() && navi.say(line(key, vars), { kind: "react" });

  // ---- tour -------------------------------------------------------------------------------------
  const tourState = () => getState().naviTour;
  const tourActive = () => { const t = tourState(); return !!t && !t.done && running; };
  let running = false, forced = false, stepTimer = 0;

  function markSeen(/** @type {string} */ id) {
    const t = tourState() ?? { seen: [], done: false };
    if (t.seen.includes(id)) return;
    const seen = [...t.seen, id];
    update({ naviTour: { seen, done: TOUR.every(s => seen.includes(s.id)) } });
  }
  function skipTour() { running = false; clearTimeout(stepTimer); update({ naviTour: { seen: TOUR.map(s => s.id), done: true } }); navi.say("Tour skipped. I'll still be around if you need me. Just click me.", { kind: "important", force: true, ms: 4500 }); }

  /** Show one tour step: move beside its target, whisper it, wait for Next / dismissal / the action. */
  function show(/** @type {typeof TOUR[number]} */ step) {
    if (navi.route() !== step.route) return;
    if (!forced && getState().naviHints === "off") { running = false; return; }
    if (step.near) navi.goNear(step.near, 12000);
    const isLast = step.id === "modes" || step.id === "blink" || step.id === "spectrum";
    setTimeout(() => {
      const actions = [{ label: isLast ? "Got it" : "Next ›", run: () => advance(step.id) }, { label: "Skip tour", run: skipTour }];
      navi.onWhisperClosed = () => advance(step.id);
      navi.say(step.text, { kind: "tour", actions, force: forced });
    }, step.near ? 900 : 300);
  }
  /** Move to the next step of the main flow (after a pause, never while the user studies an image). */
  function advance(/** @type {string} */ id) {
    markSeen(id);
    navi.onWhisperClosed = null;
    clearTimeout(stepTimer);
    const i = MAIN_FLOW.indexOf(id);
    const next = i >= 0 ? MAIN_FLOW.slice(i + 1).map(n => TOUR.find(s => s.id === n)).find(s => s && !tourState()?.seen.includes(s.id)) : null;
    if (!next) { if (i >= 0) running = false; return; }
    const wait = () => { if (!running) return; if (navi.studying || navi.panelOpen) { stepTimer = window.setTimeout(wait, 2500); return; } show(next); };
    stepTimer = window.setTimeout(wait, 1600);
  }
  function startTour(/** @type {boolean} */ force = false) {
    forced = force; running = true;
    if (force) update({ naviTour: { seen: [], done: false } });
    const first = TOUR.find(s => MAIN_FLOW.includes(s.id) && !tourState()?.seen.includes(s.id));
    if (navi.route() !== "/investigate") { if (force) window.location.hash = "#/investigate"; return; }
    if (first) setTimeout(() => show(first), 400);
  }
  navi.onReplayTour = () => startTour(true);

  // ---- arrival ------------------------------------------------------------------------------------
  function onArrive() {
    const t = tourState();
    if (navi.route() === "/investigate" && (!t || !t.done) && (navi.arrived || !t)) setTimeout(() => startTour(false), navi.arrived ? 1100 : 1600);
    else if (navi.route() === "/investigate" && t?.done) setTimeout(() => navi.say(line("welcomeBack"), { kind: "ambient" }), 1800);
    else setTimeout(() => routeLine(), 1500);
  }
  function routeLine() {
    const r = navi.route(), t = tourState();
    const spectrumStep = TOUR.find(s => s.id === "spectrum");
    if (r === "/spectrum" && spectrumStep && t && !t.seen.includes("spectrum") && getState().naviHints !== "off") { running = true; show(spectrumStep); markSeen("spectrum"); return; }
    const key = r === "/spectrum" ? "spectrum" : r === "/notes" ? "notes" : r === "/learn" ? "learn" : "";
    if (key) navi.say(line(key), { kind: "ambient" });
  }

  // ---- reactions to the investigation ----------------------------------------------------------------
  // Only react to changes the USER just made (not to state the page sets while loading).
  let lastUserAct = 0;
  ["pointerdown", "keydown", "click"].forEach(t => document.addEventListener(t, e => { if (!/** @type {Element} */ (e.target).closest?.(".navi, .navi-panel, .navi-whisper")) lastUserAct = Date.now(); }, { capture: true, passive: true }));
  const byUser = () => Date.now() - lastUserAct < 1500;
  let playing = false, lastDateLine = 0, lastBandLine = 0, dateTimer = 0;
  subscribe((s, patch) => {
    const r = navi.route();
    if ("objectId" in patch && patch.objectId && byUser()) {
      setTimeout(() => { const name = /** @type {HTMLSelectElement|null} */ (document.querySelector("#target-select"))?.selectedOptions[0]?.textContent; react("object", { name: name ?? "that one" }); }, 900);
    }
    if (!byUser()) return;
    if ("observationId" in patch && r === "/investigate" && s.compareMode === "view" && !playing && !("objectId" in patch)) {
      // One remark per date change: an honest warning if the observation is quality-flagged, otherwise
      // (now and then) a timeline line. Decided once the viewer has shown the new date.
      clearTimeout(dateTimer);
      dateTimer = window.setTimeout(() => {
        if (/quality:/i.test(document.querySelector("#viewer .viewer-hud")?.textContent ?? "")) react("degraded");
        else if (Date.now() - lastDateLine > 20000) { lastDateLine = Date.now(); react("date"); }
      }, 350);
    }
    if ("compareMode" in patch && r === "/investigate") {
      const blinkStep = TOUR.find(t => t.id === "blink");
      if (s.compareMode === "blink" && blinkStep && !tourState()?.seen.includes("blink") && getState().naviHints !== "off") {
        running = true; markSeen("modes"); show(blinkStep); markSeen("blink");
      } else if (s.compareMode !== "view") react(s.compareMode);
    }
    if ("candidateId" in patch && patch.candidateId && r === "/investigate") {
      const score = Number(document.querySelector("#cand-panel [aria-selected=true] .score")?.textContent ?? "0");
      react(score >= 0.8 ? "discovery" : "candidate");
    }
    if ("bandIndex" in patch && r === "/spectrum" && Date.now() - lastBandLine > 15000) {
      lastBandLine = Date.now();
      const wl = document.querySelector(".bs-wl")?.textContent?.replace(/[^\d.]/g, "") ?? "";
      setTimeout(() => react("band", { n: s.bandIndex, wl }), 200);
    }
    if ("notes" in patch && (patch.notes?.length ?? 0) > 0) react("note");
  });

  on(e => {
    switch (e.type) {
      case "focus_mode": if (e.on) navi.hideWhisper(); break;
      case "play": playing = true; if (tourActive() && !tourState()?.seen.includes("timeline")) { navi.hideWhisper(true); } else react("play"); break;
      case "pause": playing = false; react("pause"); break;
      case "stop": playing = false; react("stop"); break;
      case "save": react("save"); break;
      case "image_missing": react("missing"); break;
      case "image_processing": react("processing"); break;
      case "not_comparable": react("notComparable"); break;     // the verdict is already on screen; muting silences her
      case "gap_band": react("gap"); break;
      case "guided_wrong": react("guidedWrong"); break;
      case "guided_right": navi.say(line("guidedRight"), { kind: "important" }); break;
    }
  });

  // "You clicked the same button three times." (at most once a minute)
  /** @type {{ el: Element|null, n: number, t: number }} */ const clicks = { el: null, n: 0, t: 0 };
  let lastRepeat = 0;
  document.addEventListener("click", e => {
    const b = /** @type {Element} */ (e.target).closest?.("button, [role=radio], a.tab");
    if (!b || b.closest(".navi-panel, .navi, .navi-whisper")) return;
    const now = Date.now();
    if (b === clicks.el && now - clicks.t < 3000) clicks.n++; else { clicks.el = b; clicks.n = 1; }
    clicks.t = now;
    if (clicks.n === 2) lastRepeatSeen = now;
    if (clicks.n === 3 && now - lastRepeat > 60000) { lastRepeat = now; react("repeated"); }
  }, true);
  // (Spontaneous remarks during quiet spells come only from IDLE_DIALOGUE; see pickIdleLine.)

  // IDLE_DIALOGUE content: navi.js decides when (25% on entering IDLE, cooldown); this decides what.
  let lastRepeatSeen = 0;
  navi.idleLines = () => pickIdleLine({
    route: navi.route(),
    staring: navi.route() === "/investigate" && Date.now() - lastUserAct > 20000 && Date.now() - lastUserAct < 180000,
    repeated: Date.now() - lastRepeatSeen < 60000,
  });

  onArrive();

  let firstRoute = true, routeTimer = 0;    // the arrival greeting already covers the first route
  return {
    onRoute() { if (firstRoute) { firstRoute = false; return; } clearTimeout(stepTimer); if (running && navi.route() !== "/investigate") running = false; clearTimeout(routeTimer); routeTimer = window.setTimeout(routeLine, 900); },
    startTour, skipTour,
  };
}
