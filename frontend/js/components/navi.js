// @ts-check
/**
 * Navi, the research companion: her body (flight), her voice (whispers) and the assistant.
 * What she says and when is decided by js/components/naviBrain.js.
 *
 * FLIGHT. Navi is anchored to the viewport (position: fixed) and flown by a steering model in a
 * requestAnimationFrame loop: she has a position and a velocity; each frame she accelerates toward
 * her destination (capped speed and acceleration) and slows smoothly on arrival. Changing the
 * destination mid-flight just bends her path, so she never jumps. A gentle hover (a few px of
 * layered sine motion) keeps her alive while idle, and she leans slightly into turns.
 *
 * WHERE SHE MAY GO. Destinations are picked from the free space actually on screen: a grid of
 * candidate points, kept clear of the viewport edges and corners, rejecting any spot where her box
 * would cover imagery, controls, inputs, navigation or text, and any destination whose straight
 * path would cross imagery or controls. She prefers medium-distance hops and places she hasn't just
 * been. Scrolling, resizing, switching tools or layout changes trigger a re-check: if her spot is
 * no longer free she flies (smoothly) to the nearest free one. Nothing is ever placed at an
 * unchecked random position.
 *
 * SPEAKING. Before a whisper she settles where the bubble fits beside her without covering
 * anything, slows to a hover, shows the bubble (which follows her base position, so it never
 * shakes), then pauses briefly after it closes and resumes exploring. Clicking her pauses her and
 * opens the assistant.
 *
 * SIZE. One size everywhere: 60 CSS px tall on desktop, 52 px on small screens, aspect ratio of the
 * approved sprite preserved, never scaled by containers. The sprite itself is never altered.
 *
 * Reduced motion: no flight, hover, lean or particles; she is placed directly in free space.
 * The browser never calls an LLM: replies come from api.sendChatMessage (mock: scripted).
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, subscribe, toQuery } from "../core/store.js";
import { esc } from "../core/dom.js";
import { go, currentLocation } from "../core/router.js";
import { placeBeside, rectsOf, overlap } from "../core/placement.js";
import { createStateMachine, IDLE, MOVING, TOUCHED, IDLE_DIALOGUE, MISC } from "./naviStates.js";

const SPRITE_W = 88, SPRITE_H = 84;                   // approved asset
const HANDOFF_KEY = "spherex.navi.handoff";
const COMMENT_LABEL = { all: "On", fewer: "Fewer", off: "Off" };
const EDGE = 18;                                      // keep this far from viewport edges
const PAD = 8;                                        // clearance around her when checking free space

// What she must not cover. Imagery/art (critical) and controls are never crossed; text is avoided at rest.
const CONTROLS = "a[href], button:not(.navi-body), input, select, textarea, summary, [role=slider], [role=radio], [role=tab], .tab";
const TEXT = "h1, h2, h3, h4, p:not(.navi-whisper), li, dt, dd, label, figcaption, .badge, .chip, .ctx-summary, .bs-axis, .verdict-chip";

/**
 * @typedef {Object} NaviOptions
 * @property {boolean} [inApp]        true inside app.html (hash routes); false on the landing page
 * @property {string} [avoid]         extra selector of things she and her whispers must not cover
 * @property {string} [panelAvoid]    selector of things the assistant panel should not cover
 * @property {string} [critical]      selector of imagery / art she must never cover or fly across
 * @typedef {{ label: string, run: () => void }} WhisperAction
 * @typedef {{ kind?: "important"|"tour"|"react"|"ambient", actions?: WhisperAction[], ms?: number, force?: boolean }} SayOptions
 * @typedef {{ left: number, top: number, right: number, bottom: number }} Box
 */

