// @ts-check
/**
 * Navi, the research companion: her body (movement), her voice (whispers) and the assistant.
 * What she says and when is decided by js/components/naviBrain.js.
 *
 * Movement. Each page marks SAFE ZONES in unused space (`data-navi-zone`; optional
 * `data-navi-size="small"`): the empty end of the nav bar, the title row, the scenery below the
 * timeline, beside the spacecraft... Navi wanders between points in the visible zones: slow curved
 * flights, leaning gently into the direction of travel, then pauses of several seconds, with the
 * occasional loop. Paths that would cross imagery (`opts.critical`) become a quick sparkle-hop
 * instead, so she never flies over data. When the user starts inspecting an image (pointer or wheel
 * on imagery) she retreats to the zone farthest away and stays quiet for a while. When no zone is on
 * screen she tucks into the corner at half size. Reduced motion: no wandering, gliding, floating or
 * particles; she just sits in a zone.
 *
 * Continuity: leaving the landing page through "Begin exploring" stores her screen position; the
 * workspace starts her there and she flies in (see handoff()).
 *
 * The sprite is the approved asset, never altered: drawn at 1x (88x84) or exactly 0.5x.
 * The browser never calls an LLM: replies come from api.sendChatMessage (mock: scripted).
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, subscribe, toQuery } from "../core/store.js";
import { esc } from "../core/dom.js";
import { go, currentLocation } from "../core/router.js";
import { placeBeside, rectsOf, overlap } from "../core/placement.js";

const FULL = { w: 88, h: 84 }, SMALL = { w: 44, h: 42 };
const HANDOFF_KEY = "spherex.navi.handoff";
const COMMENT_LABEL = { all: "On", fewer: "Fewer", off: "Off" };

/**
 * @typedef {Object} NaviOptions
 * @property {boolean} [inApp]        true inside app.html (hash routes); false on the landing page
 * @property {string} [avoid]         selector of things a whisper must never cover
 * @property {string} [panelAvoid]    selector of things the assistant panel should not cover
 * @property {string} [critical]      selector of imagery Navi must never fly over
 * @property {string} [panelColumn]   selector of a side column the assistant may dock over when visible
 * @property {(open: boolean) => boolean} [onPanel]  called when the assistant opens/closes; return true if the page made room
 * @typedef {{ label: string, run: () => void }} WhisperAction
 * @typedef {{ kind?: "important"|"tour"|"react"|"ambient", actions?: WhisperAction[], ms?: number, force?: boolean }} SayOptions
 */

