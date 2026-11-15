// @ts-check
/**
 * Spectral band selector.
 *
 * Every band is drawn at its real position and width on a wavelength axis, using the band edges
 * the API supplies (SpectralPoint.wavelengthMinUm / wavelengthMaxUm). Spacing is never assumed
 * to be uniform. Bands without data are hatched. The colours are a display mapping of
 * wavelength only: infrared light has no visible colour.
 *
 * One focusable slider (role="slider") carries the keyboard model: ←/→ one band, PageUp/PageDown
 * ten bands, Home/End first/last. Pointer: hover shows a tooltip, click/tap picks the nearest band.
 * Previous/next buttons and a live readout show the band number and wavelength in micrometres.
 */

import { esc } from "../core/dom.js";

// Approved muted spectrum (matches --spectrum-gradient in tokens.css): blue → teal → muted gold → orange.
export const STOPS = ["#5b6fb0", "#5a8fc0", "#5fb0b8", "#8fc08a", "#d6b56a", "#e08f60", "#c8605a"];
export function gradientColor(/** @type {number} */ t) {
  const x = Math.min(0.9999, Math.max(0, t)) * (STOPS.length - 1), i = Math.floor(x), f = x - i;
  const a = STOPS[i], b = STOPS[i + 1];
  const mix = (/** @type {number} */ k) => Math.round(parseInt(a.slice(k, k + 2), 16) * (1 - f) + parseInt(b.slice(k, k + 2), 16) * f);
  return `rgb(${mix(1)},${mix(3)},${mix(5)})`;
}

/**
 * @typedef {{ bandIndex: number, wavelengthUm: number, wavelengthMinUm?: number, wavelengthMaxUm?: number, available: boolean }} BandPoint
 * @param {HTMLElement} root
 * @param {{ onSelect: (bandIndex: number) => void, label?: string }} h
 */
