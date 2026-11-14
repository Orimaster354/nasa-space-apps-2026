// @ts-check
/**
 * Application shell: navigation, tool tabs, and view mounting.
 * All four investigation tools share ONE store (core/store.js), so switching
 * tabs never loses the object, dates, band, bookmarks or notes.
 */

import { startRouter, ROUTES, go } from "./core/router.js";
import { getState, update } from "./core/store.js";
import { esc, formatRa, formatDec, shortDate } from "./core/dom.js";
import { api, isMock } from "./api/client.js";
import { ApiClientError } from "./api/types.js";

const viewEl = /** @type {HTMLElement} */ (document.getElementById("view"));
const toolbarEl = /** @type {HTMLElement} */ (document.getElementById("toolbar"));
const titleEl = /** @type {HTMLElement} */ (document.getElementById("page-title"));
const badgeEl = /** @type {HTMLElement} */ (document.getElementById("data-badge"));

const TITLES = {
  "/investigate": "Investigate the sky",
  "/compare": "Compare observations",
  "/spectrum": "Spectral explorer",
  "/notes": "Investigation notes",
  "/learn": "Learn",
};

// Honesty marker: always visible while mock data is in use.
if (isMock()) badgeEl.innerHTML = `<span class="badge badge-illustrative">Sample / illustrative — not real SPHEREx data</span>`;

/** @param {import("./core/router.js").Route} route */
function onRoute(route) {
  titleEl.textContent = /** @type {Record<string,string>} */ (TITLES)[route.path] ?? route.title;
  document.title = `${titleEl.textContent} · SPHERE X`;

  // Primary nav + tool tabs reflect the current route.
  document.querySelectorAll("#primary-nav a[data-group]").forEach(a =>
    a.toggleAttribute("aria-current", /** @type {HTMLElement} */ (a).dataset.group === route.group));
  document.querySelectorAll("#primary-nav a[aria-current]").forEach(a => a.setAttribute("aria-current", "page"));

  toolbarEl.hidden = route.group !== "investigate";
  toolbarEl.innerHTML = ROUTES.filter(r => r.group === "investigate").map(r =>
    `<a class="tab" href="#${r.path}" ${r.path === route.path ? 'aria-current="page"' : ""}>${esc(r.title)}</a>`).join("");

  // Tab links drop the query; re-attach shared state so it is never lost.
  toolbarEl.querySelectorAll("a").forEach(a => a.addEventListener("click", e => {
    e.preventDefault();
    go(/** @type {string} */ (a.getAttribute("href")).slice(1));
  }));

  VIEWS[route.path]?.(route);
}

// ---------------------------------------------------------------------------
// M1 views. Real tool UIs arrive in M2-M7; these prove routing, store and API.
// ---------------------------------------------------------------------------

/** @type {Record<string, (route: import("./core/router.js").Route) => void>} */
const VIEWS = {
  "/investigate": renderInvestigate,
  "/compare": () => placeholder("Compare observations", "M5", "Blink, side-by-side, overlay and difference views."),
  "/spectrum": () => placeholder("Spectral explorer", "M6", "Browse the instrument's spectral bands and wavelengths."),
  "/notes": () => placeholder("Investigation notes", "M7", "Notes, bookmarks and saved investigations."),
  "/learn": () => placeholder("Learn", "M8", "Beginner explanations of infrared light and what moves in the sky."),
};

/** Summary of the shared state, so persistence across tools is visible. */
function sharedStatePanel() {
  const s = getState();
  return `<section class="panel" aria-labelledby="ss-h">
    <h2 id="ss-h">Shared investigation state</h2>
    <dl class="kv">
      <dt>Object</dt><dd>${esc(s.objectId ?? "none selected")}</dd>
      <dt>Observation</dt><dd>${esc(s.observationId ?? "none")}</dd>
      <dt>Band</dt><dd>${esc(s.bandIndex ?? "none")}</dd>
      <dt>Notes</dt><dd>${s.notes.length}</dd>
      <dt>Bookmarks</dt><dd>${s.bookmarks.length}</dd>
    </dl>
  </section>`;
}

