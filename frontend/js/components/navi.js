// @ts-check
/**
 * Navi, the research companion. Floats near the viewer on every workspace page with the
 * original sprite and a contextual speech bubble; "Chat" opens an in-place panel.
 * The browser never calls an LLM: replies come from api.sendChatMessage (mock: scripted).
 * Answers render as: explanation, "Based on" references, caveats, and next-step actions.
 */

import { api, isMock } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, subscribe } from "../core/store.js";
import { esc } from "../core/dom.js";
import { go, currentLocation } from "../core/router.js";
import { loadGuided, guidedHint } from "./guided.js";

const BUBBLES = /** @type {Record<string,string>} */ ({
  "/investigate": "Spot the shift.", "/compare": "Do these two dates match?", "/spectrum": "Each band is a different infrared colour.",
  "/notes": "Write down what you notice.", "/learn": "Ask me anything.",
});
const VIEW_OF = /** @type {Record<string,string>} */ ({ "/investigate": "viewer", "/compare": "comparison", "/spectrum": "spectrum", "/notes": "workspace", "/learn": "workspace" });
const KEY_OF = /** @type {Record<string,string>} */ ({ "/investigate": "investigate", "/compare": "compare", "/spectrum": "spectrum", "/notes": "notes", "/learn": "learn" });