export function createBandSelector(root, h) {
  root.classList.add("band-selector");
  root.innerHTML = `
    <div class="bs-head">
      <button type="button" class="btn btn-icon" data-step="-1" aria-label="Previous band">‹</button>
      <p class="bs-readout" aria-live="polite"></p>
      <button type="button" class="btn btn-icon" data-step="1" aria-label="Next band">›</button>
    </div>
    <div class="bs-track" role="slider" tabindex="0" aria-label="${esc(h.label ?? "Spectral band")}" aria-orientation="horizontal">
      <div class="bs-bands"></div>
      <div class="bs-mark bs-mark2" hidden></div>
      <div class="bs-mark bs-mark1"><span class="bs-caret" aria-hidden="true"></span></div>
      <div class="bs-tip" hidden></div>
    </div>
    <div class="bs-axis" aria-hidden="true"></div>
    <p class="bs-legend fine muted"><span>Wavelength in micrometres (<span class="unit">µm</span>)</span> <span class="bs-sep">·</span> <span class="bs-key"></span> data <span class="bs-key bs-key-gap"></span> no data
      <span class="bs-sep">·</span> Colours show wavelength order only. Infrared light is invisible to the eye.</p>`;

  const track = /** @type {HTMLElement} */ (root.querySelector(".bs-track"));
  const bandsEl = /** @type {HTMLElement} */ (root.querySelector(".bs-bands"));
  const tip = /** @type {HTMLElement} */ (root.querySelector(".bs-tip"));
  /** @type {BandPoint[]} */ let pts = [];
  let sel = 0, second = /** @type {number|null} */ (null), x0 = 0, x1 = 1;

  const lo = (/** @type {BandPoint} */ p) => p.wavelengthMinUm ?? p.wavelengthUm;
  const hi = (/** @type {BandPoint} */ p) => p.wavelengthMaxUm ?? p.wavelengthUm;
  const pct = (/** @type {number} */ wl) => ((wl - x0) / (x1 - x0)) * 100;
  const fmt = (/** @type {number} */ v) => v.toFixed(v < 1 ? 3 : 2);
  const indexOf = (/** @type {number} */ band) => pts.findIndex(p => p.bandIndex === band);

  /** Band under a wavelength: the one whose edges contain it, else the nearest centre. */
  function nearest(/** @type {number} */ wl) {
    let best = pts[0];
    for (const p of pts) {
      if (wl >= lo(p) && wl <= hi(p)) return p;
      if (Math.abs(p.wavelengthUm - wl) < Math.abs(best.wavelengthUm - wl)) best = p;
    }
    return best;
  }
  const wlAt = (/** @type {number} */ clientX) => { const r = track.getBoundingClientRect(); return x0 + ((clientX - r.left) / r.width) * (x1 - x0); };

  function render() {
    if (!pts.length) return;
    x0 = lo(pts[0]); x1 = hi(pts[pts.length - 1]);
    // Narrow bands get at least a hairline so every band stays visible at small widths.
    bandsEl.innerHTML = pts.map(p => {
      const l = pct(lo(p)), w = Math.max(pct(hi(p)) - l, 0.12), c = gradientColor((p.wavelengthUm - x0) / (x1 - x0));
      return `<i class="bs-band${p.available ? "" : " is-gap"}" style="left:${l}%;width:${w}%;--c:${c}"></i>`;
    }).join("");
    // Axis: round wavelengths, placed by value (not by band count).
    const ticks = [0.75, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5].filter(v => v >= x0 - 1e-6 && v <= x1 + 1e-6);
    /** @type {HTMLElement} */ (root.querySelector(".bs-axis")).innerHTML =
      ticks.map(v => `<span class="bs-tick" style="left:${pct(v)}%">${v}</span>`).join("");
    sync();
  }

  function placeMark(/** @type {HTMLElement} */ el, /** @type {BandPoint|undefined} */ p) {
    el.hidden = !p; if (!p) return;
    const l = pct(lo(p)), w = Math.max(pct(hi(p)) - l, 0.4);
    el.style.left = `${l}%`; el.style.width = `${w}%`;
  }

  function sync() {
    const i = indexOf(sel), p = pts[i]; if (!p) return;
    placeMark(/** @type {HTMLElement} */ (root.querySelector(".bs-mark1")), p);
    placeMark(/** @type {HTMLElement} */ (root.querySelector(".bs-mark2")), second !== null ? pts[indexOf(second)] : undefined);
    /** @type {HTMLElement} */ (root.querySelector(".bs-readout")).innerHTML =
      `<strong>Band ${p.bandIndex}</strong> <span class="muted">of ${pts.length}</span>
       <span class="bs-wl mono">${fmt(p.wavelengthUm)} <span class="unit">µm</span></span>
       ${p.wavelengthMinUm !== undefined ? `<span class="muted mono fine">${fmt(lo(p))}–${fmt(hi(p))} <span class="unit">µm</span></span>` : ""}
       <span class="bs-state ${p.available ? "is-ok" : "is-gap"}">${p.available ? "data" : "no data"}</span>`;
    track.setAttribute("aria-valuemin", String(pts[0].bandIndex));
    track.setAttribute("aria-valuemax", String(pts[pts.length - 1].bandIndex));
    track.setAttribute("aria-valuenow", String(p.bandIndex));
    track.setAttribute("aria-valuetext", `Band ${p.bandIndex}, ${fmt(p.wavelengthUm)} micrometres, ${p.available ? "data available" : "no data"}`);
    /** @type {HTMLButtonElement} */ (root.querySelector('[data-step="-1"]')).disabled = i <= 0;
    /** @type {HTMLButtonElement} */ (root.querySelector('[data-step="1"]')).disabled = i >= pts.length - 1;
  }

  function step(/** @type {number} */ d) {
    const i = Math.min(pts.length - 1, Math.max(0, indexOf(sel) + d));
    if (pts[i] && pts[i].bandIndex !== sel) h.onSelect(pts[i].bandIndex);
  }

  root.addEventListener("click", e => { const s = /** @type {HTMLElement} */ (e.target).closest("[data-step]"); if (s) step(Number(s.getAttribute("data-step"))); });
  track.addEventListener("keydown", e => {
    const map = /** @type {Record<string, number>} */ ({ ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1, PageUp: 10, PageDown: -10 });
    if (e.key in map) { e.preventDefault(); step(map[e.key]); }
    else if (e.key === "Home") { e.preventDefault(); h.onSelect(pts[0].bandIndex); }
    else if (e.key === "End") { e.preventDefault(); h.onSelect(pts[pts.length - 1].bandIndex); }
  });
  track.addEventListener("pointermove", e => {
    if (!pts.length) return;
    const p = nearest(wlAt(e.clientX)), r = track.getBoundingClientRect();
    tip.hidden = false;
    tip.innerHTML = `Band ${p.bandIndex} · ${fmt(p.wavelengthUm)} <span class="unit">µm</span>${p.available ? "" : " · no data"}`;
    const x = Math.min(r.width - tip.offsetWidth / 2, Math.max(tip.offsetWidth / 2, e.clientX - r.left));
    tip.style.left = `${x}px`;
  });
  track.addEventListener("pointerleave", () => { tip.hidden = true; });
  track.addEventListener("click", e => { if (pts.length) { h.onSelect(nearest(wlAt(e.clientX)).bandIndex); track.focus(); } });

  return {
    /** @param {BandPoint[]} points @param {number} selected @param {number|null} [compared] */
    set(points, selected, compared = null) {
      const changed = points !== pts; pts = points; sel = selected; second = compared;
      if (changed) render(); else sync();
    },
  };
}
