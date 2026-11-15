// @ts-check
/**
 * Library: the targets stored on the server, which keep working when NASA's archive is unreachable, with what is stored for each
 * (frames, spectrum, candidates), a one-click stacked image, and the data credits.
 * Real backend only (frontend-only operations GET /library, /credits, /objects/{id}/coadd).
 */

import { api, isMock } from "../api/client.js";
import { update } from "../core/store.js";
import { go } from "../core/router.js";
import { esc } from "../core/dom.js";

const num = (/** @type {number} */ v, d = 2) => (Number.isFinite(v) ? v.toFixed(d) : "n/a");
const errMsg = (/** @type {any} */ e) => (e && e.message) || "Something went wrong.";

/** @param {any} t */
function spectrumLine(t) {
  const s = t.spectrum;
  if (!s) return `<span class="muted">Spectrum: not stored yet (built on first visit, about 25 s)</span>`;
  if (s.status === "available") return `Spectrum: ${s.bands} bands, ${num(s.fromUm)} to ${num(s.toUm)} µm`;
  return `<span class="muted">Spectrum: not possible here. ${esc((s.reason || "").slice(0, 110))}</span>`;
}

/** @param {any} t */
function candidateLine(t) {
  const c = t.candidates;
  if (!c) return `<span class="muted">Candidates: not searched yet</span>`;
  return c.count ? `Candidates: ${c.count} to look at` : `<span class="muted">Candidates: none stand out</span>`;
}

/** @param {any} t */
function card(t) {
  const bands = Object.entries(t.storedByBand || {}).map(([b, n]) => `<span class="badge lib-band">${esc(b.replace("SPHEREx-", ""))} · ${n}</span>`).join(" ");
  const pm = t.properMotion ? ` · moves ${num(t.properMotion.value, 2)} ${esc(t.properMotion.unit)}` : "";
  return `<article class="panel lib-card" data-id="${esc(t.objectId)}">
    <h2>${esc(t.name)}</h2>
    <p class="fine muted">${t.objectType ? esc(t.objectType) + " · " : ""}RA ${num(t.coordinates.raDeg, 3)}°, Dec ${num(t.coordinates.decDeg, 3)}°${pm}</p>
    <p><strong>${t.storedFrames}</strong> stored frames <span class="muted">of ${t.observationsListed} listed</span> ${bands}</p>
    <p class="fine">${spectrumLine(t)}</p>
    <p class="fine">${candidateLine(t)}</p>
    <div class="lib-actions">
      <button class="btn" data-a="open">Investigate</button>
      <button class="btn" data-a="spectrum">Spectrum</button>
      <button class="btn" data-a="stack">Stacked image</button>
    </div>
    <div class="lib-stack" aria-live="polite"></div>
  </article>`;
}

/** @param {any} j */
function stackHtml(j) {
  if (j.availability.status !== "available") return `<p class="notice notice-warn fine">${esc(j.availability.statusReason || "No stack is available.")}</p>`;
  const n = j.noise;
  return `<figure class="lib-stackfig"><img src="${esc(j.imageUrl)}" alt="Stacked image of ${j.frameCount} frames" width="${j.width}" height="${j.height}">
    <figcaption class="fine"><strong>Derived stack, not a NASA observation.</strong> ${esc(j.method)}
      ${n.measuredGain >= 1.05 ? `Noise per pixel is ${num(n.measuredGain)}× lower than in one frame` : "No clear noise improvement over one frame"} (ideal ${num(n.idealGain)}×; whole picture ${num(n.largeScaleGain)}×).
      ${j.frameCount} frames, ${esc((j.observedFrom || "").slice(0, 10))} to ${esc((j.observedTo || "").slice(0, 10))}.</figcaption></figure>
    <ul class="fine lib-warn">${j.warnings.slice(0, 4).map((/** @type {string} */ w) => `<li>${esc(w)}</li>`).join("")}</ul>`;
}

