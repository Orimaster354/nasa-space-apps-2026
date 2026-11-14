// @ts-check
/** Small DOM helpers shared by all views. */

/** Escape text for safe interpolation into HTML (API/user strings are never trusted). */
export function esc(/** @type {unknown} */ s) {
  return String(s ?? "").replace(/[&<>"']/g, c => /** @type {Record<string,string>} */
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

/** Format RA degrees as "17h 57m 48.50s". */
export function formatRa(/** @type {number} */ deg) {
  const h = deg / 15, hh = Math.floor(h), m = (h - hh) * 60, mm = Math.floor(m), ss = (m - mm) * 60;
  return `${hh}h ${String(mm).padStart(2, "0")}m ${ss.toFixed(2).padStart(5, "0")}s`;
}

/** Format Dec degrees as "+04° 41′ 36.2″". */
export function formatDec(/** @type {number} */ deg) {
  const sign = deg < 0 ? "−" : "+", a = Math.abs(deg), d = Math.floor(a), m = (a - d) * 60, mm = Math.floor(m), ss = (m - mm) * 60;
  return `${sign}${String(d).padStart(2, "0")}° ${String(mm).padStart(2, "0")}′ ${ss.toFixed(1).padStart(4, "0")}″`;
}

/** "Jan 16" style short date from ISO, in UTC. */
export function shortDate(/** @type {string|undefined} */ iso) {
  if (!iso) return "date n/a";
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "2-digit", timeZone: "UTC" });
}
