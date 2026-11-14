// @ts-check
/**
 * Minimal hash router: "#/compare?o=abc&band=12".
 * The path picks the tool; the query is the shareable slice of the store.
 * Switching tools changes only the path, so shared state is never lost.
 */

import { fromQuery, toQuery } from "./store.js";

/** @typedef {{ path: string, title: string, group: "investigate"|"learn" }} Route */

/** @type {Route[]} */
export const ROUTES = [
  { path: "/investigate", title: "Investigate", group: "investigate" },
  { path: "/compare",     title: "Compare",     group: "investigate" },
  { path: "/spectrum",    title: "Spectrum",    group: "investigate" },
  { path: "/notes",       title: "Notes",       group: "investigate" },
  { path: "/learn",       title: "Learn",       group: "learn" },
];

/** @returns {{ path: string, query: string }} */
export function currentLocation() {
  const hash = window.location.hash.replace(/^#/, "") || "/investigate";
  const [path, query = ""] = hash.split("?");
  return { path, query };
}

/** Navigate to a tool, carrying the shared state in the URL. */
export function go(/** @type {string} */ path) {
  const q = toQuery();
  window.location.hash = q ? `${path}?${q}` : path;
}

/**
 * Start routing. `onRoute` receives the matched route on every navigation.
 * @param {(route: Route) => void} onRoute
 */
export function startRouter(onRoute) {
  const handle = () => {
    const { path, query } = currentLocation();
    if (query) fromQuery(query);              // URL wins on arrival, so shared links restore state
    const route = ROUTES.find(r => r.path === path) ?? ROUTES[0];
    onRoute(route);
  };
  window.addEventListener("hashchange", handle);
  handle();
}
