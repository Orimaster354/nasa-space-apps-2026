// @ts-check
/**
 * Application shell: navigation, tool tabs, view lifecycle and the global companion.
 * All investigation tools share ONE store (core/store.js), so switching tabs never loses
 * the object, dates, band, bookmarks or notes. Each view is `mount(container) -> unmount`.
 */

import { startRouter, ROUTES, go } from "./core/router.js";
import { esc } from "./core/dom.js";
import { isMock } from "./api/client.js";
import { mountNavi } from "./components/navi.js";
import { mountArchiveBadge } from "./components/archiveBadge.js";
import { attachBrain } from "./components/naviBrain.js";
import { mountScene } from "./components/scene.js";
import * as investigate from "./views/investigate.js";
import * as spectrum from "./views/spectrum.js";
import * as notes from "./views/notes.js";
import * as learn from "./views/learn.js";
import * as library from "./views/library.js";

const viewEl = /** @type {HTMLElement} */ (document.getElementById("view"));
const toolbarEl = /** @type {HTMLElement} */ (document.getElementById("toolbar"));
const titleEl = /** @type {HTMLElement} */ (document.getElementById("page-title"));
const badgeEl = /** @type {HTMLElement} */ (document.getElementById("data-badge"));

/** @type {Record<string, { mount: (el: HTMLElement) => () => void, title: string }>} */
const VIEWS = {
  "/investigate": { mount: investigate.mount, title: "Investigate the sky" },
  "/spectrum": { mount: spectrum.mount, title: "Spectral explorer" },
  "/notes": { mount: notes.mount, title: "Investigation notes" },
  "/library": { mount: library.mount, title: "Library of stored targets" },
  "/learn": { mount: learn.mount, title: "Learn" },
};

// Honesty marker: always visible while mock data is in use.
if (isMock()) badgeEl.innerHTML = `<span class="badge badge-illustrative">Sample / illustrative — not real SPHEREx data</span>`;

if (!isMock()) mountArchiveBadge(badgeEl);          // real backend: show whether NASA's archive is answering

mountScene(/** @type {HTMLElement} */ (document.getElementById("scene")));
const navi = mountNavi({
  inApp: true,
  // Whispers never cover navigation, titles, imagery or the controls around it.
  avoid: ".brand, #primary-nav, .toolbar, .page-head h1, #data-badge, .ws-context, .ws-toolbar, .viewer-wrap, .timeline-panel, .ws-drawer, .sp-viewer, .band-selector, #sp-info",
  // The assistant may sit over a side column, but not over imagery or the main controls.
  panelAvoid: "#primary-nav, .ws-context, .ws-toolbar, .viewer-wrap, .sp-viewer, .band-selector",
  critical: ".viewer-wrap, .sp-viewer",
  // The chat is a floating window with its own open state (store.naviOpen); it never opens, closes or
  // docks into the Details drawer (store.panels.right), and the drawer never affects it.
});
const brain = attachBrain(navi);
/** @type {(() => void)|null} */ let unmount = null;

/** @param {import("./core/router.js").Route} route */
function onRoute(route) {
  const view = VIEWS[route.path];
  titleEl.textContent = view.title;
  document.title = `${view.title} · SPHERE X`;

  document.querySelectorAll("#primary-nav a[data-group]").forEach(a => {
    if (/** @type {HTMLElement} */ (a).dataset.group === route.group) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });

  toolbarEl.hidden = route.group !== "investigate";
  toolbarEl.innerHTML = ROUTES.filter(r => r.group === "investigate").map(r =>
    `<a class="tab" href="#${r.path}" ${r.path === route.path ? 'aria-current="page"' : ""}>${esc(r.title)}</a>`).join("");
  // Tab links go through go() so the shared state is carried in the URL.
  toolbarEl.querySelectorAll("a").forEach(a => a.addEventListener("click", e => { e.preventDefault(); go(/** @type {string} */ (a.getAttribute("href")).slice(1)); }));

  // Replace the previous view cleanly (stops timers, unsubscribes, frees viewers).
  unmount?.();
  viewEl.innerHTML = "";
  unmount = view.mount(viewEl);
  navi.onRoute(); brain.onRoute();
}

startRouter(onRoute);