/** @param {HTMLElement} host */
export function mountNavi(host) {
  host.innerHTML = `
    <div class="navi-dock">
      <p class="navi-bubble" id="navi-bubble" role="status"></p>
      <button class="navi-sprite" id="navi-sprite" aria-label="Open Navi, your research companion"><img src="assets/navi/navi.png" alt="" width="88" height="84"></button>
      <button class="btn chat-btn" id="navi-chat" aria-expanded="false" aria-controls="navi-panel">💬 Chat</button>
    </div>
    <section class="navi-panel" id="navi-panel" role="dialog" aria-label="Navi, research companion" hidden>
      <header>
        <img src="assets/navi/navi.png" alt="" width="44" height="42">
        <div class="navi-title"><strong class="pixel">Navi</strong><span class="muted fine">${isMock() ? "Illustrative replies · no AI model" : "Research companion"}</span></div>
        <div class="seg" role="radiogroup" aria-label="Explanation level">
          <button class="tab" role="radio" data-mode="beginner" aria-checked="true">Beginner</button>
          <button class="tab" role="radio" data-mode="advanced" aria-checked="false">Advanced</button>
        </div>
        <button class="btn btn-icon" id="navi-close" aria-label="Close chat">✕</button>
      </header>
      <div class="navi-log" id="navi-log" aria-live="polite" tabindex="0" aria-label="Conversation"></div>
      <div class="navi-sugg" id="navi-sugg" aria-label="Suggested questions"></div>
      <form class="navi-form" id="navi-form">
        <label class="sr-only" for="navi-input">Ask Navi</label>
        <input class="input" id="navi-input" placeholder="Ask about what you see…" autocomplete="off" maxlength="300">
        <button class="btn" type="submit" id="navi-send">Ask</button>
      </form>
    </section>`;

  const $ = (/** @type {string} */ s) => /** @type {HTMLElement} */ (host.querySelector(s));
  const panel = $("#navi-panel"), log = $("#navi-log"), input = /** @type {HTMLInputElement} */ ($("#navi-input"));
  let thinking = false, failed = /** @type {string|null} */ (null), guidedDef = /** @type {any} */ (null);
  const route = () => currentLocation().path;

  // ---- open / close ---------------------------------------------------------------
  function setOpen(/** @type {boolean} */ open, focus = true) {
    update({ naviOpen: open });
    panel.hidden = !open; $("#navi-chat").setAttribute("aria-expanded", String(open));
    $(".navi-dock").classList.toggle("is-open", open);
    if (open) { renderLog(); renderSuggestions(); if (focus) input.focus(); } else if (focus) $("#navi-chat").focus();
  }
  const toggle = () => setOpen(panel.hidden);
  $("#navi-chat").addEventListener("click", toggle);
  $("#navi-sprite").addEventListener("click", toggle);
  $("#navi-close").addEventListener("click", () => setOpen(false));
  panel.addEventListener("keydown", e => { if (e.key === "Escape") setOpen(false); });

  // ---- mode toggle -------------------------------------------------------------------
  function syncMode() { host.querySelectorAll("[data-mode]").forEach(b => b.setAttribute("aria-checked", String(/** @type {HTMLElement} */ (b).dataset.mode === getState().naviMode))); }
  host.querySelectorAll("[data-mode]").forEach(b => b.addEventListener("click", () => { update({ naviMode: /** @type {any} */ (/** @type {HTMLElement} */ (b).dataset.mode) }); syncMode(); }));

  // ---- bubble ----------------------------------------------------------------------------
  function refreshBubble() {
    const s = getState();
    $("#navi-bubble").textContent = s.guided ? guidedHint(guidedDef, s.guided) : BUBBLES[route()] ?? "Spot the shift.";
  }

  // ---- messages --------------------------------------------------------------------------------
  const para = (/** @type {string} */ t) => t.split(/\n\n+/).map(p => `<p>${esc(p)}</p>`).join("");

  function renderLog() {
    const msgs = getState().messages;
    log.innerHTML = (msgs.length ? msgs.map(renderMessage).join("") : `<div class="msg msg-ai"><div class="bubble">${para(getState().naviMode === "beginner"
      ? "Hi, I'm Navi. I can explain what you're looking at, why something is listed as a candidate, and what to try next."
      : "Navi here. Ask about candidates, comparisons, uncertainty or spectra. Replies cite what they rely on and list caveats.")}</div></div>`)
      + (thinking ? `<div class="msg msg-ai"><div class="bubble thinking" aria-label="Navi is thinking"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span> thinking</div></div>` : "")
      + (failed ? `<div class="msg"><p class="notice notice-error">${esc(failed)} <button class="btn" id="navi-retry">Try again</button></p></div>` : "");
    log.scrollTop = log.scrollHeight;
  }

  /** @param {any} m @param {number} i */
  function renderMessage(m, i) {
    if (m.role === "user") return `<div class="msg msg-user"><div class="bubble">${esc(m.content)}</div></div>`;
    const r = m.response;
    const refs = r?.references?.length ? `<div class="refs"><span class="label">Based on</span> ${r.references.map((/** @type {any} */ x) =>
      `<button class="chip" data-ref="${esc(x.targetType)}|${esc(x.targetId)}">${esc(x.targetType)}: ${esc(x.targetId.replace(/^mock-(obj|obs)-/, ""))}</button>`).join(" ")}</div>` : "";
    const cav = r?.caveats?.length ? `<div class="caveats"><span class="label">Keep in mind</span><ul>${r.caveats.map((/** @type {string} */ c) => `<li>${esc(c)}</li>`).join("")}</ul></div>` : "";
    const act = r?.suggestedActions?.length ? `<div class="actions"><span class="label">Next steps</span> ${r.suggestedActions.map((/** @type {any} */ a, /** @type {number} */ j) =>
      `<button class="btn" data-act="${i}|${j}">${esc(a.label)}</button>`).join(" ")}</div>` : "";
    return `<div class="msg msg-ai"><div class="bubble">${para(m.content)}${refs}${cav}${act}</div></div>`;
  }

  log.addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target);
    if (t.id === "navi-retry") { const last = [...getState().messages].reverse().find(m => m.role === "user"); failed = null; if (last) ask(last.content, true); return; }
    const ref = t.closest("[data-ref]")?.getAttribute("data-ref");
    if (ref) { const [type, id] = ref.split("|"); update(type === "object" ? { objectId: id, observationId: null } : type === "observation" ? { observationId: id } : {}); go("/investigate"); return; }
    const act = t.closest("[data-act]")?.getAttribute("data-act");
    if (act) { const [i, j] = act.split("|").map(Number); const a = getState().messages[i]?.response?.suggestedActions?.[j]; if (a) runAction(a); }
  });

  /** Suggested next steps navigate or change shared state; nothing is computed here. */
  function runAction(/** @type {any} */ a) {
    const s = getState();
    switch (a.kind) {
      case "open_comparison": case "run_comparison": {
        const mode = a.params?.mode;
        update({ ...(mode ? { compareMode: mode } : {}) });
        go("/compare"); break;
      }
      case "open_spectrum": go("/spectrum"); break;
      case "open_observation": if (a.targetId) update({ observationId: a.targetId }); go("/investigate"); break;
      case "add_note":
        update({ notes: [{ noteId: `note-${Date.now().toString(36)}`, text: String(a.params?.text ?? ""), createdAt: new Date().toISOString(), ...(s.observationId ? { observationId: s.observationId } : {}) }, ...s.notes] });
        pushMessage({ role: "assistant", content: "Added to your notes.", at: new Date().toISOString() }); break;
    }
  }

  function pushMessage(/** @type {any} */ m) { update({ messages: [...getState().messages.slice(-39), m] }); renderLog(); }

  // ---- context + sending -----------------------------------------------------------------------------
  function buildContext() {
    const s = getState(), r = route();
    const observationIds = r === "/compare" ? [s.compareBaseId, s.compareTargetId].filter(Boolean) : s.observationId ? [s.observationId] : [];
    return { activeView: VIEW_OF[r] ?? "workspace", ...(s.objectId ? { objectId: s.objectId } : {}), observationIds,
             ...(s.candidateId ? { candidateId: s.candidateId } : {}) };
  }

  /** @param {string} text @param {boolean} [isRetry] */
  async function ask(text, isRetry = false) {
    text = text.trim(); if (!text || thinking) return;
    if (!isRetry) pushMessage({ role: "user", content: text, at: new Date().toISOString() });
    thinking = true; failed = null; renderLog(); $("#navi-sprite").classList.add("thinking");
    input.disabled = true; /** @type {HTMLButtonElement} */ ($("#navi-send")).disabled = true;
    try {
      const r = await api.sendChatMessage({ message: text, context: buildContext() }, { mode: getState().naviMode });
      thinking = false;
      if (r.availability.status !== "available") failed = r.message?.content || "Navi is unavailable right now.";
      else pushMessage({ role: "assistant", content: r.message.content, response: r, at: r.message.createdAt });
    } catch (e) {
      thinking = false; failed = e instanceof ApiClientError ? e.message : "Navi couldn't answer. Please try again.";
    } finally {
      $("#navi-sprite").classList.remove("thinking"); input.disabled = false; /** @type {HTMLButtonElement} */ ($("#navi-send")).disabled = false;
      renderLog(); renderSuggestions(); input.focus();
    }
  }

  $("#navi-form").addEventListener("submit", e => { e.preventDefault(); const v = input.value; input.value = ""; ask(v); });

  async function renderSuggestions() {
    let list = [];
    try { list = await api.getChatSuggestions(KEY_OF[route()] ?? "default"); } catch { /* suggestions are optional */ }
    $("#navi-sugg").innerHTML = list.map((/** @type {string} */ q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join("");
  }
  $("#navi-sugg").addEventListener("click", e => { const q = /** @type {HTMLElement} */ (e.target).closest("[data-q]")?.getAttribute("data-q"); if (q) ask(q); });

  // ---- boot / sync ------------------------------------------------------------------------------------------
  loadGuided().then(d => { guidedDef = d; refreshBubble(); });
  subscribe((_s, patch) => { if ("guided" in patch) refreshBubble(); if ("candidateId" in patch && !panel.hidden) renderSuggestions(); });
  syncMode(); refreshBubble();
  if (getState().naviOpen) setOpen(true, false);

  return {
    /** Called by the shell after each navigation. */
    onRoute() { refreshBubble(); if (!panel.hidden) renderSuggestions(); },
    open: () => setOpen(true),
  };
}
