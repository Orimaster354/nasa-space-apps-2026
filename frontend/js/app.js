// @ts-check
/**
 * Application shell: navigation, tool tabs, view lifecycle and the global companion.
 * All investigation tools share ONE store (core/store.js), so switching tabs never loses
 * the object, dates, band, bookmarks or notes. Each view is `mount(container) -> unmount`.
 */

import { startRouter, ROUTES, go } from "./core/router.js";
import { esc } from "./core/dom.js";
import { isMock } from "./api/client.js";
import { getState, update } from "./core/store.js";
import { mountNavi } from "./components/navi.js";
import { attachBrain } from "./components/naviBrain.js";
import { mountScene } from "./components/scene.js";
import * as investigate from "./views/investigate.js";
import * as spectrum from "./views/spectrum.js";
import * as notes from "./views/notes.js";
import * as learn from "./views/learn.js";

const viewEl = /** @type {HTMLElement} */ (document.getElementById("view"));
const toolbarEl = /** @type {HTMLElement} */ (document.getElementById("toolbar"));
const titleEl = /** @type {HTMLElement} */ (document.getElementById("page-title"));
const badgeEl = /** @type {HTMLElement} */ (document.getElementById("data-badge"));

/** @type {Record<string, { mount: (el: HTMLElement) => () => void, title: string }>} */
const VIEWS = {
  "/investigate": { mount: investigate.mount, title: "Investigate the sky" },
  "/spectrum": { mount: spectrum.mount, title: "Spectral explorer" },
  "/notes": { mount: notes.mount, title: "Investigation notes" },
  "/learn": { mount: learn.mount, title: "Learn" },
};

// Honesty marker: always visible while mock data is in use.
if (isMock()) badgeEl.innerHTML = `<span class="badge badge-illustrative">Sample / illustrative — not real SPHEREx data</span>`;

mountScene(/** @type {HTMLElement} */ (document.getElementById("scene")));
const navi = mountNavi({
  inApp: true,
  // Whispers never cover navigation, titles, imagery or the controls around it.
  avoid: ".brand, #primary-nav, .toolbar, .page-head h1, #data-badge, .ws-context, .ws-toolbar, .viewer-wrap, .timeline-panel, .ws-drawer, .sp-viewer, .band-selector, #sp-info",
  // The assistant may sit over a side column, but not over imagery or the main controls.
  panelAvoid: "#primary-nav, .ws-context, .ws-toolbar, .viewer-wrap, .sp-viewer, .band-selector",
  critical: ".viewer-wrap, .sp-viewer",
  panelColumn: ".ws.right-open .ws-right, #sp-info",
  // In the workspace the assistant docks in the right drawer: open it while she talks, restore afterwards.
  onPanel: open => {
    if (!document.querySelector(".ws") || !matchMedia("(min-width: 1000px)").matches) return false;
    const p = getState().panels;
    if (open && !p.right) { panelsBeforeNavi = p; update({ panels: { ...p, right: true } }); return true; }
    if (!open && panelsBeforeNavi) { update({ panels: panelsBeforeNavi }); panelsBeforeNavi = null; }
    return false;
  },
});
/** @type {{left: boolean, right: boolean}|null} */ let panelsBeforeNavi = null;
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