/** @param {string} name @param {string} milestone @param {string} blurb */
function placeholder(name, milestone, blurb) {
  viewEl.innerHTML = `<div class="workspace" style="grid-template-columns:minmax(0,1fr)">
    <section class="panel"><h2>${esc(name)}</h2>
      <p>${esc(blurb)}</p><p class="muted">Planned for milestone ${esc(milestone)}.</p></section>
    ${sharedStatePanel()}</div>`;
}

function renderInvestigate() {
  viewEl.innerHTML = `<div class="workspace">
    <section class="panel" aria-labelledby="find-h">
      <h2 id="find-h">Find a target</h2>
      <form id="search-form" class="field" role="search">
        <label for="q">Name</label>
        <input class="input" id="q" name="q" placeholder="Try “Barnard”" autocomplete="off">
        <button class="btn" type="submit">Search</button>
      </form>
      <div id="search-status" role="status"></div>
    </section>
    <section class="stage"><div class="panel" id="stage"><p class="muted">The image viewer arrives in milestone M2.</p></div></section>
    <div id="side" style="display:grid;gap:var(--sp-4);align-content:start"></div>
  </div>`;

  const form = /** @type {HTMLFormElement} */ (document.getElementById("search-form"));
  const status = /** @type {HTMLElement} */ (document.getElementById("search-status"));

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const q = /** @type {HTMLInputElement} */ (form.elements.namedItem("q")).value;
    status.innerHTML = `<div class="spinner" aria-label="Searching"></div>`;
    try {
      const page = await api.searchObjects(q);
      if (!page.items.length) { status.innerHTML = `<p class="notice notice-warn">${esc(page.coverageNote ?? "No results.")}</p>`; return; }
      status.innerHTML = "";
      update({ objectId: page.items[0].objectId, coordinates: page.items[0].coordinates, observationId: null });
      await renderObject();
    } catch (err) {
      const msg = err instanceof ApiClientError ? err.message : "Unexpected error.";
      status.innerHTML = `<p class="notice notice-error">${esc(msg)}</p>`;
    }
  });

  if (getState().objectId) renderObject();
}

/** Object details + observation list for the selected object (state-driven, so it survives tab switches). */
async function renderObject() {
  const side = document.getElementById("side");
  const stage = document.getElementById("stage");
  const { objectId } = getState();
  if (!side || !stage || !objectId) return;
  try {
    const [obj, obs] = await Promise.all([api.getObject(objectId), api.listObservations(objectId)]);
    side.innerHTML = `
      <section class="panel" aria-labelledby="od-h"><h2 id="od-h">Object details</h2>
        <dl class="kv">
          <dt>Target</dt><dd>${esc(obj.name)}</dd>
          <dt>RA</dt><dd>${esc(formatRa(obj.coordinates.raDeg))}</dd>
          <dt>Dec</dt><dd>${esc(formatDec(obj.coordinates.decDeg))}</dd>
          <dt>Source</dt><dd>${esc(obj.resolver.name)}</dd>
        </dl></section>`;
    stage.innerHTML = obs.items.length
      ? `<h2 class="label">Observations (${obs.total})</h2><ul class="list">${obs.items.map(o =>
          `<li><button class="list-item" aria-selected="${o.observationId === getState().observationId}" data-id="${esc(o.observationId)}">
             <span>${esc(shortDate(o.observedAt))}</span><span class="score">${esc(o.qualitySummary)}</span></button></li>`).join("")}</ul>`
      : `<p class="notice">${esc(obs.coverageNote ?? "No observations.")}</p>`;
    stage.querySelectorAll("button[data-id]").forEach(b => b.addEventListener("click", () => {
      update({ observationId: /** @type {HTMLElement} */ (b).dataset.id ?? null });
      renderObject();
    }));
  } catch (err) {
    stage.innerHTML = `<p class="notice notice-error">${esc(err instanceof ApiClientError ? err.message : "Unexpected error.")}</p>`;
  }
}

startRouter(onRoute);