/** @param {any} c */
function creditsHtml(c) {
  const ack = c.acknowledgments.map((/** @type {any} */ a) => `<li><strong>${esc(a.label)}.</strong> “${esc(a.text)}”${a.reference ? ` <span class="muted">${esc(a.reference)}.</span>` : ""}
      <a href="${esc(a.source)}" target="_blank" rel="noopener">source</a></li>`).join("");
  const ds = c.datasets.map((/** @type {any} */ d) => `<li>${esc(d.author)}: ${esc(d.name)}. <a href="${esc(d.url)}" target="_blank" rel="noopener">doi:${esc(d.doi)}</a></li>`).join("");
  const pp = c.papers.map((/** @type {any} */ p) => `<li><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.label)}</a></li>`).join("");
  const rr = c.resources.map((/** @type {any} */ r) => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.label)}</a>${r.note ? ` <span class="muted">${esc(r.note)}</span>` : ""}</li>`).join("");
  return `<section class="panel lib-credits" id="credits"><h2>Data credits and acknowledgments</h2>
    <p>${esc(c.dataNote)}</p><p>${esc(c.aiNote)}</p>
    <h3>Acknowledgments</h3><ul>${ack}</ul>
    <h3>Datasets</h3><ul>${ds}</ul>
    <h3>Papers describing SPHEREx</h3><ul>${pp}</ul>
    <h3>Other resources</h3><ul>${rr}</ul>
    <p class="fine muted">Acknowledgment wording copied from NASA IRSA's pages on ${esc(c.retrievedOn)}.</p></section>`;
}

/** @param {HTMLElement} root @returns {() => void} */
export function mount(root) {
  let alive = true;
  if (isMock()) {
    root.innerHTML = `<p class="notice notice-warn">The library lists what is stored on the live server, so it is only available with the real backend.</p>`;
    return () => { root.innerHTML = ""; };
  }
  root.innerHTML = `<div class="library"><section class="panel" id="lib-intro"><div class="stage-msg"><div class="spinner" aria-hidden="true"></div><p>Loading the library…</p></div></section>
    <div class="lib-grid" id="lib-grid"></div><div id="lib-credits"></div></div>`;
  const intro = /** @type {HTMLElement} */ (root.querySelector("#lib-intro")), grid = /** @type {HTMLElement} */ (root.querySelector("#lib-grid"));

  Promise.all([api.getLibrary(), api.getCredits()]).then(([lib, credits]) => {
    if (!alive) return;
    const t = lib.totals;
    intro.innerHTML = `<h2>Stored on this server</h2>
      <p>${t.targets} targets, ${t.storedFrames} frames, ${t.storedMegabytes} MB. ${esc(lib.note)}</p>
      <p class="fine muted">Anything else you search for needs NASA's archive; the badge at the top shows whether it is answering. <a href="#credits" data-a="credits">Data credits</a></p>`;
    grid.innerHTML = lib.items.length ? lib.items.map(card).join("") : `<p class="notice">Nothing is stored on this server yet.</p>`;
    /** @type {HTMLElement} */ (root.querySelector("#lib-credits")).innerHTML = creditsHtml(credits);
  }).catch(e => { intro.innerHTML = `<p class="notice notice-error">${esc(errMsg(e))}</p>`; });

  root.addEventListener("click", async e => {
    const el = /** @type {HTMLElement} */ (e.target);
    if (el.closest("[data-a=credits]")) { e.preventDefault(); root.querySelector("#credits")?.scrollIntoView({ behavior: "smooth", block: "start" }); return; }
    const btn = /** @type {HTMLElement|null} */ (el.closest("[data-a]"));
    const cardEl = /** @type {HTMLElement|null} */ (el.closest(".lib-card"));
    if (!btn || !cardEl) return;
    const id = /** @type {string} */ (cardEl.dataset.id), a = btn.dataset.a;
    if (a === "open" || a === "spectrum") { update({ objectId: id, observationId: null, candidateId: null }); go(a === "open" ? "/investigate" : "/spectrum"); return; }
    if (a === "stack") {
      const box = /** @type {HTMLElement} */ (cardEl.querySelector(".lib-stack"));
      box.innerHTML = `<div class="stage-msg"><div class="spinner" aria-hidden="true"></div><p>Stacking frames…</p></div>`;
      try { const j = await api.getCoadd(id); if (alive) box.innerHTML = stackHtml(j); }
      catch (err) { if (alive) box.innerHTML = `<p class="notice notice-error fine">${esc(errMsg(err))}</p>`; }
    }
  });
  return () => { alive = false; root.innerHTML = ""; };
}
