// @ts-check
/**
 * Small status badge: is NASA's IRSA archive answering? Targets that are not saved on our server need it, so when it is
 * down a search for them can fail; this tells the visitor why. Real backend only (GET /api/v1/archive-status, a
 * frontend-only operation that never waits on the archive). Polls gently, only while the tab is visible.
 */

import { CONFIG } from "../config.js";

const LABEL = { up: "NASA archive: online", down: "NASA archive: offline", unknown: "NASA archive: checking" };

/** @param {HTMLElement} el @returns {() => void} stop */
export function mountArchiveBadge(el) {
  const badge = document.createElement("span");
  badge.className = "badge archive-badge is-unknown";
  badge.setAttribute("role", "status");
  badge.innerHTML = `<span class="dot" aria-hidden="true"></span><span class="txt">${LABEL.unknown}</span>`;
  el.appendChild(badge);
  let timer = 0, stopped = false;

  async function poll() {
    if (stopped) return;
    let status = /** @type {"up"|"down"|"unknown"} */ ("unknown"), message = "";
    try {
      if (!document.hidden) {
        const res = await fetch(CONFIG.apiBase + "/archive-status", { cache: "no-store" });
        const j = res.ok ? await res.json() : null;
        if (j && ["up", "down", "unknown"].includes(j.status)) { status = j.status; message = j.message ?? ""; }
      } else { schedule(); return; }
    } catch { /* the badge must never raise an error */ }
    if (stopped) return;
    badge.className = `badge archive-badge is-${status}`;
    badge.querySelector(".txt").textContent = LABEL[status];
    badge.title = message || (status === "down" ? "The NASA archive is not answering. Saved targets still work." : "");
    schedule(status === "unknown" ? 4000 : 30000);
  }
  function schedule(ms = 30000) { clearTimeout(timer); timer = window.setTimeout(poll, ms); }
  poll();
  return () => { stopped = true; clearTimeout(timer); badge.remove(); };
}