/** @param {NaviOptions} [opts] */
export function mountNavi(opts = {}) {
  const inApp = !!opts.inApp;
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- DOM ------------------------------------------------------------------------------------
  const navi = document.createElement("div");
  navi.className = "navi";
  navi.innerHTML = `
    <div class="navi-lean"><div class="navi-float">
      <span class="navi-trail" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>
      <button class="navi-body" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="navi-panel"
        aria-label="Navi, your research companion. Open the assistant.">
        <img src="assets/navi/navi.png" alt="" width="${FULL.w}" height="${FULL.h}" draggable="false">
      </button>
    </div></div>`;
  const whisper = document.createElement("div");
  whisper.className = "navi-whisper"; whisper.setAttribute("role", "status"); whisper.hidden = true;
  const panel = document.createElement("section");
  panel.className = "navi-panel"; panel.id = "navi-panel"; panel.hidden = true;
  panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Navi, research companion");
  panel.innerHTML = `
    <header class="np-head">
      <strong class="pixel">Navi</strong>
      <span class="muted fine">${isMock() ? "Illustrative replies · no AI model" : "Research companion"}</span>
      <button class="np-close" type="button" aria-label="Close Navi">✕</button>
    </header>
    <div class="np-settings">
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
      <input class="input" id="navi-input" placeholder="Ask about what you see…" autocomplete="off" maxlength="300">
      <button class="btn" type="submit">Ask</button>
    </form>`;
  document.body.append(navi, whisper, panel);

  const body = /** @type {HTMLButtonElement} */ (navi.querySelector(".navi-body"));
  const lean = /** @type {HTMLElement} */ (navi.querySelector(".navi-lean"));
  const float = /** @type {HTMLElement} */ (navi.querySelector(".navi-float"));
  const $ = (/** @type {string} */ s) => /** @type {HTMLElement} */ (panel.querySelector(s));
  const log = $(".np-log"), input = /** @type {HTMLInputElement} */ ($("#navi-input")), sendBtn = /** @type {HTMLButtonElement} */ ($(".np-form .btn"));
  const route = () => (inApp ? currentLocation().path : "/landing");
  const nav = (/** @type {string} */ path) => {
    if (inApp) return go(path);
    const q = toQuery(); window.location.href = `app.html#${path}${q ? "?" + q : ""}`;
  };

  // ---- geometry -------------------------------------------------------------------------------
  /** Rest position in DOCUMENT coordinates (the target of the current flight), and size. */
  let pos = { x: 0, y: 0 }, size = FULL, peek = false, placed = false;
  /** @type {Element|null} */ let zone = null;
  const vpRect = () => ({ left: pos.x - scrollX, top: pos.y - scrollY, right: pos.x - scrollX + size.w, bottom: pos.y - scrollY + size.h });
  /** Viewport rect of where Navi is (or is heading). */
  function restRect() {
    if (peek) { const r = body.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }; }
    return vpRect();
  }
  const critical = () => rectsOf(opts.critical ?? "");
  function publish() { navi.dataset.rest = `${Math.round(pos.x)},${Math.round(pos.y)},${size.w},${size.h}`; navi.dataset.peek = String(peek); }

  /** Visible safe zones (at least partly on screen and not hidden). */
  function zones() {
    const vh = window.innerHeight, vw = document.documentElement.clientWidth;
    return [...document.querySelectorAll("[data-navi-zone]")].filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 20 && r.height > 20 && r.bottom > 30 && r.top < vh - 30 && r.right > 0 && r.left < vw && /** @type {HTMLElement} */ (el).offsetParent !== null;
    });
  }
  const sizeFor = (/** @type {Element} */ z) => (z.getAttribute("data-navi-size") === "small" || document.documentElement.clientWidth < 600 ? SMALL : FULL);

  /** A point (document coords, top-left of the sprite) inside zone z, optionally near a viewport point. */
  function pointIn(/** @type {Element} */ z, /** @type {{x:number,y:number}|null} */ near = null) {
    const r = z.getBoundingClientRect(), s = sizeFor(z), inset = 7;          // room for her gentle bob
    const minX = r.left + 2, maxX = Math.max(minX, r.right - s.w - 2), minY = Math.min(r.top + inset, r.bottom - s.h), maxY = Math.max(minY, r.bottom - s.h - 2);
    let x, y;
    if (near) { x = Math.min(maxX, Math.max(minX, near.x - s.w / 2)); y = Math.min(maxY, Math.max(minY, near.y - s.h / 2)); }
    else { x = minX + Math.random() * (maxX - minX); y = minY + Math.random() * (maxY - minY); }
    // keep fully on screen
    x = Math.min(document.documentElement.clientWidth - s.w - 4, Math.max(4, x));
    return { x: Math.round(x + scrollX), y: Math.round(y + scrollY), s };
  }

  // ---- flight ---------------------------------------------------------------------------------
  let flightId = 0;
  /** rAF with a timer fallback, so motion never stalls in throttled frames. */
  const frame = (/** @type {() => void} */ cb) => { let done = false; const id = requestAnimationFrame(() => { done = true; cb(); }); setTimeout(() => { if (!done) { cancelAnimationFrame(id); cb(); } }, 40); };

  /** Fly to a document point. Curved, slow, leaning into the direction; hops instead of crossing imagery. */
  function flyTo(/** @type {{x:number,y:number,s:{w:number,h:number}}} */ p, speed = 70) {
    const id = ++flightId;
    const from = placed && !peek ? { ...pos } : null;
    leavePeek();
    pos = { x: p.x, y: p.y }; size = p.s; publish();
    navi.classList.toggle("is-small", size === SMALL);
    if (!from || reduced()) { navi.style.transform = `translate3d(${pos.x}px, ${pos.y}px, 0)`; placed = true; return; }

    const dx = pos.x - from.x, dy = pos.y - from.y, dist = Math.hypot(dx, dy);
    if (dist < 2) return;
    // Would a straight-ish path cross imagery? Then hop: fade out, reappear at the destination.
    const crit = critical().map(r => ({ left: r.left + scrollX, top: r.top + scrollY, right: r.right + scrollX, bottom: r.bottom + scrollY }));
    const crosses = Array.from({ length: 14 }, (_, i) => (i + 1) / 15).some(t => {
      const x = from.x + dx * t, y = from.y + dy * t, box = { left: x, top: y, right: x + size.w, bottom: y + size.h };
      return crit.some(c => overlap(box, c) > 0);
    });
    if (crosses) {
      navi.classList.add("is-hopping");
      setTimeout(() => { if (id !== flightId) return; navi.style.transform = `translate3d(${pos.x}px, ${pos.y}px, 0)`; navi.classList.remove("is-hopping"); }, 260);
      return;
    }
    // Quadratic Bézier with a sideways bow, so flights curve rather than slide.
    const bow = Math.min(60, dist * 0.25) * (Math.random() < 0.5 ? -1 : 1);
    // Keep the curve close to the start/end band (so a flight along the nav bar never swings over the
    // links) and inside the viewport.
    const lo = Math.max(scrollY + 4, Math.min(from.y, pos.y) - 22), hi = Math.min(scrollY + window.innerHeight - size.h - 4, Math.max(from.y, pos.y) + 22);
    const cx = Math.min(scrollX + document.documentElement.clientWidth - size.w - 4, Math.max(scrollX + 4, from.x + dx / 2 - (dy / (dist || 1)) * bow));
    const cy = Math.min(hi, Math.max(lo, from.y + dy / 2 + (dx / (dist || 1)) * bow));
    const dur = Math.min(6000, Math.max(1200, (dist / speed) * 1000)), t0 = performance.now();
    navi.classList.add("is-moving");
    lean.style.transform = `rotate(${Math.max(-8, Math.min(8, (dx / (dist || 1)) * 8))}deg)`;
    const step = () => {
      if (id !== flightId) return;
      const t = Math.min(1, (performance.now() - t0) / dur), e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
      const x = (1 - e) ** 2 * from.x + 2 * (1 - e) * e * cx + e * e * pos.x, y = (1 - e) ** 2 * from.y + 2 * (1 - e) * e * cy + e * e * pos.y;
      navi.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
      if (t < 1) frame(step);
      else { navi.classList.remove("is-moving"); lean.style.transform = ""; if (!whisper.hidden) placeWhisper(); }
    };
    frame(step);
  }

  function enterPeek() {
    if (peek) return;
    flightId++; peek = true; navi.classList.add("is-peek"); navi.style.transform = ""; publish();
    const r = body.getBoundingClientRect();
    navi.classList.toggle("is-tucked", critical().some(c => overlap({ left: r.left, top: r.top, right: r.right, bottom: r.bottom }, c) > 0) && panel.hidden);
  }
  function leavePeek() { if (!peek) return; peek = false; navi.classList.remove("is-peek", "is-tucked"); navi.classList.add("is-switching"); void navi.offsetWidth; navi.classList.remove("is-switching"); }

  /** Make sure Navi is somewhere visible and safe; pick a new zone if hers scrolled away. */
  function settle() {
    if (!panel.hidden) return placePanel();
    const zs = zones();
    const r = vpRect(), onScreen = !peek && placed && r.bottom > 0 && r.top < window.innerHeight;
    // Still valid only if she is inside her zone (layout may have moved it) and clear of imagery.
    const zr = zone?.getBoundingClientRect();
    const stillInside = !!zr && r.left >= zr.left - 4 && r.right <= zr.right + 4 && r.top >= zr.top - 4 && r.bottom <= zr.bottom + 4;
    if (onScreen && zone && zs.includes(zone) && stillInside && !critical().some(c => overlap(r, c) > 0)) return;
    if (!zs.length) { zone = null; return enterPeek(); }
    zone = zs[0];
    flyTo(pointIn(zone), 220);
  }

  // ---- wandering ------------------------------------------------------------------------------
  let wanderTimer = 0, studyUntil = 0, holdUntil = 0;
  const studying = () => Date.now() < studyUntil;
  function scheduleWander(/** @type {number} */ ms = 5000 + Math.random() * 7000) {
    clearTimeout(wanderTimer);
    wanderTimer = window.setTimeout(wander, ms);
  }
  function wander() {
    scheduleWander();
    if (reduced() || !panel.hidden || document.hidden || studying() || Date.now() < holdUntil || peek) return;
    const zs = zones(); if (!zs.length) return;
    // Mostly drift within the current zone; sometimes explore another one.
    const other = zs.filter(z => z !== zone);
    if (!zone || !zs.includes(zone) || (other.length && Math.random() < 0.35)) zone = other.length ? other[Math.floor(Math.random() * other.length)] : zs[0];
    const cur = vpRect(), p = pointIn(zone, Math.random() < 0.6 ? { x: (cur.left + cur.right) / 2 + (Math.random() - 0.5) * 320, y: (cur.top + cur.bottom) / 2 + (Math.random() - 0.5) * 80 } : null);
    flyTo(p);
    if (Math.random() < 0.18) setTimeout(() => playful(), 2500);
  }
  function playful() {
    if (reduced() || studying()) return;
    const kind = ["loop", "sparkle", "twirl"][Math.floor(Math.random() * 3)];
    float.classList.add(`idle-${kind}`); setTimeout(() => float.classList.remove(`idle-${kind}`), 2400);
  }

  /** User is inspecting imagery: retreat to the zone farthest from it and keep quiet for a while. */
  function study() {
    const was = studying(); studyUntil = Date.now() + 20000;
    hideWhisper(true);
    navi.classList.add("is-quiet");
    clearTimeout(quietTimer); quietTimer = window.setTimeout(() => navi.classList.remove("is-quiet"), 20000);
    if (was || reduced() || !panel.hidden) return;
    const zs = zones(), crit = critical(); if (!zs.length || !crit.length) return;
    const c = crit[0], cx = (c.left + c.right) / 2, cy = (c.top + c.bottom) / 2;
    const far = zs.map(z => { const r = z.getBoundingClientRect(); return { z, d: Math.hypot((r.left + r.right) / 2 - cx, (r.top + r.bottom) / 2 - cy) }; }).sort((a, b) => b.d - a.d)[0].z;
    zone = far; flyTo(pointIn(far), 140);
  }
  let quietTimer = 0;
  const onInspect = (/** @type {Event} */ e) => { if (opts.critical && /** @type {Element} */ (e.target).closest?.(opts.critical)) study(); };
  document.addEventListener("pointerdown", onInspect, true);
  document.addEventListener("wheel", onInspect, { capture: true, passive: true });

  /** Fly to the safe-zone point closest to an element (never onto it), and hold there briefly. */
  function goNear(/** @type {string|Element} */ target, holdMs = 9000) {
    const el = typeof target === "string" ? document.querySelector(target) : target;
    const zs = zones(); if (!el || !zs.length) return;
    const t = el.getBoundingClientRect(), tx = (t.left + t.right) / 2, ty = (t.top + t.bottom) / 2;
    let best = null, bd = Infinity;
    for (const z of zs) {
      const p = pointIn(z, { x: tx, y: ty }), d = Math.hypot(p.x - scrollX + p.s.w / 2 - tx, p.y - scrollY + p.s.h / 2 - ty);
      if (d < bd) { bd = d; best = { z, p }; }
    }
    if (!best) return;
    zone = best.z; holdUntil = Date.now() + holdMs; flyTo(best.p, 160);
  }

  // ---- scroll / resize ------------------------------------------------------------------------
  let settleTimer = 0, lastY = scrollY;
  const onScroll = () => { lastY = scrollY; hideWhisper(); clearTimeout(settleTimer); settleTimer = window.setTimeout(settle, 220); };
  addEventListener("scroll", onScroll, { passive: true });
  setInterval(() => { if (!document.hidden && Math.abs(scrollY - lastY) > 40) onScroll(); }, 800);   // for environments that drop scroll events
  // Content that loads or drawers that open move the zones: re-check her spot when layout settles.
  let layoutTimer = 0;
  new ResizeObserver(() => { clearTimeout(layoutTimer); layoutTimer = window.setTimeout(settle, 250); }).observe(document.body);
  addEventListener("resize", () => { hideWhisper(); clearTimeout(settleTimer); settleTimer = window.setTimeout(() => { zone = null; placed = placed && !peek; settle(); }, 200); });

  // ---- whispers -------------------------------------------------------------------------------
  let whisperTimer = 0, lastSaid = 0, lastTip = "", currentKind = "";
  const RANK = { ambient: 0, react: 1, tour: 2, important: 3 };

  /**
   * Say something in a small bubble beside her. Unsolicited lines respect the comment setting;
   * a lower-priority line never interrupts a higher one (e.g. a joke never cuts off the tour).
   * @param {string} text @param {SayOptions} [o] @returns {boolean} shown
   */
  function say(text, o = {}) {
    const kind = o.kind ?? "react";
    if (!text) return false;
    lastTip = text; renderTip();
    if (!panel.hidden && !o.force) return false;
    const pref = getState().naviHints, now = Date.now();
    if (!o.force && kind !== "important") {
      if (document.body.classList.contains("focus-mode")) return false;    // distraction-free means it
      if (pref === "off") return false;
      if (pref === "fewer" && (kind === "ambient" || (kind === "react" && now - lastSaid < 45000))) return false;
      if (kind === "react" && now - lastSaid < 2500) return false;
      if (studying() && kind !== "tour") return false;
    }
    if (!whisper.hidden && whisper.classList.contains("is-shown") && RANK[/** @type {keyof typeof RANK} */ (currentKind)] > RANK[kind] && !o.force) return false;
    currentKind = kind; lastSaid = now;
    whisper.innerHTML = `<span class="nw-text">${esc(text)}</span>${o.actions?.length ? `<span class="nw-actions">${o.actions.map((a, i) => `<button type="button" data-i="${i}">${esc(a.label)}</button>`).join("")}</span>` : ""}`;
    whisperActions = o.actions ?? [];
    whisper.hidden = false; whisper.classList.remove("is-shown");
    placeWhisper();
    void whisper.offsetWidth; whisper.classList.add("is-shown");
    clearTimeout(whisperTimer);
    const ms = o.ms ?? (kind === "tour" ? 14000 : kind === "important" ? 9000 : Math.min(9000, 3500 + text.length * 45));
    whisperTimer = window.setTimeout(() => hideWhisper(true), ms);
    return true;
  }
  /** @type {WhisperAction[]} */ let whisperActions = [];
  /** @type {(() => void)|null} */ let onWhisperClosed = null;
  function hideWhisper(/** @type {boolean} */ final = false) {
    clearTimeout(whisperTimer);
    if (whisper.hidden) return;
    whisper.classList.remove("is-shown");
    window.setTimeout(() => { if (!whisper.classList.contains("is-shown")) whisper.hidden = true; }, 260);
    if (final) { const cb = onWhisperClosed; onWhisperClosed = null; cb?.(); }
  }
  function placeWhisper() {
    if (whisper.hidden) return;
    const a = restRect(), w = whisper.offsetWidth, h = whisper.offsetHeight;
    const p = placeBeside(a, w, h, rectsOf(opts.avoid ?? "", [navi, whisper, panel]), { sides: ["left", "right", "bottom", "top"], gap: 6 });
    whisper.style.left = `${Math.round(p.x)}px`; whisper.style.top = `${Math.round(p.y)}px`;
    whisper.dataset.side = p.side;
    const ax = (a.left + a.right) / 2 - p.x, ay = (a.top + a.bottom) / 2 - p.y;
    whisper.style.setProperty("--px", `${Math.round(Math.min(w - 14, Math.max(14, ax)))}px`);
    whisper.style.setProperty("--py", `${Math.round(Math.min(h - 10, Math.max(10, ay)))}px`);
  }
  // Click the bubble to dismiss it; its small action buttons (tour Next / Skip) do their own thing.
  whisper.addEventListener("click", e => {
    const b = /** @type {HTMLElement} */ (e.target).closest("[data-i]");
    const act = b ? whisperActions[Number(b.getAttribute("data-i"))] : null;
    hideWhisper(!act);
    act?.run();
  });
  function renderTip() { const t = $(".np-tip"); t.hidden = !lastTip; t.textContent = lastTip ? `Navi: “${lastTip}”` : ""; }

  // ---- assistant panel ------------------------------------------------------------------------
  function placePanel() {
    const sheet = document.documentElement.clientWidth < (inApp ? 1000 : 600);
    panel.classList.toggle("is-sheet", sheet);
    if (sheet) { panel.style.left = ""; panel.style.top = ""; panel.style.width = ""; panel.style.height = ""; return; }
    const colEl = opts.panelColumn ? [...document.querySelectorAll(opts.panelColumn)].find(el => /** @type {HTMLElement} */ (el).offsetParent && el.getBoundingClientRect().width >= 240) : null;
    const col = colEl?.getBoundingClientRect();
    if (col && col.width < document.documentElement.clientWidth * 0.45) {
      const a = restRect(), vh = window.innerHeight, top = Math.max(8, Math.min(Math.max(a.bottom + 8, col.top), vh - 300));
      panel.style.width = `${Math.round(col.width)}px`; panel.style.height = `${Math.round(Math.min(480, vh - top - 12))}px`;
      panel.style.left = `${Math.round(col.left)}px`; panel.style.top = `${Math.round(top)}px`;
      return;
    }
    panel.style.width = ""; panel.style.height = "";
    const a = restRect(), w = panel.offsetWidth, h = panel.offsetHeight;
    const p = placeBeside(a, w, h, rectsOf(opts.panelAvoid ?? "", [navi, whisper, panel]), { sides: ["bottom", "left", "top", "right"], gap: 8, align: "end" });
    panel.style.left = `${Math.round(p.x)}px`; panel.style.top = `${Math.round(p.y)}px`;
  }

  function setOpen(/** @type {boolean} */ open, focus = true) {
    update({ naviOpen: open });
    panel.hidden = !open; body.setAttribute("aria-expanded", String(open));
    navi.classList.toggle("is-open", open);
    // Let the page make room (e.g. open a side drawer) so the assistant never covers imagery.
    const madeRoom = opts.onPanel?.(open) ?? false;
    if (open) {
      hideWhisper(); navi.classList.remove("is-tucked");
      renderLog(); renderSuggestions(); renderTip(); syncSettings(); placePanel();
      if (madeRoom) setTimeout(placePanel, 280);          // after the drawer has slid open
      if (focus) input.focus();
    } else {
      settle();
      if (focus) body.focus();
    }
  }
  body.addEventListener("click", () => {
    if (!reduced()) { float.classList.remove("hop"); void float.offsetWidth; float.classList.add("hop"); }
    setOpen(panel.hidden);
  });
  $(".np-close").addEventListener("click", () => setOpen(false));
  panel.addEventListener("keydown", e => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!panel.hidden && !panel.contains(/** @type {Node} */ (e.target))) setOpen(false);
    else if (!whisper.hidden) hideWhisper(true);
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

  function renderLog() {
    const msgs = getState().messages;
    log.innerHTML = (msgs.length ? msgs.map(renderMessage).join("") : `<div class="msg msg-ai"><div class="bubble">${para(getState().naviMode === "beginner"
      ? "Hi, I'm Navi. Ask me what you're looking at, why something is a candidate, or what to try next. I'll be honest when the data can't tell us."
      : "Navi here. Ask about candidates, comparisons, uncertainty or spectra. Replies cite what they rely on and list caveats.")}</div></div>`)
      + (thinking ? `<div class="msg msg-ai"><div class="bubble thinking" aria-label="Navi is thinking"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span> thinking</div></div>` : "")
      + (failed ? `<div class="msg"><p class="notice notice-error">${esc(failed)} <button class="btn" type="button" id="navi-retry">Try again</button></p></div>` : "");
    log.scrollTop = log.scrollHeight;
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
    if (t.id === "navi-retry") { const last = [...getState().messages].reverse().find(m => m.role === "user"); failed = null; if (last) ask(last.content, true); return; }
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

  function pushMessage(/** @type {any} */ m) { update({ messages: [...getState().messages.slice(-39), m] }); renderLog(); }

  const comparing = () => route() === "/investigate" && getState().compareMode !== "view";
  function buildContext() {
    const s = getState(), r = route();
    const activeView = comparing() ? "comparison" : r === "/investigate" ? "viewer" : r === "/spectrum" ? "spectrum" : "workspace";
    const observationIds = comparing() ? [s.compareBaseId, s.compareTargetId].filter(Boolean) : s.observationId ? [s.observationId] : [];
    return { activeView, ...(s.objectId ? { objectId: s.objectId } : {}), observationIds, ...(s.candidateId ? { candidateId: s.candidateId } : {}) };
  }

  /** @param {string} text @param {boolean} [isRetry] */
  async function ask(text, isRetry = false) {
    text = text.trim(); if (!text || thinking) return;
    if (!isRetry) pushMessage({ role: "user", content: text, at: new Date().toISOString() });
    thinking = true; failed = null; renderLog(); navi.classList.add("thinking");
    input.disabled = true; sendBtn.disabled = true;
    try {
      const r = await api.sendChatMessage({ message: text, context: buildContext() }, { mode: getState().naviMode });
      thinking = false;
      if (r.availability.status !== "available") failed = r.message?.content || "Navi is unavailable right now.";
      else pushMessage({ role: "assistant", content: r.message.content, response: r, at: r.message.createdAt });
    } catch (e) {
      thinking = false; failed = e instanceof ApiClientError ? e.message : "Navi couldn't answer. Please try again.";
    } finally {
      navi.classList.remove("thinking"); input.disabled = false; sendBtn.disabled = false;
      renderLog(); renderSuggestions(); input.focus();
    }
  }
  $(".np-form").addEventListener("submit", e => { e.preventDefault(); const v = input.value; input.value = ""; ask(v); });

  async function renderSuggestions() {
    const r = route(), key = comparing() ? "compare" : ({ "/investigate": "investigate", "/spectrum": "spectrum", "/notes": "notes", "/learn": "learn" })[r] ?? "default";
    let list = [];
    try { list = await api.getChatSuggestions(key); } catch { /* suggestions are optional */ }
    $(".np-sugg").innerHTML = list.map((/** @type {string} */ q) => `<button type="button" class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  }
  $(".np-sugg").addEventListener("click", e => { const q = /** @type {HTMLElement} */ (e.target).closest("[data-q]")?.getAttribute("data-q"); if (q) ask(q); });

  subscribe((_s, patch) => {
    if (("candidateId" in patch || "compareMode" in patch) && !panel.hidden) renderSuggestions();
    if ("naviHints" in patch && patch.naviHints === "off" && currentKind !== "important" && currentKind !== "tour") hideWhisper();
  });

  // ---- continuity with the landing page ---------------------------------------------------------
  /** Landing → workspace: fly to the button, remember where she was, then navigate. */
  function handoff(/** @type {HTMLElement} */ toward, /** @type {string} */ href) {
    const go = () => { const r = restRect(); try { sessionStorage.setItem(HANDOFF_KEY, JSON.stringify({ x: r.left, y: r.top, at: Date.now() })); } catch { /* storage blocked */ } window.location.href = href; };
    if (reduced()) return go();
    const t = toward.getBoundingClientRect();
    flightId++; peek = false; navi.classList.remove("is-peek", "is-tucked");
    pos = { x: Math.round(t.right + 8 + scrollX), y: Math.round(t.top - 20 + scrollY) }; size = FULL; publish();
    navi.classList.add("is-dashing"); navi.style.transform = `translate3d(${pos.x}px, ${pos.y}px, 0)`;
    say("Follow me!", { kind: "important", ms: 900, force: true });
    setTimeout(go, 650);
  }
  /** Workspace boot: if she just came from the landing page, start where she left and fly in. */
  function arriveFromHandoff() {
    let h = null;
    try { h = JSON.parse(sessionStorage.getItem(HANDOFF_KEY) ?? "null"); sessionStorage.removeItem(HANDOFF_KEY); } catch { /* ignore */ }
    if (!h || Date.now() - h.at > 15000) return false;
    pos = { x: Math.round(Math.min(document.documentElement.clientWidth - 92, Math.max(4, h.x))) + scrollX, y: Math.round(h.y) + scrollY };
    size = FULL; placed = true; navi.style.transform = `translate3d(${pos.x}px, ${pos.y}px, 0)`; publish();
    return true;
  }

  // ---- boot -----------------------------------------------------------------------------------
  syncSettings();
  const arrived = inApp ? arriveFromHandoff() : false;
  setTimeout(() => { settle(); scheduleWander(4000 + Math.random() * 3000); }, arrived ? 350 : 0);
  if (getState().naviOpen && inApp) setTimeout(() => setOpen(true, false), 500);

  return {
    /** Called by the app shell after each navigation (zones and avoid areas may have changed). */
    onRoute() { hideWhisper(); setTimeout(() => { zone = null; settle(); }, 0); if (!panel.hidden) renderSuggestions(); },
    open: () => setOpen(true),
    say, hideWhisper, goNear, handoff, playful,
    /** Called when a whisper is dismissed (click, Esc, timeout) — the tour uses it to advance. */
    set onWhisperClosed(/** @type {(() => void)|null} */ fn) { onWhisperClosed = fn; },
    set onReplayTour(/** @type {(() => void)|null} */ fn) { onReplayTour = fn; },
    get arrived() { return arrived; },
    get studying() { return studying() || document.body.classList.contains("focus-mode"); },
    get panelOpen() { return !panel.hidden; },
    route,
  };
}