/** @param {NaviOptions} [opts] */
export function mountNavi(opts = {}) {
  const inApp = !!opts.inApp;
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- DOM ------------------------------------------------------------------------------------
  const navi = document.createElement("div");
  navi.className = "navi";
  navi.innerHTML = `
    <div class="navi-lean">
      <span class="navi-trail" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
      <button class="navi-body" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="navi-panel"
        aria-label="Navi, your research companion. Open the assistant.">
        <img src="assets/navi/navi.png" alt="" width="${SPRITE_W}" height="${SPRITE_H}" draggable="false">
      </button>
    </div>`;
  const whisper = document.createElement("div");
  whisper.className = "navi-whisper"; whisper.setAttribute("role", "status"); whisper.hidden = true;
  const panel = document.createElement("section");
  panel.className = "navi-panel"; panel.id = "navi-panel"; panel.hidden = true;
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Navi, research companion");
  panel.innerHTML = `
    <header class="np-head" title="Drag to move">
      <button class="np-grip" type="button" aria-label="Move the chat window (arrow keys)" title="Drag to move">⠿</button>
      <strong class="pixel np-name"><span class="np-star" aria-hidden="true">✦</span><span class="np-label"> Navi</span></strong>
      <span class="np-sub">${isMock() ? "Illustrative replies" : "Research companion"}</span>
      <button class="np-gear" type="button" aria-expanded="false" aria-controls="np-settings" aria-label="Settings" title="Settings">⚙</button>
      <button class="np-expand" type="button" aria-pressed="false" aria-label="Larger reading area" title="Larger reading area">⤢</button>
      <button class="np-close" type="button" aria-label="Close chat" title="Close">✕</button>
    </header>
    <div class="np-settings" id="np-settings" hidden>
      <div class="np-seg" role="radiogroup" aria-label="Explanation level">
        <button type="button" role="radio" data-mode="beginner" aria-checked="true">Beginner</button>
        <button type="button" role="radio" data-mode="advanced" aria-checked="false">Advanced</button>
      </div>
      <div class="np-seg" role="radiogroup" aria-label="Navi's comments (questions always work)">
        <span class="np-seg-label" aria-hidden="true">Comments</span>
        ${Object.entries(COMMENT_LABEL).map(([k, t]) => `<button type="button" role="radio" data-hints="${k}" aria-checked="false">${t}</button>`).join("")}
      </div>
      <button type="button" class="np-link" data-tour="replay">Replay tour</button>
    </div>
    <p class="np-tip" hidden></p>
    <div class="np-log" aria-live="polite" tabindex="0" aria-label="Conversation"></div>
    <div class="np-sugg" aria-label="Suggested questions"></div>
    <form class="np-form">
      <label class="sr-only" for="navi-input">Ask Navi</label>
      <span class="np-cloud"><span class="np-cloud-in"><input class="input" id="navi-input" placeholder="Ask about what you see…" autocomplete="off" maxlength="300"></span></span>
      <button class="btn" type="submit">Ask</button>
    </form>`;
  document.body.append(navi, whisper, panel);

  const body = /** @type {HTMLButtonElement} */ (navi.querySelector(".navi-body"));
  const lean = /** @type {HTMLElement} */ (navi.querySelector(".navi-lean"));
  const $ = (/** @type {string} */ s) => /** @type {HTMLElement} */ (panel.querySelector(s));
  const log = $(".np-log"), input = /** @type {HTMLInputElement} */ ($("#navi-input")), sendBtn = /** @type {HTMLButtonElement} */ ($(".np-form .btn"));
  const route = () => (inApp ? currentLocation().path : "/landing");
  const nav = (/** @type {string} */ path) => {
    if (inApp) return go(path);
    const q = toQuery(); window.location.href = `app.html#${path}${q ? "?" + q : ""}`;
  };

  // ---- size: one consistent size --------------------------------------------------------------
  let W = 63, H = 60;
  function measureSize() {
    H = document.documentElement.clientWidth < 600 ? 52 : 60;
    W = Math.round(H * SPRITE_W / SPRITE_H);
    navi.style.width = `${W}px`; navi.style.height = `${H}px`;
  }
  measureSize();

  // ---- free space -------------------------------------------------------------------------------
  const vw = () => document.documentElement.clientWidth, vh = () => window.innerHeight;
  const own = [navi, whisper, panel];
  /** Obstacles currently on screen, by severity. */
  /** Her open dialogue, which she does not rest on (it floats beside her). */
  const dialogueBox = () => {
    if (panel.hidden) return [];
    const r = panel.getBoundingClientRect();
    return [{ left: r.left - 4, top: r.top - 4, right: r.right + 4, bottom: r.bottom + 4 }];   // + her own 8 px < the 14 px it keeps from her
  };
  function obstacles() {
    const sel = (/** @type {string} */ s) => rectsOf(s, own).filter(r => r.bottom > 0 && r.top < vh() && r.right > 0 && r.left < vw());
    return {
      hard: sel([opts.critical, CONTROLS].filter(Boolean).join(", ")),                 // never at rest, never crossed
      soft: [...sel([TEXT, opts.avoid].filter(Boolean).join(", ")), ...dialogueBox()],   // never at rest
      critical: sel(opts.critical ?? ""),
    };
  }
  const boxAt = (/** @type {number} */ x, /** @type {number} */ y, pad = PAD) => ({ left: x - pad, top: y - pad, right: x + W + pad, bottom: y + H + pad });
  const clear = (/** @type {Box} */ b, /** @type {Box[]} */ rs) => rs.every(r => overlap(b, r) === 0);
  const inBounds = (/** @type {number} */ x, /** @type {number} */ y) => x >= EDGE && y >= EDGE && x + W <= vw() - EDGE && y + H <= vh() - EDGE;

  /** Straight path from a to b avoids imagery and controls (sampled). */
  function pathClear(/** @type {{x:number,y:number}} */ a, /** @type {{x:number,y:number}} */ b, /** @type {Box[]} */ hard) {
    const n = Math.max(4, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / 24));
    for (let i = 1; i < n; i++) { const t = i / n; if (!clear(boxAt(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, 2), hard)) return false; }
    return true;
  }

  /** All free resting points on screen (top-left of her box), on a 24 px grid. */
  function freePoints(/** @type {ReturnType<typeof obstacles>} */ o) {
    const pts = [], step = 24, all = [...o.hard, ...o.soft];
    for (let y = EDGE; y + H <= vh() - EDGE; y += step)
      for (let x = EDGE; x + W <= vw() - EDGE; x += step)
        if (clear(boxAt(x, y), all)) pts.push({ x, y });
    return pts;
  }

  /**
   * Choose a destination. `mode`:
   *  - "explore": a reachable free point at a comfortable distance, not where she just was
   *  - "nearest": the closest reachable free point (escape when her spot stops being free)
   *  - "far":     a reachable free point far from a given focus point (retreat while the user studies)
   *  - "near":    the free point closest to a given focus point (tour)
   * Returns null when the screen has no free space at all.
   * @param {"explore"|"nearest"|"far"|"near"} mode @param {{x:number,y:number}|null} [focus]
   */
  function choose(mode, focus = null) {
    const o = obstacles(), pts = freePoints(o);
    if (!pts.length) return null;
    const here = { x: pos.x, y: pos.y };
    const d = (/** @type {{x:number,y:number}} */ p, /** @type {{x:number,y:number}} */ q) => Math.hypot(p.x - q.x, p.y - q.y);
    const centre = (/** @type {{x:number,y:number}} */ p) => ({ x: p.x + W / 2, y: p.y + H / 2 });
    /** @type {{x:number,y:number}[]} */ let ordered;
    if (mode === "nearest") ordered = pts.sort((a, b) => d(a, here) - d(b, here));
    else if (mode === "near" && focus) ordered = pts.sort((a, b) => d(centre(a), focus) - d(centre(b), focus));
    else if (mode === "far" && focus) ordered = pts.sort((a, b) => d(b, focus) - d(a, focus));
    else {
      // explore: prefer a comfortable hop (120–480 px), new places, and some distance from the edges;
      // a weighted random order (key = u^(1/w)) keeps her slightly unpredictable.
      ordered = pts.map(p => {
        const dist = d(p, here), hop = dist < 60 ? 0.03 : dist < 120 ? 0.4 : dist < 480 ? 1 : 0.55;
        const novelty = recent.every(r => d(p, r) > 90) ? 1 : 0.1;
        const edge = Math.min(p.x, p.y, vw() - p.x - W, vh() - p.y - H) < 50 ? 0.45 : 1;
        return { p, k: Math.random() ** (1 / Math.max(1e-6, hop * novelty * edge)) };
      }).sort((a, b) => b.k - a.k).map(c => c.p);
    }
    // First candidate she can actually reach without crossing imagery or controls (checked lazily).
    let tested = 0;
    for (const p of ordered) { if (pathClear(here, p, o.hard)) return p; if (++tested >= 80) break; }
    return ordered[0];                                           // boxed in: any free point; she steers out
  }
  /** @type {{x:number,y:number}[]} */ const recent = [];

  // ---- flight (steering model) ------------------------------------------------------------------
  let pos = { x: vw() - W - 40, y: 80 }, vel = { x: 0, y: 0 }, target = { ...pos };
  let maxSpeed = 95, leanDeg = 0, t0 = performance.now(), last = t0, running = false;
  // Behaviour lives in a separate state machine (naviStates.js); flight below only reads it.
  const fsm = createStateMachine({ onChange: (from, to) => {
    publish();
    if (from === IDLE) clearTimeout(idleTalkTimer);
    // A higher-priority state pre-empts an idle remark: drop that bubble (no overlapping dialogue).
    if (from === IDLE_DIALOGUE && to !== IDLE && currentKind === "idle") dropBubble();
    if (to === IDLE) onEnterIdle();
  } });
  let idleUntil = 0, holdUntil = 0, studyUntil = 0, quietTimer = 0, idleTalkTimer = 0, speaking = false, lastUserAct = 0;
  ["pointerdown", "keydown", "wheel"].forEach(t => document.addEventListener(t, e => {
    if (!/** @type {Element} */ (e.target).closest?.(".navi, .navi-whisper, .navi-panel")) lastUserAct = Date.now();
  }, { capture: true, passive: true }));
  const phase = [Math.random() * 6, Math.random() * 6, Math.random() * 6];
  const studying = () => Date.now() < studyUntil || document.body.classList.contains("focus-mode");

  function setTarget(/** @type {{x:number,y:number}|null} */ p, speed = 95) {
    if (!p) return;
    target = { x: p.x, y: p.y }; maxSpeed = speed;
    recent.unshift({ ...p }); recent.length = Math.min(recent.length, 4);
    publish();
    if (reduced()) { pos = { ...target }; vel = { x: 0, y: 0 }; render(0); }
    start();
  }
  function publish() { navi.dataset.rest = `${Math.round(target.x)},${Math.round(target.y)},${W},${H}`; navi.dataset.state = fsm.state; }

  function start() { if (!running && !reduced()) { running = true; last = performance.now(); requestAnimationFrame(tick); } }
  // Watchdog: if animation frames stop arriving while the page is visible (some embedded or
  // throttled contexts), keep her loop going on a timer instead of freezing mid-flight.
  let lastFrameAt = performance.now();
  setInterval(() => { const now = performance.now(); if (running && !document.hidden && now - lastFrameAt > 60) step(now); }, 30);

  // One clock for both drivers (frame timestamps can be older than performance.now()).
  function tick() { step(performance.now()); requestAnimationFrame(tick); }
  function step(/** @type {number} */ now) {
    if (!running) return;
    lastFrameAt = now;
    const dt = Math.max(0, Math.min(0.1, (now - last) / 1000)); last = now;    // never backwards; no leaps after a background tab
    if (dt === 0) return;
    if (!Number.isFinite(pos.x) || !Number.isFinite(pos.y)) { pos = { ...target }; vel = { x: 0, y: 0 }; }
    // Arrive: desired speed falls off inside the slowing radius, so she eases in.
    const dx = target.x - pos.x, dy = target.y - pos.y, dist = Math.hypot(dx, dy);
    const slow = 150, desired = dist > 0.5 ? Math.min(maxSpeed, maxSpeed * (dist / slow)) : 0;
    const want = dist > 0.5 ? { x: (dx / dist) * desired, y: (dy / dist) * desired } : { x: 0, y: 0 };
    const accel = 160, sx = want.x - vel.x, sy = want.y - vel.y, s = Math.hypot(sx, sy), lim = accel * dt;
    if (s > 0) { vel.x += s > lim ? (sx / s) * lim : sx; vel.y += s > lim ? (sy / s) * lim : sy; }
    pos.x += vel.x * dt; pos.y += vel.y * dt;
    // Never leave the viewport, even if the layout changed under her.
    pos.x = Math.min(vw() - W - 4, Math.max(4, pos.x)); pos.y = Math.min(vh() - H - 4, Math.max(4, pos.y));
    render(now);
    behave(dist, Math.hypot(vel.x, vel.y));
  }

  /** Paint: base position + a gentle hover, leaning into horizontal motion. */
  function render(/** @type {number} */ now) {
    const t = (now - t0) / 1000, still = reduced();
    const calm = speaking || fsm.state === TOUCHED ? 0.45 : 1;
    const hx = still ? 0 : calm * (3 * Math.sin(t * 0.9 + phase[0]) + 1.5 * Math.sin(t * 2.3 + phase[1]));
    const hy = still ? 0 : calm * (4 * Math.sin(t * 1.3 + phase[2]) + 1.5 * Math.sin(t * 2.9 + phase[0]));
    navi.style.transform = `translate3d(${(pos.x + hx).toFixed(2)}px, ${(pos.y + hy).toFixed(2)}px, 0)`;
    leanDeg += ((still ? 0 : Math.max(-9, Math.min(9, vel.x / 12))) - leanDeg) * 0.08;
    lean.style.transform = `rotate(${leanDeg.toFixed(2)}deg)`;
    navi.classList.toggle("is-moving", Math.hypot(vel.x, vel.y) > 25);
    if (!whisper.hidden) followWhisper();
  }

  /** State transitions that depend on flight, checked once per frame. */
  function behave(/** @type {number} */ dist, /** @type {number} */ speed) {
    const now = Date.now(), arrived = dist < 2 && speed < 6, st = fsm.state;
    if (st === MOVING && arrived) fsm.release(MOVING);                                   // MOVING → IDLE
    else if (st === IDLE && now > idleUntil && !studying() && !document.hidden) {        // IDLE → MOVING after 10–15 s
      const p = choose("explore");
      if (p && fsm.request(MOVING)) setTarget(p, 70 + Math.random() * 50);
      else idleUntil = now + 4000;
    } else if (st === MISC && !speaking && !pendingSay && now > holdUntil) fsm.release(MISC);  // MISC done → IDLE
    if (pendingSay && arrived) { const p = pendingSay; pendingSay = null; showWhisper(p.text, p.o); }
  }

  // ---- IDLE_DIALOGUE: occasional spontaneous remarks -----------------------------------------------
  /** @type {(() => string)|null} */ let idleLineSource = null;
  let lastIdleLine = 0;
  const IDLE_COOLDOWN = { all: 45000, fewer: 150000 };
  function onEnterIdle() {
    idleUntil = Date.now() + 10000 + Math.random() * 5000;
    clearTimeout(idleTalkTimer);
    if (Math.random() < 0.25) idleTalkTimer = window.setTimeout(tryIdleDialogue, 900 + Math.random() * 1500);
  }
  /** Say one idle line where she is (she never moves just to chat), only if nothing else is going on. */
  function tryIdleDialogue() {
    const pref = getState().naviHints;
    if (fsm.state !== IDLE || pref === "off" || !idleLineSource) return;
    if (Date.now() - Math.max(lastIdleLine, lastSaid) < IDLE_COOLDOWN[pref]) return;
    if (studying() || document.hidden || !panel.hidden || Date.now() - lastUserAct < 4000) return;
    const text = idleLineSource(); if (!text) return;
    fillWhisper(text, {});
    // Only where she already is. On small screens the bubble may cover plain text, never controls or imagery.
    if (bubbleFit(target, whisper.offsetWidth, whisper.offsetHeight, obstacles()).tier > (vw() < 600 ? 1 : 0)) { whisper.hidden = true; whisper.style.visibility = ""; return; }
    if (!fsm.request(IDLE_DIALOGUE)) { whisper.hidden = true; whisper.style.visibility = ""; return; }
    lastIdleLine = Date.now(); lastSay = null; speaking = true;          // own cooldown; never throttles MISC
    showWhisper(text, { kind: "idle" });
  }

  /** Re-check her spot after scroll / resize / layout change; fly to the nearest free spot if needed. */
  function recheck() {
    measureSize();
    const o = obstacles(), b = boxAt(target.x, target.y, 2);
    if (inBounds(target.x, target.y) && clear(b, [...o.hard, ...o.soft])) return;
    const free = choose("nearest");
    // Nothing free on this screen and she already sits at the least crowded spot: stay put rather
    // than hopping between near-equal fallbacks every check. She moves as soon as a free spot opens.
    if (!free && crowdedSpot && crowdedSpot.x === target.x && crowdedSpot.y === target.y && inBounds(target.x, target.y)) return;
    if (fsm.is(IDLE_DIALOGUE)) hideWhisper(true);           // don't chat from a spot that just got covered
    if (fsm.is(IDLE)) fsm.request(MOVING);                  // same flight, now as a MOVING state (→ IDLE on arrival)
    crowdedSpot = free ? null : leastCrowded(o);
    setTarget(free ?? /** @type {{x:number,y:number}} */ (crowdedSpot), 140);
  }
  /** @type {{x:number,y:number}|null} */ let crowdedSpot = null;
  /** No free space at all: the on-screen spot that covers the least (imagery worst, then controls,
   *  then text), keeping away from corners so she is never pinned there. */
  function leastCrowded(/** @type {ReturnType<typeof obstacles>} */ o) {
    let best = { x: vw() / 2 - W / 2, y: EDGE }, bestCost = Infinity;
    for (let y = EDGE; y + H <= vh() - EDGE; y += 32) for (let x = EDGE; x + W <= vw() - EDGE; x += 32) {
      const b = boxAt(x, y, 0), cornerish = (x < 80 || x > vw() - W - 80) && (y < 80 || y > vh() - H - 80);
      const cost = o.critical.reduce((s, r) => s + overlap(b, r), 0) * 20 + o.hard.reduce((s, r) => s + overlap(b, r), 0) * 4
        + o.soft.reduce((s, r) => s + overlap(b, r), 0) + (cornerish ? 4000 : 0) + Math.hypot(x - pos.x, y - pos.y) * 0.5;
      if (cost < bestCost) { bestCost = cost; best = { x, y }; }
    }
    return best;
  }
  let recheckTimer = 0;
  const scheduleRecheck = (ms = 220) => { clearTimeout(recheckTimer); recheckTimer = window.setTimeout(recheck, ms); };
  addEventListener("scroll", () => { scheduleRecheck(); }, { passive: true });
  addEventListener("resize", () => { measureSize(); scheduleRecheck(150); });
  new ResizeObserver(() => scheduleRecheck(250)).observe(document.body);
  // Fallback for environments that coalesce or drop scroll / resize / layout events.
  let lastY = scrollY, lastW = vw(), lastH = vh();
  setInterval(() => {
    if (Math.abs(scrollY - lastY) > 30 || vw() !== lastW || vh() !== lastH) { lastY = scrollY; lastW = vw(); lastH = vh(); measureSize(); scheduleRecheck(0); return; }
    // Content can appear under her (a view finishing loading). Make sure her spot is still free.
    if (!panel.hidden) return;
    const o = obstacles(), covered = !clear(boxAt(target.x, target.y, 2), [...o.hard, ...o.soft]);
    if (!covered) return;
    if ((fsm.is(IDLE, MOVING) && whisper.hidden) || fsm.is(IDLE_DIALOGUE)) scheduleRecheck(0);
    else if (fsm.is(MISC) && lastSay && !lastSay.moved && whisper.classList.contains("is-shown")) {
      // Mid-sentence and something appeared under her: close the bubble, move, and say it again there.
      // Only once per line: on a crowded screen the best spot may still touch something, and
      // repeating would make the bubble blink on and off.
      const again = lastSay; hideWhisper(false);
      setTimeout(() => { if (panel.hidden && !fsm.is(TOUCHED)) { say(again.text, { ...again.o, force: true }); if (lastSay) lastSay.moved = true; } }, 250);
    }
  }, 700);

  /** User is inspecting imagery: retreat away from it and keep quiet for a while. */
  function study(/** @type {Element|null} */ el) {
    { const g = getState().guided; if (g && !g.done) return; }               // clicking the image IS the guided activity: she stays put and answers
    if (currentKind === "tour" && !whisper.hidden && whisper.classList.contains("is-shown")) return;   // a tour step stays put while they poke at the image
    const was = studying(); studyUntil = Date.now() + 20000;
    hideWhisper(true);
    navi.classList.add("is-quiet");
    clearTimeout(quietTimer); quietTimer = window.setTimeout(() => navi.classList.remove("is-quiet"), 20000);
    if (was || !panel.hidden || !el || fsm.is(TOUCHED)) return;
    const r = el.getBoundingClientRect();
    // Retreat is an ordinary flight (MOVING); IDLE won't start new flights or chatter until study ends.
    holdUntil = 0; fsm.request(MOVING, { force: true });
    setTarget(choose("far", { x: (r.left + r.right) / 2, y: (r.top + r.bottom) / 2 }), 120);
  }
  const onInspect = (/** @type {Event} */ e) => { const el = opts.critical ? /** @type {Element} */ (e.target).closest?.(opts.critical) : null; if (el) study(el); };
  document.addEventListener("pointerdown", onInspect, true);
  document.addEventListener("wheel", onInspect, { capture: true, passive: true });

  /** Fly to the free point closest to an element (never onto it) and hold there. */
  function goNear(/** @type {string|Element} */ targetEl, holdMs = 9000) {
    const el = typeof targetEl === "string" ? document.querySelector(targetEl) : targetEl; if (!el) return;
    const r = el.getBoundingClientRect();
    const p = choose("near", { x: Math.min(vw(), Math.max(0, (r.left + r.right) / 2)), y: Math.min(vh(), Math.max(0, (r.top + r.bottom) / 2)) });
    if (!p || !fsm.request(MISC)) return;                   // a tutorial step is a MISC event
    holdUntil = Date.now() + holdMs; setTarget(p, 200);
  }

  // ---- whispers: settle, then speak --------------------------------------------------------------
  let whisperTimer = 0, lastSaid = 0, lastTip = "", currentKind = "";
  const RANK = { idle: -1, ambient: 0, react: 1, tour: 2, important: 3 };
  /** @type {{ text: string, o: SayOptions }|null} */ let pendingSay = null;
  /** @type {{ text: string, o: SayOptions, moved?: boolean }|null} */ let lastSay = null;
  /** @type {WhisperAction[]} */ let whisperActions = [];
  /** @type {(() => void)|null} */ let onWhisperClosed = null;
  let side = /** @type {"left"|"right"|"top"|"bottom"} */ ("left"), offset = { x: 0, y: 0 };

  /**
   * Say something in a bubble beside her. Unsolicited lines respect the comment setting; a
   * lower-priority line never interrupts a higher one. She first settles where the bubble fits.
   * @param {string} text @param {SayOptions} [o] @returns {boolean} accepted
   */
  function say(text, o = {}) {
    const kind = o.kind ?? "react";
    if (!text) return false;
    lastTip = text; renderTip();
    if (fsm.is(TOUCHED)) return false;                      // nothing talks over the open assistant, forced or not
    if (!panel.hidden && !o.force) return false;
    const pref = getState().naviHints, now = Date.now();
    if (!o.force && kind !== "important") {
      if (document.body.classList.contains("focus-mode")) return false;
      if (pref === "off") return false;
      if (pref === "fewer" && (kind === "ambient" || (kind === "react" && now - lastSaid < 45000))) return false;
      if (kind === "react" && now - lastSaid < 2500) return false;
      if (studying() && kind !== "tour") return false;
    }
    const showing = !whisper.hidden && whisper.classList.contains("is-shown");
    if (showing && RANK[/** @type {keyof typeof RANK} */ (currentKind)] > RANK[kind] && !o.force) return false;
    // Contextual dialogue is a MISC event: it pre-empts idle chatter and wandering, never the open assistant.
    if (!fsm.request(MISC) && !o.force) return false;
    lastSaid = now; lastSay = { text, o };
    // Measure the bubble, then make sure there is room beside her for it.
    fillWhisper(text, o);
    const spot = speakingSpot();
    speaking = true; maxSpeed = 60;
    if (spot && (Math.abs(spot.x - pos.x) > 2 || Math.abs(spot.y - pos.y) > 2) && !reduced()) {
      // Fly (briskly but smoothly) to where the bubble fits; the bubble appears when she gets there.
      const speed = 220, travel = Math.hypot(spot.x - pos.x, spot.y - pos.y) / speed;
      setTarget(spot, speed); pendingSay = { text, o };
      setTimeout(() => { if (pendingSay?.text === text) { pendingSay = null; showWhisper(text, o); } }, Math.max(8000, travel * 2500));   // safety net only
    } else { if (spot) setTarget(spot, 60); showWhisper(text, o); }
    return true;
  }
  function fillWhisper(/** @type {string} */ text, /** @type {SayOptions} */ o) {
    whisper.innerHTML = `<span class="nw-text">${esc(text)}</span>${o.actions?.length ? `<span class="nw-actions">${o.actions.map((a, i) => `<button type="button" data-i="${i}">${esc(a.label)}</button>`).join("")}</span>` : ""}`;
    whisperActions = o.actions ?? [];
    whisper.style.visibility = "hidden"; whisper.hidden = false;              // measure without showing
  }
  /**
   * Where the bubble goes beside a resting point, in tiers: 0 = covers nothing; 1 = covers no imagery
   * or controls (at most some text); 2 = least overlap (small screens with no room at all).
   */
  function bubbleFit(/** @type {{x:number,y:number}} */ pt, /** @type {number} */ w, /** @type {number} */ h, /** @type {ReturnType<typeof obstacles>} */ o) {
    const a = { left: pt.x, top: pt.y, right: pt.x + W, bottom: pt.y + H }, po = { sides: /** @type {any} */ (["left", "right", "top", "bottom"]), gap: vw() < 600 ? 10 : 18 };   // room for the trailing motes (tighter on phones)
    const all = placeBeside(a, w, h, [...o.hard, ...o.soft], po); if (all.clean) return { p: all, tier: 0 };
    const hard = placeBeside(a, w, h, o.hard, po); if (hard.clean) return { p: hard, tier: 1 };
    return { p: hard, tier: 2 };
  }
  /** A spot (her current one if possible) with the best bubble tier, nearest first, reachable. */
  function speakingSpot() {
    const o = obstacles(), w = whisper.offsetWidth, h = whisper.offsetHeight, avoid = [...o.hard, ...o.soft];
    const hereOk = inBounds(target.x, target.y) && clear(boxAt(target.x, target.y), avoid);
    const hereTier = hereOk ? bubbleFit(target, w, h, o).tier : 3;
    if (hereTier === 0) return target;
    const pts = freePoints(o).sort((a, b) => Math.hypot(a.x - pos.x, a.y - pos.y) - Math.hypot(b.x - pos.x, b.y - pos.y));
    /** @type {{x:number,y:number}|null} */ let best = hereOk ? target : null; let bestTier = hereTier, tested = 0;
    for (const p of pts) {
      if (++tested > 160) break;
      const tier = bubbleFit(p, w, h, o).tier;
      if (tier < bestTier && pathClear(pos, p, o.hard)) { best = p; bestTier = tier; if (tier === 0) break; }
    }
    if (bestTier <= 1) return best;
    // Crowded (small) screens: while speaking she may hover over plain text, never over imagery or
    // controls, if that is the only way to keep the bubble off everything interactive.
    /** @type {{x:number,y:number}[]} */ const loose = [];
    for (let y = EDGE; y + H <= vh() - EDGE; y += 24) for (let x = EDGE; x + W <= vw() - EDGE; x += 24) if (clear(boxAt(x, y, 4), o.hard)) loose.push({ x, y });
    loose.sort((a, b) => Math.hypot(a.x - pos.x, a.y - pos.y) - Math.hypot(b.x - pos.x, b.y - pos.y));
    tested = 0;
    for (const p of loose) { if (++tested > 600) break; if (bubbleFit(p, w, h, o).tier <= 1) return p; }   // small screens have few spots: check them all
    return best;
  }
  function showWhisper(/** @type {string} */ text, /** @type {SayOptions} */ o) {
    const kind = o.kind ?? "react";
    currentKind = kind;
    if (whisper.querySelector(".nw-text")?.textContent !== text) fillWhisper(text, o);
    whisper.hidden = false; whisper.style.visibility = "";
    // Pick the bubble's side once (around her resting point), then follow her every frame.
    const w = whisper.offsetWidth, h = whisper.offsetHeight;
    const { p } = bubbleFit(target, w, h, obstacles());
    side = p.side; offset = { x: p.x - target.x, y: p.y - target.y };
    whisper.dataset.side = side;
    followWhisper();
    whisper.classList.remove("is-shown"); void whisper.offsetWidth; whisper.classList.add("is-shown");
    clearTimeout(whisperTimer);
    // A tour bubble has no timeout: it stays until the visitor presses Next / Skip or does what it asks. A timeout used to count as
    // "dismissed" and walked the tour on by itself.
    const ms = o.ms ?? (kind === "tour" ? 0 : kind === "important" ? 9000 : Math.min(10000, 4000 + text.length * 55));
    if (ms) whisperTimer = window.setTimeout(() => hideWhisper(true), ms);
    if (reduced()) followWhisper();
  }
  /** Keep the bubble beside her base position (not her hover), inside the viewport, on whole pixels. */
  function followWhisper() {
    const w = whisper.offsetWidth, h = whisper.offsetHeight;
    const x = Math.round(Math.min(vw() - w - 8, Math.max(8, pos.x + offset.x)));
    const y = Math.round(Math.min(vh() - h - 8, Math.max(8, pos.y + offset.y)));
    if (whisper.style.left !== `${x}px`) whisper.style.left = `${x}px`;
    if (whisper.style.top !== `${y}px`) whisper.style.top = `${y}px`;
    const ax = pos.x + W / 2 - x, ay = pos.y + H / 2 - y;
    whisper.style.setProperty("--px", `${Math.round(Math.min(w - 16, Math.max(16, ax)))}px`);
    whisper.style.setProperty("--py", `${Math.round(Math.min(h - 14, Math.max(14, ay)))}px`);
  }
  function hideWhisper(/** @type {boolean} */ final = false) {
    clearTimeout(whisperTimer); pendingSay = null;
    if (whisper.hidden) return;
    whisper.classList.remove("is-shown");
    window.setTimeout(() => { if (!whisper.classList.contains("is-shown")) whisper.hidden = true; }, 260);
    // After speaking: IDLE_DIALOGUE returns to IDLE now; MISC after a brief pause (behave() releases it).
    if (speaking) {
      speaking = false; maxSpeed = 95;
      if (fsm.is(IDLE_DIALOGUE)) fsm.release(IDLE_DIALOGUE);
      else if (fsm.is(MISC)) holdUntil = Math.max(holdUntil, Date.now() + 1500);
    }
    if (final) { const cb = onWhisperClosed; onWhisperClosed = null; cb?.(); }
  }
  /** Remove the bubble without touching state or callbacks (a higher state pre-empted an idle remark). */
  function dropBubble() {
    clearTimeout(whisperTimer); speaking = false;
    if (whisper.hidden) return;
    whisper.classList.remove("is-shown");
    window.setTimeout(() => { if (!whisper.classList.contains("is-shown")) whisper.hidden = true; }, 260);
  }
  // Click the bubble to dismiss it; its small action buttons (tour Next / Skip) do their own thing.
  whisper.addEventListener("click", e => {
    const b = /** @type {HTMLElement} */ (e.target).closest("[data-i]");
    const act = b ? whisperActions[Number(b.getAttribute("data-i"))] : null;
    hideWhisper(!act);
    act?.run();
  });
  function renderTip() {
    const t = $(".np-tip"); t.hidden = !lastTip; t.textContent = lastTip ? `Navi: “${lastTip}”` : "";
    if (!panel.hidden) { roomForWords(); toLatest(); }
  }
  /** Viewport rect of where she is resting / heading. */
  const restRect = () => ({ left: target.x, top: target.y, right: target.x + W, bottom: target.y + H });

  // ---- assistant: Navi's constellation chat ------------------------------------------------------
  // A small translucent panel framed by a living constellation. Before it opens, and whenever the layout
  // changes, the screen is mapped on an 8 px grid (imagery, controls, navigation, side panels, text and
  // Navi herself are taken) and the chat takes free space near Navi, in one of three forms:
  //   full — preferred 360 × 400, down to 300 × 260, wherever that fits completely free;
  //   bar  — when no such room exists, a one-line bar (input + buttons) in free space;
  //   big  — a larger reading area: from ⤢, or opened by itself when a reply arrives in the bar. It uses
  //          free space when there is enough; otherwise it may lie over imagery (never over controls,
  //          navigation, side panels, text or Navi) until the user shrinks, moves or closes it.
  // Dragging it (header or grip) leaves it where the user put it until the chat is closed. While idle it
  // trails Navi; it holds still while the user types, reads, points at it or drags it.
  const PREF = { w: 360, h: 400 }, MIN = { w: 300, h: 260 }, BIG = { w: 600, h: 620 }, BAR = { w: 440, wMin: 300, h: 52 };
  const CELL = 8, CLEAR = 10;           // grid, and clearance around everything (room for the stars around it)
  const EDGE_GAP = 12;                  // kept from the viewport edges
  /** @type {"full"|"bar"|"big"} */ let form = "full";
  let big = false;                      // the user (or a reply in the bar) asked for the reading area
  let userPlaced = false;               // dragged by the user: stays put until closed
  let retries = 0, refitTimer = 0;
  /** @type {{dx:number,dy:number,id:number}|null} */ let drag = null;      // the user is dragging it
  const sheetMode = () => vw() < 600;
  const clampN = (/** @type {number} */ v, /** @type {number} */ lo, /** @type {number} */ hi) => Math.min(hi, Math.max(lo, v));

  /** Visible rects matching a selector (not ours, not hidden), clipped to the viewport. */
  function taken(/** @type {string} */ sel, skip = "") {
    if (!sel) return [];
    return [...document.querySelectorAll(sel)]
      .filter(el => !own.some(x => x === el || x.contains(el) || el.contains(x)) && getComputedStyle(el).visibility !== "hidden" && !(skip && el.matches(skip)))
      .map(el => el.getBoundingClientRect())
      .filter(r => r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < vh() && r.right > 0 && r.left < vw());
  }
  /**
   * Occupancy map of the viewport: summed-area table of how costly it is to cover each cell.
   * `withImagery` false leaves the imagery out (the rest of the interface only).
   */
  function spaceMap(withNavi = true, withImagery = true) {
    const cols = Math.floor(vw() / CELL), rows = Math.floor(vh() / CELL), cost = new Float64Array(cols * rows);
    const mark = (/** @type {Box} */ r, /** @type {number} */ wgt) => {
      const c0 = Math.max(0, Math.floor((r.left - CLEAR) / CELL)), c1 = Math.min(cols - 1, Math.floor((r.right + CLEAR) / CELL));
      const r0 = Math.max(0, Math.floor((r.top - CLEAR) / CELL)), r1 = Math.min(rows - 1, Math.floor((r.bottom + CLEAR) / CELL));
      for (let y = r0; y <= r1; y++) for (let x = c0; x <= c1; x++) cost[y * cols + x] += wgt;
    };
    // Imagery first, then controls, then the rest of the interface, then text. Navi is never covered.
    const img = opts.critical ?? "";
    if (withImagery) {
      taken(img).forEach(r => mark(r, 100));
      // The pictures themselves weigh most: an empty part of the viewer is a better place than the sky.
      // Each picture only counts where its frame shows it (a zoomed image is far larger than the viewer).
      if (img) document.querySelectorAll(img).forEach(frame => {
        const f = frame.getBoundingClientRect();
        frame.querySelectorAll("img, canvas").forEach(pic => {
          const p = pic.getBoundingClientRect();
          const r = { left: Math.max(p.left, f.left), top: Math.max(p.top, f.top), right: Math.min(p.right, f.right), bottom: Math.min(p.bottom, f.bottom) };
          if (r.right > r.left && r.bottom > r.top && getComputedStyle(pic).visibility !== "hidden") mark(r, 200);
        });
      });
    }
    taken(CONTROLS).forEach(r => mark(r, 40));
    taken([opts.panelAvoid, opts.avoid].filter(Boolean).join(", "), withImagery ? "" : img).forEach(r => mark(r, 25));
    taken(TEXT).forEach(r => mark(r, 8));
    // Navi is never covered: where she rests and where she is (she may still be easing to a stop).
    if (withNavi) { const n = naviZone(); mark({ left: n.left - 6, top: n.top - 6, right: n.right + 6, bottom: n.bottom + 6 }, 1e7); }
    const sat = new Float64Array((cols + 1) * (rows + 1));
    for (let y = 0; y < rows; y++) {
      let line = 0;
      for (let x = 0; x < cols; x++) { line += cost[y * cols + x]; sat[(y + 1) * (cols + 1) + x + 1] = sat[y * (cols + 1) + x + 1] + line; }
    }
    /** Cost of covering the box at cell (c, r) of cw × ch cells. */
    const sum = (/** @type {number} */ c, /** @type {number} */ r, /** @type {number} */ cw, /** @type {number} */ ch) =>
      sat[(r + ch) * (cols + 1) + c + cw] - sat[r * (cols + 1) + c + cw] - sat[(r + ch) * (cols + 1) + c] + sat[r * (cols + 1) + c];
    return { cols, rows, sum };
  }
  /** Box around where Navi rests and where she is drawn now (plus her gentle hover). */
  function naviZone() {
    const r = restRect(), hover = 8;
    return { left: Math.min(r.left, pos.x) - hover, top: Math.min(r.top, pos.y) - hover,
      right: Math.max(r.right, pos.x + W) + hover, bottom: Math.max(r.bottom, pos.y + H) + hover };
  }
  /** A free resting spot for Navi that stays clear of the dialogue box `b` (nearest to her), or null. */
  function naviSpotOutside(/** @type {{x:number,y:number,w:number,h:number}} */ b) {
    const o = obstacles(), box = { left: b.x - 16, top: b.y - 16, right: b.x + b.w + 16, bottom: b.y + b.h + 16 };
    const pts = freePoints(o).filter(p => overlap(boxAt(p.x, p.y, 8), box) === 0);
    if (!pts.length) return null;
    return pts.reduce((a, p) => (Math.hypot(p.x - pos.x, p.y - pos.y) < Math.hypot(a.x - pos.x, a.y - pos.y) ? p : a));
  }
  /** Gap between Navi and a box at (x, y) of w × h (0 when touching). */
  function gapToNavi(/** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ w, /** @type {number} */ h) {
    const n = restRect(), dx = Math.max(n.left - (x + w), x - n.right, 0), dy = Math.max(n.top - (y + h), y - n.bottom, 0);
    return Math.hypot(dx, dy);
  }
  /** Cells needed for a box of w × h pixels. */
  const cells = (/** @type {number} */ px) => Math.ceil(px / CELL);
  /** Free cost of a box at pixel position (x, y). */
  function costAt(/** @type {ReturnType<typeof spaceMap>} */ map, /** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ w, /** @type {number} */ h) {
    const c = Math.floor(x / CELL), r = Math.floor(y / CELL), cw = cells(w + (x - c * CELL)), ch = cells(h + (y - r * CELL));
    if (c < 0 || r < 0 || c + cw > map.cols || r + ch > map.rows) return Infinity;
    return map.sum(c, r, cw, ch);
  }
  /**
   * Best spot for a w × h box: with `onlyFree`, a spot covering nothing (closest to Navi) or null;
   * otherwise the spot covering the least (then closest to Navi).
   */
  function spotFor(/** @type {ReturnType<typeof spaceMap>} */ map, /** @type {number} */ w, /** @type {number} */ h, onlyFree = true) {
    const cw = cells(w), ch = cells(h);
    /** @type {{x:number,y:number,cost:number,d:number}|null} */ let best = null;
    for (let r = 2; r + ch <= map.rows - 2; r++) for (let c = 2; c + cw <= map.cols - 2; c++) {
      const cost = map.sum(c, r, cw, ch);
      if (onlyFree && cost > 0) continue;
      const x = c * CELL, y = r * CELL, d = gapToNavi(x, y, w, h);
      if (!best || cost < best.cost || (cost === best.cost && d < best.d)) best = { x, y, cost, d };
    }
    return best;
  }
  /** Spot for a w × h box that covers nothing in `gate`, covering the least of `map` (then closest to Navi). */
  function spotGated(/** @type {ReturnType<typeof spaceMap>} */ map, /** @type {ReturnType<typeof spaceMap>} */ gate, /** @type {number} */ w, /** @type {number} */ h) {
    const cw = cells(w), ch = cells(h);
    /** @type {{x:number,y:number,cost:number,d:number}|null} */ let best = null;
    for (let r = 2; r + ch <= map.rows - 2; r++) for (let c = 2; c + cw <= map.cols - 2; c++) {
      if (gate.sum(c, r, cw, ch) > 0) continue;
      const cost = map.sum(c, r, cw, ch), x = c * CELL, y = r * CELL, d = gapToNavi(x, y, w, h);
      if (!best || cost < best.cost || (cost === best.cost && d < best.d)) best = { x, y, cost, d };
    }
    return best;
  }
  /** Candidate sizes from large to small. */
  function sizes(/** @type {number} */ w0, /** @type {number} */ w1, /** @type {number} */ h0, /** @type {number} */ h1, /** @type {number} */ sw, /** @type {number} */ sh) {
    const out = [];
    for (let w = w0; w >= w1; w -= sw) for (let h = h0; h >= h1; h -= sh) out.push({ w: Math.round(w), h: Math.round(h) });
    if (!out.some(s => s.w === w1 && s.h === h1)) out.push({ w: w1, h: h1 });
    return out.sort((a, b) => b.w * b.h - a.w * a.h);
  }

  /** Decide form, size and position from the free space right now. */
  function placePanel() {
    const sheet = sheetMode();
    panel.classList.toggle("is-sheet", sheet);
    if (sheet) { panel.style.left = panel.style.top = panel.style.width = panel.style.height = ""; setForm(big ? "big" : "full"); drawSky(); return; }
    if (userPlaced) { keepOnScreen(); return; }
    const map = spaceMap(true), maxW = vw() - 2 * EDGE_GAP, maxH = vh() - 2 * EDGE_GAP;
    /** @type {ReturnType<typeof spaceMap>|null} */ let mapNoNavi = null;
    /** @type {{x:number,y:number}|null} */ let naviTo = null;
    let crowded = false;
    /** @typedef {{w:number,h:number,x:number,y:number}} Pick */
    const first = (/** @type {{w:number,h:number}[]} */ list, /** @type {ReturnType<typeof spaceMap>} */ m) => {
      for (const s of list) { if (s.w > maxW || s.h > maxH) continue; const p = spotFor(m, s.w, s.h); if (p) return /** @type {Pick} */ ({ ...s, x: p.x, y: p.y }); }
      return null;
    };
    /** Free space with Navi where she is; else free space she makes by flying to another free spot. */
    const fitIn = (/** @type {{w:number,h:number}[]} */ list) => {
      const here = first(list, map); if (here) return here;
      mapNoNavi ??= spaceMap(false);
      const there = first(list, mapNoNavi); if (!there) return null;
      const to = naviSpotOutside(there); if (!to) return null;
      naviTo = to; return there;
    };
    /** Over imagery at most (the emptiest part of it), never over the rest of the interface. */
    const overImagery = (/** @type {{w:number,h:number}[]} */ list) => {
      const ui = spaceMap(true, false);
      for (const s of list) { if (s.w > maxW || s.h > maxH) continue; const p = spotGated(map, ui, s.w, s.h); if (p) return /** @type {Pick} */ ({ ...s, x: p.x, y: p.y }); }
      return null;
    };
    /** @type {Pick|null} */ let pick = null;
    /** @type {"full"|"bar"|"big"} */ let f = "full";
    if (big) {
      const list = sizes(Math.min(BIG.w, maxW), Math.min(420, maxW), Math.min(BIG.h, maxH), Math.min(360, maxH), 60, 40);
      pick = fitIn(list) ?? overImagery(list); f = "big";
    } else {
      pick = fitIn(sizes(PREF.w, MIN.w, PREF.h, MIN.h, 30, 35));
      if (!pick) { pick = fitIn(sizes(BAR.w, BAR.wMin, BAR.h, BAR.h, 20, 1)); f = "bar"; }
      if (!pick) { pick = overImagery([{ w: BAR.wMin, h: BAR.h }]); f = "bar"; crowded = true; }
    }
    if (!pick) {
      const w = f === "big" ? Math.min(BIG.w, maxW) : BAR.wMin, h = f === "big" ? Math.min(BIG.h, maxH) : BAR.h, p = spotFor(map, w, h, false);
      pick = p && { w, h, x: p.x, y: p.y }; crowded = true;
    }
    // Squeezed: look again shortly (Navi may still be flying, the layout settling).
    if (crowded && retries < 6) { retries++; refit(700); } else if (!crowded) retries = 0;
    if (!pick) return;
    if (naviTo) setTarget(naviTo, 120);                      // she makes room with her usual flight
    setForm(f);
    panel.style.width = `${pick.w}px`; panel.style.height = `${pick.h}px`;
    moveTo(pick.x, pick.y);
    roomForWords(); drawSky(); toLatest();
    fmap = null; desired = null;                             // following starts again from this layout
  }
  /** The conversation keeps at least 150 px: in a short panel Navi's tip line, then the suggestions, step aside. */
  function roomForWords() {
    panel.classList.remove("no-tip", "no-sugg");
    if (form === "bar") return;
    if (log.clientHeight < 150) panel.classList.add("no-tip");
    if (log.clientHeight < 150) panel.classList.add("no-sugg");
  }
  function setForm(/** @type {"full"|"bar"|"big"} */ f) {
    if (f !== "bar") { panel.classList.remove("has-news"); input.placeholder = "Ask about what you see…"; }
    form = f;
    panel.classList.toggle("is-bar", f === "bar"); panel.classList.toggle("is-big", f === "big");
    const b = $(".np-expand"), on = f === "big";
    b.textContent = on ? "⤡" : "⤢"; b.setAttribute("aria-pressed", String(on));
    b.title = on ? "Smaller" : "Larger reading area"; b.setAttribute("aria-label", b.title);
  }
  function moveTo(/** @type {number} */ x, /** @type {number} */ y) {
    const w = panel.offsetWidth, h = panel.offsetHeight;
    x = clampN(x, EDGE_GAP, Math.max(EDGE_GAP, vw() - w - EDGE_GAP)); y = clampN(y, EDGE_GAP, Math.max(EDGE_GAP, vh() - h - EDGE_GAP));
    panel.style.left = `${Math.round(x)}px`; panel.style.top = `${Math.round(y)}px`;
    const n = restRect();                                    // it grows out of Navi
    panel.style.setProperty("--ox", `${Math.round((n.left + n.right) / 2 - x)}px`);
    panel.style.setProperty("--oy", `${Math.round((n.top + n.bottom) / 2 - y)}px`);
    return { x, y };
  }
  const keepOnScreen = () => moveTo(parseFloat(panel.style.left) || 0, parseFloat(panel.style.top) || 0);

  // Re-fit whenever the layout around it changes (a chat the user placed is only kept on screen).
  function refit(delay = 120) {
    if (panel.hidden) return;
    clearTimeout(refitTimer);
    refitTimer = window.setTimeout(() => { if (!panel.hidden && !drag) requestAnimationFrame(placePanel); }, delay);
  }
  addEventListener("resize", () => refit());
  document.addEventListener("fullscreenchange", () => refit(200));
  subscribe((_s, patch) => { if (["compareMode", "panels", "objectId", "observationId", "candidateId"].some(k => k in patch)) refit(180); });
  const mainEl = document.querySelector("main");
  if (mainEl) new ResizeObserver(() => refit(160)).observe(mainEl);

  // ---- the constellation around it: stars joined by thin lines ------------------------------------
  // It sits just outside the panel's edge (inside the clearance kept around the chat), floats 5 px up
  // and down (CSS) and changes shape in small stop-motion steps every 400–600 ms. Only the decoration
  // moves; text, input and buttons never do. Nothing moves with reduced motion.
  const SVG = "http://www.w3.org/2000/svg", SKY_M = 6;      // the outline extends 6 px around the panel
  const sky = document.createElementNS(SVG, "svg");
  sky.setAttribute("class", "np-sky"); sky.setAttribute("aria-hidden", "true");
  panel.prepend(sky);
  let skyKey = "";
  /** @type {{x:number,y:number}[]} */ let skyPts = [];
  /** @type {SVGPathElement|null} */ let skyPath = null;
  /** @type {SVGCircleElement[]} */ let skyStars = [];
  function drawSky() {
    const w = Math.round(panel.offsetWidth) + 2 * SKY_M, h = Math.round(panel.offsetHeight) + 2 * SKY_M, key = `${w}x${h}`;
    if (w <= 2 * SKY_M || h <= 2 * SKY_M || key === skyKey) return;
    skyKey = key;
    const i = 3, c = 12;                                     // 3 px outside the panel edge; corners cut
    /** @type {number[][]} */ const pts = [];
    const edge = (/** @type {number[]} */ a, /** @type {number[]} */ b) => {
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]), k = Math.max(1, Math.round(len / (60 + Math.random() * 40)));
      for (let s = 1; s < k; s++) { const t = (s + (Math.random() - 0.5) * 0.4) / k; pts.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]); }
    };
    const corners = [[i + c, i], [w - i - c, i], [w - i, i + c], [w - i, h - i - c], [w - i - c, h - i], [i + c, h - i], [i, h - i - c], [i, i + c]];
    corners.forEach((p, k) => { pts.push(p); if (k % 2 === 0) edge(p, corners[k + 1]); else edge(p, corners[(k + 1) % 8]); });
    skyPts = pts.map(p => ({ x: p[0], y: p[1] }));
    sky.setAttribute("viewBox", `0 0 ${w} ${h}`);
    sky.innerHTML = `<path class="np-lines"/>` + skyPts.map((_, k) =>
      `<circle r="${(k % 4 === 0 ? 2 : 1.2 + Math.random() * 0.6).toFixed(2)}" style="--d:${(3 + Math.random() * 4).toFixed(2)}s;--dl:${(-Math.random() * 6).toFixed(2)}s"/>`).join("");
    skyPath = sky.querySelector("path"); skyStars = [...sky.querySelectorAll("circle")];
    stepSky(reduced());
  }
  /** One stop-motion frame: every star takes a new small offset (none when `still`). */
  function stepSky(still = false) {
    if (!skyPath) return;
    let d = "";
    skyPts.forEach((p, k) => {
      const x = p.x + (still ? 0 : (Math.random() - 0.5) * 4.8), y = p.y + (still ? 0 : (Math.random() - 0.5) * 4.8);
      d += `${k ? " L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
      skyStars[k].setAttribute("cx", x.toFixed(1)); skyStars[k].setAttribute("cy", y.toFixed(1));
    });
    skyPath.setAttribute("d", d + " Z");
  }
  let skyTimer = 0;
  function startSky() {
    clearTimeout(skyTimer);
    if (panel.hidden || reduced()) { stepSky(true); return; }
    const tick = () => {
      if (panel.hidden || document.hidden || reduced()) return;
      stepSky(); skyTimer = window.setTimeout(tick, 400 + Math.random() * 200);
    };
    tick();
    startFollow();
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) startSky(); });

  // ---- following Navi --------------------------------------------------------------------------
  // While the user is not busy with the chat it trails Navi: a few times a second it looks for the
  // nearest free spot beside, above or below her and eases there with a soft lag. Every step must stay in
  // free space; if the straight way would cross anything it fades out and re-forms at the new spot.
  // When she is somewhere crowded it stays put and catches up later. While the user types, reads, points
  // at it or drags it, she pauses (TOUCHED) and the chat holds still.
  /** @type {ReturnType<typeof spaceMap>|null} */ let fmap = null;
  /** @type {{x:number,y:number}|null} */ let desired = null;
  let fmapAt = 0, desireAt = 0, engageAt = 0, readUntil = 0, calmSince = 0, hopping = false, followRaf = 0, frameAt = 0;
  const engaged = () => Date.now() < readUntil || Date.now() - engageAt < 4000 || panel.matches(":hover")
    || document.activeElement === input || !!drag;
  ["keydown", "input", "wheel", "pointerdown", "focusin"].forEach(t => panel.addEventListener(t, () => { engageAt = Date.now(); }, { passive: true }));
  log.addEventListener("scroll", () => { engageAt = Date.now(); }, { passive: true });
  const following = () => !userPlaced && form !== "big" && !sheetMode() && !engaged();

  function desire() {
    if (!fmap) return null;
    const w = panel.offsetWidth, h = panel.offsetHeight, cw = cells(w), ch = cells(h);
    const her = { left: pos.x, top: pos.y, right: pos.x + W, bottom: pos.y + H }, rest = restRect(), pad = 14;
    const clearOf = (/** @type {Box} */ b, /** @type {Box} */ n) => b.right <= n.left - pad || b.left >= n.right + pad || b.bottom <= n.top - pad || b.top >= n.bottom + pad;
    const gap = (/** @type {Box} */ b) => Math.hypot(Math.max(her.left - b.right, b.left - her.right, 0), Math.max(her.top - b.bottom, b.top - her.bottom, 0));
    /** @type {{x:number,y:number,g:number}|null} */ let best = null;
    const c0 = Math.ceil(EDGE_GAP / CELL), r0 = c0;
    for (let r = r0; r + ch <= fmap.rows - r0; r++) for (let c = c0; c + cw <= fmap.cols - c0; c++) {
      if (fmap.sum(c, r, cw, ch) > 0) continue;
      const x = c * CELL, y = r * CELL, b = { left: x, top: y, right: x + w, bottom: y + h };
      if (!clearOf(b, her) || !clearOf(b, rest)) continue;
      const g = gap(b);
      if (!best || g < best.g) best = { x, y, g };
    }
    const cx = parseFloat(panel.style.left) || 0, cy = parseFloat(panel.style.top) || 0;
    const cur = { left: cx, top: cy, right: cx + w, bottom: cy + h };
    const curOk = costAt(fmap, cx, cy, w, h) === 0 && clearOf(cur, her) && clearOf(cur, rest);
    if (!best) return null;                                       // nowhere free: stay where it is
    if (curOk && (gap(cur) <= best.g + 28 || best.g > 260)) return { x: cx, y: cy };
    return best;
  }
  function startFollow() { if (!followRaf && !panel.hidden) { frameAt = 0; followRaf = requestAnimationFrame(followTick); } }
  function followTick(/** @type {number} */ now) {
    followRaf = 0;
    if (panel.hidden) return;
    const dt = Math.min(0.1, Math.max(0, (now - (frameAt || now)) / 1000)); frameAt = now;
    follow(now, dt);
    followRaf = requestAnimationFrame(followTick);
  }
  function follow(/** @type {number} */ now, /** @type {number} */ dt) {
    // She pauses while the user is busy with the chat, and wanders again a little after.
    if (engaged()) {
      calmSince = 0;
      if (!fsm.is(TOUCHED) && fsm.request(TOUCHED, { force: true })) { target = { x: pos.x, y: pos.y }; publish(); }
    } else if (fsm.is(TOUCHED)) {
      calmSince ||= now;
      if (now - calmSince > 2500) { fsm.release(TOUCHED); calmSince = 0; }
    }
    if (!following() || hopping || document.hidden) return;
    if (!fmap || now - fmapAt > 1000) { fmap = spaceMap(false); fmapAt = now; }
    if (now - desireAt > 140) { desired = desire(); desireAt = now; }
    if (!desired) return;
    const cx = parseFloat(panel.style.left) || 0, cy = parseFloat(panel.style.top) || 0;
    const dx = desired.x - cx, dy = desired.y - cy, dist = Math.hypot(dx, dy);
    if (dist < 0.5) return;
    if (reduced()) { moveTo(desired.x, desired.y); return; }
    const step = Math.min(dist * (1 - Math.exp(-dt / 0.6)), 380 * dt);    // soft lag, at most 380 px/s
    const nx = cx + (dx / dist) * step, ny = cy + (dy / dist) * step;
    if (costAt(fmap, nx, ny, panel.offsetWidth, panel.offsetHeight) === 0) moveTo(nx, ny);
    else hop(desired);
  }
  /** Fade out, re-form at p (never sweeping across occupied space). */
  function hop(/** @type {{x:number,y:number}} */ p) {
    hopping = true; panel.classList.add("is-hopping");
    setTimeout(() => { moveTo(p.x, p.y); panel.classList.remove("is-hopping"); setTimeout(() => { hopping = false; }, 260); }, 220);
  }

  // ---- drag, expand, settings, open / close ---------------------------------------------------------
  // Drag from the header (not its buttons) or the grip; the pointer is captured on the panel itself, so
  // the drag keeps working in every form. Arrow keys on the grip move it too.
  const head = /** @type {HTMLElement} */ ($(".np-head"));
  head.addEventListener("pointerdown", e => {
    if (sheetMode() || e.button !== 0) return;
    const t = /** @type {HTMLElement} */ (e.target);
    if (t.closest("button") && !t.closest(".np-grip")) return;
    drag = { dx: e.clientX - (parseFloat(panel.style.left) || 0), dy: e.clientY - (parseFloat(panel.style.top) || 0), id: e.pointerId };
    try { panel.setPointerCapture(e.pointerId); } catch { /* synthetic pointer */ }
    panel.classList.add("is-dragging"); e.preventDefault();
  });
  panel.addEventListener("pointermove", e => {
    if (!drag || e.pointerId !== drag.id) return;
    userPlaced = true; moveTo(e.clientX - drag.dx, e.clientY - drag.dy);
  });
  const endDrag = () => { if (!drag) return; drag = null; panel.classList.remove("is-dragging"); };
  panel.addEventListener("pointerup", endDrag); panel.addEventListener("pointercancel", endDrag); panel.addEventListener("lostpointercapture", endDrag);
  $(".np-grip").addEventListener("keydown", e => {
    const d = /** @type {Record<string,[number,number]>} */ ({ ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] })[e.key];
    if (!d || sheetMode()) return;
    e.preventDefault(); userPlaced = true;
    moveTo((parseFloat(panel.style.left) || 0) + d[0], (parseFloat(panel.style.top) || 0) + d[1]);
  });

  // ⤢ larger reading area ⇄ ⤡ back; it is placed afresh either way.
  $(".np-expand").addEventListener("click", () => { big = form !== "big"; userPlaced = false; placePanel(); });
  // ⚙ settings (explanation level, Navi's comments, replay tour) fold out inside the chat.
  function showSettings(/** @type {boolean} */ on) {
    $(".np-settings").hidden = !on; $(".np-gear").setAttribute("aria-expanded", String(on));
    roomForWords();
  }
  $(".np-gear").addEventListener("click", () => showSettings($(".np-settings").hidden));

  function setOpen(/** @type {boolean} */ open, focus = true) {
    update({ naviOpen: open });
    body.setAttribute("aria-expanded", String(open));
    navi.classList.toggle("is-open", open);
    if (open) {
      hideWhisper(); dropBubble(); fsm.request(TOUCHED, { force: true });   // she pauses while it opens
      target = { x: pos.x, y: pos.y }; publish();
      panel.hidden = false; userPlaced = false; big = false; retries = 0; engageAt = Date.now(); panel.classList.remove("has-news");
      showSettings(false);
      renderLog(); renderSuggestions(); renderTip(); syncSettings(); placePanel();
      panel.classList.remove("is-arriving"); void panel.offsetWidth; panel.classList.add("is-arriving");
      startSky();
      if (focus) input.focus({ preventScroll: true });
    } else {
      panel.hidden = true; drag = null; clearTimeout(skyTimer);
      fsm.release(TOUCHED); scheduleRecheck(300);                           // TOUCHED → IDLE
      if (focus) body.focus();
    }
  }
  body.addEventListener("click", () => setOpen(panel.hidden));
  $(".np-close").addEventListener("click", () => setOpen(false));
  panel.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && !whisper.hidden && !panel.contains(/** @type {Node} */ (e.target))) hideWhisper(true);
  });

  function syncSettings() {
    const s = getState();
    panel.querySelectorAll("[data-mode]").forEach(b => b.setAttribute("aria-checked", String(/** @type {HTMLElement} */ (b).dataset.mode === s.naviMode)));
    panel.querySelectorAll("[data-hints]").forEach(b => b.setAttribute("aria-checked", String(/** @type {HTMLElement} */ (b).dataset.hints === s.naviHints)));
  }
  /** @type {(() => void)|null} */ let onReplayTour = null;
  panel.addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target);
    const m = t.closest("[data-mode]")?.getAttribute("data-mode"); if (m) { update({ naviMode: /** @type {any} */ (m) }); syncSettings(); }
    const h = t.closest("[data-hints]")?.getAttribute("data-hints"); if (h) { update({ naviHints: /** @type {any} */ (h) }); syncSettings(); }
    if (t.closest("[data-tour=replay]")) { setOpen(false, false); onReplayTour?.(); }
  });

  // ---- conversation ---------------------------------------------------------------------------
  let thinking = false, failed = /** @type {string|null} */ (null);
  const para = (/** @type {string} */ t) => t.split(/\n\n+/).map(p => `<p>${esc(p)}</p>`).join("");

  /** Show the newest words: the start of Navi's latest reply when it is taller than the view, else the bottom. */
  function toLatest() {
    const last = /** @type {HTMLElement|null} */ (log.lastElementChild);
    if (last?.classList.contains("msg-ai") && last.offsetHeight > log.clientHeight) log.scrollTop = last.offsetTop - 6;   // .np-log is positioned
    else log.scrollTop = log.scrollHeight;
  }
  function renderLog() {
    const msgs = getState().messages;
    log.innerHTML = (msgs.length ? msgs.map(renderMessage).join("") : `<div class="msg msg-ai"><div class="bubble">${para(getState().naviMode === "beginner"
      ? "Hi, I'm Navi. Ask me what you're looking at, why something is a candidate, or what to try next. I'll be honest when the data can't tell us."
      : "Navi here. Ask about candidates, comparisons, uncertainty or spectra. Replies cite what they rely on and list caveats.")}</div></div>`)
      + (thinking ? `<div class="msg msg-ai"><div class="bubble thinking" aria-label="Navi is thinking"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span> thinking</div></div>` : "")
      + (failed ? `<div class="msg"><p class="notice notice-error">${esc(failed)} <button class="btn" type="button" id="navi-retry">Try again</button></p></div>` : "");
    toLatest();
  }

  /** @param {any} m @param {number} i */
  function renderMessage(m, i) {
    if (m.role === "user") return `<div class="msg msg-user"><div class="bubble">${esc(m.content)}</div></div>`;
    const r = m.response;
    const refs = r?.references?.length ? `<div class="refs"><span class="label">Based on</span> ${r.references.map((/** @type {any} */ x) =>
      `<button type="button" class="chip" data-ref="${esc(x.targetType)}|${esc(x.targetId)}">${esc(x.targetType)}: ${esc(x.targetId.replace(/^mock-(obj|obs)-/, ""))}</button>`).join(" ")}</div>` : "";
    const cav = r?.caveats?.length ? `<div class="caveats"><span class="label">Keep in mind</span><ul>${r.caveats.map((/** @type {string} */ c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : "";
    const act = r?.suggestedActions?.length ? `<div class="actions"><span class="label">Next steps</span> ${r.suggestedActions.map((/** @type {any} */ a, /** @type {number} */ j) =>
      `<button type="button" class="btn" data-act="${i}|${j}">${esc(a.label)}</button>`).join(" ")}</div>` : "";
    return `<div class="msg msg-ai"><div class="bubble">${para(m.content)}${refs}${cav}${act}</div></div>`;
  }

  log.addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target);
    if (t.id === "navi-retry") { const lastMsg = [...getState().messages].reverse().find(m => m.role === "user"); failed = null; if (lastMsg) ask(lastMsg.content, true); return; }
    const ref = t.closest("[data-ref]")?.getAttribute("data-ref");
    if (ref) { const [type, id] = ref.split("|"); update(type === "object" ? { objectId: id, observationId: null } : type === "observation" ? { observationId: id, compareMode: "view" } : {}); nav("/investigate"); return; }
    const act = t.closest("[data-act]")?.getAttribute("data-act");
    if (act) { const [i, j] = act.split("|").map(Number); const a = getState().messages[i]?.response?.suggestedActions?.[j]; if (a) runAction(a); }
  });

  /** Suggested next steps change shared state or navigate; nothing is computed here. */
  function runAction(/** @type {any} */ a) {
    const s = getState();
    switch (a.kind) {
      case "open_comparison": case "run_comparison": update({ compareMode: a.params?.mode ?? "blink" }); nav("/investigate"); break;
      case "open_spectrum": nav("/spectrum"); break;
      case "open_observation": update({ ...(a.targetId ? { observationId: a.targetId } : {}), compareMode: "view" }); nav("/investigate"); break;
      case "add_note":
        update({ notes: [{ noteId: `note-${Date.now().toString(36)}`, text: String(a.params?.text ?? ""), createdAt: new Date().toISOString(), ...(s.observationId ? { observationId: s.observationId } : {}) }, ...s.notes] });
        pushMessage({ role: "assistant", content: "Added to your notes.", at: new Date().toISOString() }); break;
    }
  }

  function pushMessage(/** @type {any} */ m) {
    update({ messages: [...getState().messages.slice(-39), m] }); renderLog();
    // Give the user time to read a reply before Navi drifts off with it (~60 ms a character, 3–25 s).
    if (m.role === "assistant") {
      readUntil = Date.now() + Math.min(25000, Math.max(3000, String(m.content ?? "").length * 60));
      if (!panel.hidden && form === "bar") panel.classList.add("has-news");   // the user opens it with ⤢ (it may need to lie over imagery)
    }
  }

  const comparing = () => route() === "/investigate" && getState().compareMode !== "view";
  function buildContext() {
    const s = getState(), r = route();
    const activeView = comparing() ? "comparison" : r === "/investigate" ? "viewer" : r === "/spectrum" ? "spectrum" : "workspace";
    const observationIds = comparing() ? [s.compareBaseId, s.compareTargetId].filter(Boolean) : s.observationId ? [s.observationId] : [];
    // On the Spectrum page the selected band goes along, so "this band" questions are answered from that band's measurement.
    return { activeView, ...(s.objectId ? { objectId: s.objectId } : {}), observationIds, ...(s.candidateId ? { candidateId: s.candidateId } : {}),
             ...(r === "/spectrum" && s.bandIndex ? { bandIndex: s.bandIndex } : {}) };
  }

  /** @param {string} text @param {boolean} [isRetry] */
  async function ask(text, isRetry = false) {
    text = text.trim(); if (!text || thinking) return;
    if (!isRetry) pushMessage({ role: "user", content: text, at: new Date().toISOString() });
    thinking = true; failed = null; renderLog(); navi.classList.add("thinking"); input.placeholder = "Navi is thinking…";
    input.disabled = true; sendBtn.disabled = true;
    try {
      const r = await api.sendChatMessage({ message: text, context: buildContext() }, { mode: getState().naviMode });
      thinking = false;
      if (r.availability.status !== "available") failed = r.message?.content || "Navi is unavailable right now.";
      else pushMessage({ role: "assistant", content: r.message.content, response: r, at: r.message.createdAt });
    } catch (e) {
      thinking = false; failed = e instanceof ApiClientError ? e.message : "Navi couldn't answer. Please try again.";
    } finally {
      navi.classList.remove("thinking"); input.disabled = false; sendBtn.disabled = false; input.placeholder = panel.classList.contains("has-news") ? "Navi replied — press ⤢ to read" : "Ask about what you see…";
      renderLog(); renderSuggestions(); input.focus();
    }
  }
  $(".np-form").addEventListener("submit", e => { e.preventDefault(); const v = input.value; input.value = ""; ask(v); });

  async function renderSuggestions() {
    const r = route(), key = comparing() ? "compare" : ({ "/investigate": "investigate", "/spectrum": "spectrum", "/notes": "notes", "/learn": "learn" })[r] ?? "default";
    let list = [];
    try { list = await api.getChatSuggestions(key); } catch { /* suggestions are optional */ }
    $(".np-sugg").innerHTML = list.map((/** @type {string} */ q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
    if (!panel.hidden) { roomForWords(); toLatest(); }
  }
  $(".np-sugg").addEventListener("click", e => { const q = /** @type {HTMLElement} */ (e.target).closest("[data-q]")?.getAttribute("data-q"); if (q) ask(q); });

  subscribe((_s, patch) => {
    if (("candidateId" in patch || "compareMode" in patch) && !panel.hidden) renderSuggestions();
    if ("naviHints" in patch && patch.naviHints === "off" && currentKind !== "important" && currentKind !== "tour") hideWhisper();
  });

  // ---- continuity with the landing page ---------------------------------------------------------
  /** Landing → workspace: fly to the button, remember where she was, then navigate. */
  function handoff(/** @type {HTMLElement} */ toward, /** @type {string} */ href) {
    const leave = () => { try { sessionStorage.setItem(HANDOFF_KEY, JSON.stringify({ x: pos.x, y: pos.y, at: Date.now() })); } catch { /* storage blocked */ } window.location.href = href; };
    if (reduced()) return leave();
    const t = toward.getBoundingClientRect();
    fsm.request(MISC, { force: true }); holdUntil = Date.now() + 5000;
    setTarget({ x: Math.min(vw() - W - EDGE, t.right + 10), y: Math.max(EDGE, t.top - H / 2) }, 420);
    fillWhisper("Follow me!", {}); speaking = true; showWhisper("Follow me!", { kind: "important", ms: 900 });
    setTimeout(leave, 700);
  }
  /** Workspace boot: if she just came from the landing page, start where she left and fly in. */
  function arriveFromHandoff() {
    let h = null;
    try { h = JSON.parse(sessionStorage.getItem(HANDOFF_KEY) ?? "null"); sessionStorage.removeItem(HANDOFF_KEY); } catch { /* ignore */ }
    if (!h || Date.now() - h.at > 15000) return false;
    pos = { x: Math.min(vw() - W - 4, Math.max(4, h.x)), y: Math.min(vh() - H - 4, Math.max(4, h.y)) };
    target = { ...pos };
    return true;
  }

  // ---- boot -----------------------------------------------------------------------------------
  syncSettings();
  const arrived = inApp ? arriveFromHandoff() : false;
  navi.style.opacity = "0";                              // appear at her first spot (fade in), not at a default one
  render(performance.now()); start();
  // First destination once the page has laid out.
  setTimeout(() => { const p = choose(arrived ? "explore" : "nearest"); if (p) { fsm.request(MOVING); setTarget(p, arrived ? 140 : 9999); if (!arrived) { pos = { ...p }; vel = { x: 0, y: 0 }; } } navi.style.opacity = ""; }, 120);
  if (getState().naviOpen && inApp) setTimeout(() => setOpen(true, false), 500);

  return {
    /** Called by the app shell after each navigation (layout and free space changed). */
    onRoute() { hideWhisper(); scheduleRecheck(60); if (!panel.hidden) renderSuggestions(); },
    open: () => setOpen(true),
    say, hideWhisper, goNear, handoff,
    playful() { if (fsm.is(IDLE) && !studying()) idleUntil = 0; },
    /** Provider of spontaneous IDLE_DIALOGUE lines (personality lives in naviBrain.js). */
    set idleLines(/** @type {(() => string)|null} */ fn) { idleLineSource = fn; },
    /** Current behaviour state (idle | moving | touched | idle_dialogue | misc). */
    get state() { return fsm.state; },
    /** Called when a whisper is dismissed (click, Esc, timeout) — the tour uses it to advance. */
    set onWhisperClosed(/** @type {(() => void)|null} */ fn) { onWhisperClosed = fn; },
    set onReplayTour(/** @type {(() => void)|null} */ fn) { onReplayTour = fn; },
    get arrived() { return arrived; },
    get studying() { return studying(); },
    get panelOpen() { return !panel.hidden; },
    route,
  };
}
