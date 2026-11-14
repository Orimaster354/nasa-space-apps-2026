// @ts-check
/**
 * Notes, bookmarks, history and saved investigations. Working copies live in the shared store
 * (so they follow you between tools); "Save" snapshots them through the investigations API
 * (mock: localStorage; real: backend, OD-7).
 */

import { api } from "../api/client.js";
import { ApiClientError } from "../api/types.js";
import { getState, update, subscribe } from "../core/store.js";
import { esc, shortDate } from "../core/dom.js";
import { go } from "../core/router.js";

const errMsg = (/** @type {unknown} */ e) => (e instanceof ApiClientError ? e.message : "Something unexpected went wrong.");
const when = (/** @type {string} */ iso) => new Date(iso).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const EVENT_LABEL = /** @type {Record<string,string>} */ ({ object_opened: "Opened target", observation_viewed: "Viewed observation", comparison_created: "Compared observations",
  spectrum_viewed: "Viewed spectrum", measurement_created: "Requested measurement" });

/** @param {HTMLElement} root @returns {() => void} */
export function mount(root) {
  root.innerHTML = `<div class="notes">
    <section class="panel" aria-labelledby="n-h">
      <h2 id="n-h">Notes</h2>
      <label class="label" for="inv-title">Investigation title</label>
      <input class="input" id="inv-title" maxlength="80">
      <form id="note-form" class="field" style="margin-top:var(--sp-4)">
        <label for="note-text">New note</label>
        <textarea class="input" id="note-text" rows="3" placeholder="What did you notice?"></textarea>
        <label class="inline"><input type="checkbox" id="note-attach" checked> <span class="fine">Attach to the current observation</span></label>
        <button class="btn" type="submit">Add note</button>
      </form>
      <ul class="list notes-list" id="note-list"></ul>
    </section>

    <section class="panel" aria-labelledby="b-h">
      <h2 id="b-h">Bookmarks</h2>
      <div class="row"><button class="btn" id="bm-obs">Bookmark current observation</button><button class="btn" id="bm-obj">Bookmark target</button></div>
      <ul class="list" id="bm-list"></ul>
    </section>

    <section class="panel" aria-labelledby="s-h">
      <h2 id="s-h">Saved investigations</h2>
      <div class="row"><button class="btn" id="save">Save current investigation</button><button class="btn" id="new">Start a new one</button></div>
      <div id="save-status" role="status"></div>
      <ul class="list" id="saved-list"></ul>
    </section>

    <section class="panel" aria-labelledby="sum-h">
      <h2 id="sum-h">Summary</h2>
      <button class="btn" id="summarise">Ask Navi to summarise</button>
      <div id="summary" role="status"></div>
    </section>

    <section class="panel" aria-labelledby="h-h">
      <h2 id="h-h">History</h2>
      <ol class="history" id="hist"></ol>
    </section>
  </div>`;

  const $ = (/** @type {string} */ s) => /** @type {HTMLElement} */ (root.querySelector(s));
  let alive = true;
  const title = /** @type {HTMLInputElement} */ ($("#inv-title"));
  title.value = getState().title;
  title.addEventListener("input", () => update({ title: title.value }));

  function render() {
    const s = getState();
    $("#note-list").innerHTML = s.notes.length ? s.notes.map(n => `<li class="note"><p>${esc(n.text)}</p>
      <span class="muted fine">${esc(when(n.createdAt))}${n.observationId ? " · " + esc(n.observationId) : ""}</span>
      <button class="btn" data-del-note="${esc(n.noteId)}" aria-label="Delete note">Delete</button></li>`).join("") : `<li class="muted">No notes yet.</li>`;
    $("#bm-list").innerHTML = s.bookmarks.length ? s.bookmarks.map(b => `<li class="bm"><button class="list-item" data-open="${esc(b.targetType)}|${esc(b.targetId)}"><span>${esc(b.label ?? b.targetId)}</span><span class="score">${esc(b.targetType)}</span></button>
      <button class="btn" data-del-bm="${esc(b.bookmarkId)}" aria-label="Remove bookmark">✕</button></li>`).join("") : `<li class="muted">No bookmarks yet.</li>`;
    $("#hist").innerHTML = s.history.length ? [...s.history].reverse().slice(0, 40).map(h => `<li><span>${esc(EVENT_LABEL[h.type] ?? h.type)}</span><span class="muted fine">${esc(when(h.at))}</span></li>`).join("") : `<li class="muted">Nothing yet. Open a target to begin.</li>`;
    /** @type {HTMLInputElement} */ ($("#note-attach")).disabled = !s.observationId;
    /** @type {HTMLButtonElement} */ ($("#bm-obs")).disabled = !s.observationId;
    /** @type {HTMLButtonElement} */ ($("#bm-obj")).disabled = !s.objectId;
  }

  $("#note-form").addEventListener("submit", e => {
    e.preventDefault();
    const ta = /** @type {HTMLTextAreaElement} */ ($("#note-text")), text = ta.value.trim(); if (!text) return;
    const s = getState(), attach = /** @type {HTMLInputElement} */ ($("#note-attach")).checked && s.observationId;
    update({ notes: [{ noteId: `note-${Date.now().toString(36)}`, text, createdAt: new Date().toISOString(), ...(attach ? { observationId: s.observationId } : {}) }, ...s.notes] });
    ta.value = "";
  });
  const addBookmark = (/** @type {"object"|"observation"} */ t, /** @type {string|null} */ id, /** @type {string} */ label) => {
    if (!id) return; const s = getState();
    if (s.bookmarks.some(b => b.targetType === t && b.targetId === id)) return;
    update({ bookmarks: [{ bookmarkId: `bm-${Date.now().toString(36)}`, targetType: t, targetId: id, label, createdAt: new Date().toISOString() }, ...s.bookmarks] });
  };
  $("#bm-obs").addEventListener("click", () => addBookmark("observation", getState().observationId, `Observation ${getState().observationId}`));
  $("#bm-obj").addEventListener("click", () => addBookmark("object", getState().objectId, `Target ${getState().objectId}`));
  root.addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target);
    const dn = t.closest("[data-del-note]")?.getAttribute("data-del-note"); if (dn) update({ notes: getState().notes.filter(n => n.noteId !== dn) });
    const db = t.closest("[data-del-bm]")?.getAttribute("data-del-bm"); if (db) update({ bookmarks: getState().bookmarks.filter(b => b.bookmarkId !== db) });
    const op = t.closest("[data-open]")?.getAttribute("data-open");
    if (op) { const [type, id] = op.split("|"); update(type === "object" ? { objectId: id, observationId: null } : { observationId: id }); go("/investigate"); }
  });

  // ---- saved investigations --------------------------------------------------------
  async function loadSaved() {
    try {
      const page = await api.listInvestigations();
      if (!alive) return;
      $("#saved-list").innerHTML = page.items.length ? page.items.map((/** @type {any} */ i) => `<li class="bm"><button class="list-item" data-load="${esc(i.investigationId)}"><span>${esc(i.title)}</span><span class="score">${esc(when(i.updatedAt))}</span></button>
        <button class="btn" data-del-inv="${esc(i.investigationId)}" aria-label="Delete saved investigation">✕</button></li>`).join("") : `<li class="muted">Nothing saved yet.</li>`;
    } catch (e) { $("#saved-list").innerHTML = `<li><p class="notice notice-error">${esc(errMsg(e))}</p></li>`; }
  }
  $("#save").addEventListener("click", async () => {
    const s = getState(), st = $("#save-status");
    try {
      await api.createInvestigation({ title: s.title, ...(s.objectId ? { objectId: s.objectId } : {}), notes: s.notes, bookmarks: s.bookmarks, history: s.history });
      st.innerHTML = `<p class="notice">Saved.</p>`; loadSaved();
    } catch (e) { st.innerHTML = `<p class="notice notice-error">${esc(errMsg(e))}</p>`; }
  });
  $("#new").addEventListener("click", () => { update({ title: "Untitled investigation", notes: [], bookmarks: [], history: [], messages: [], guided: null, candidateId: null }); title.value = getState().title; });
  $("#saved-list").addEventListener("click", async e => {
    const t = /** @type {HTMLElement} */ (e.target), st = $("#save-status");
    const del = t.closest("[data-del-inv]")?.getAttribute("data-del-inv");
    const load = t.closest("[data-load]")?.getAttribute("data-load");
    try {
      if (del) { await api.deleteInvestigation(del); loadSaved(); }
      else if (load) {
        const inv = await api.getInvestigation(load);
        update({ title: inv.title, objectId: inv.objectId ?? null, observationId: null, notes: inv.notes, bookmarks: inv.bookmarks, history: inv.history });
        title.value = inv.title; st.innerHTML = `<p class="notice">Loaded “${esc(inv.title)}”.</p>`;
      }
    } catch (err) { st.innerHTML = `<p class="notice notice-error">${esc(errMsg(err))}</p>`; }
  });

  // ---- summary (asks the companion; mock reply is scripted) -----------------------------
  $("#summarise").addEventListener("click", async () => {
    const s = getState(), out = $("#summary");
    out.innerHTML = `<div class="spinner" aria-label="Thinking"></div>`;
    try {
      const r = await api.sendChatMessage({ message: "Summarise my investigation", context: { activeView: "workspace", ...(s.objectId ? { objectId: s.objectId } : {}) } }, { mode: s.naviMode });
      out.innerHTML = `<p>${esc(r.message.content)}</p>${r.caveats.map((/** @type {string} */ c) => `<p class="notice notice-warn fine">${esc(c)}</p>`).join("")}`;
    } catch (e) { out.innerHTML = `<p class="notice notice-error">${esc(errMsg(e))}</p>`; }
  });

  const unsub = subscribe((_s, patch) => { if ("notes" in patch || "bookmarks" in patch || "history" in patch || "observationId" in patch || "objectId" in patch) render(); });
  render(); loadSaved();
  return () => { alive = false; unsub(); };
}
