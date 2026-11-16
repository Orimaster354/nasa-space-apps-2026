// @ts-check
/**
 * Minimal hash router: "#/spectrum?o=abc&band=12".
 * The path picks the tool; the query is the shareable slice of the store.
 * Switching tools changes only the path, so shared state is never lost.
 */

import { fromQuery, toQuery, getState, update } from "./store.js";

/** @typedef {{ path: string, title: string, group: "investigate"|"learn"|"library"|"achievements" }} Route */

/** @type {Route[]} */
export const ROUTES = [
  { path: "/investigate", title: "Investigate", group: "investigate" },
  { path: "/spectrum",    title: "Spectrum",    group: "investigate" },
  { path: "/notes",       title: "Notes",       group: "investigate" },
  { path: "/library",     title: "Library",     group: "library" },
  { path: "/achievements", title: "Achievements", group: "achievements" },
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
    // Compare is now a mode of the Investigate workspace. Old "#/compare" links keep working.
    if (path === "/compare") {
      if (query) fromQuery(query);
      const s = getState();
      if (s.compareMode === "view") update({ compareMode: "blink" });
      const q = toQuery();
      history.replaceState(null, "", `#/investigate${q ? "?" + q : ""}`);
      return handle();
    }
    if (query) fromQuery(query);              // URL wins on arrival, so shared links restore state
    const route = ROUTES.find(r => r.path === path) ?? ROUTES[0];
    onRoute(route);
  };
  window.addEventListener("hashchange", handle);
  handle();
}
