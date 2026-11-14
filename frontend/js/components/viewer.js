// @ts-check
/**
 * Astronomical image viewer.
 *
 * Display-only: it shows a raster delivered through the API (`imageUrl`) and applies a CSS
 * transform for zoom/pan plus cosmetic brightness/contrast. It performs NO alignment,
 * subtraction, measurement or sky maths (pixel->sky is an API call made by the view).
 * Image data is passed in through setImage(), so real SPHEREx rasters replace mock ones
 * without touching this file.
 *
 * Interaction: wheel / buttons / "+" "-" to zoom, drag or arrow keys to pan, "0" to reset, "f" fullscreen.
 */

import { esc } from "../core/dom.js";

/**
 * @typedef {{ id: string, x: number, y: number, r?: number, label?: string, selected?: boolean }} Marker
 * @typedef {{ zoom: number, cx: number, cy: number }} ViewState  zoom is relative to "fit"; cx/cy are the image-pixel point at the centre.
 * @typedef {Object} ViewerOptions
 * @property {string} [label]                      accessible name
 * @property {(x:number,y:number)=>void} [onClick]    click on empty image, image-pixel coordinates
 * @property {(id:string)=>void} [onMarkerClick]
 * @property {(x:number|null,y:number|null)=>void} [onHover]   null when the pointer leaves
 * @property {(v:ViewState)=>void} [onView]           fired on every user-driven zoom/pan (for linked viewers)
 * @property {boolean} [controls]                  show the control bar (default true)
 */

/** @param {HTMLElement} root @param {ViewerOptions} [opts] */
export function createViewer(root, opts = {}) {
  const showControls = opts.controls !== false;
  root.classList.add("viewer");
  root.innerHTML = `
    <div class="viewer-stage" tabindex="0" role="group"
         aria-label="${esc(opts.label ?? "Sky image viewer")}. Drag to pan, scroll or plus and minus keys to zoom, zero to reset, F for fullscreen.">
      <div class="viewer-layer"><img alt="" draggable="false"><svg class="viewer-markers" aria-hidden="true"></svg></div>
    </div>
    <div class="viewer-state" role="status" hidden></div>
    <div class="viewer-hud"></div>
    <div class="viewer-readout mono" aria-live="off"></div>
    ${showControls ? `
    <div class="viewer-controls" role="toolbar" aria-label="Viewer controls">
      <button class="btn btn-icon" data-act="in" aria-label="Zoom in" title="Zoom in (+)">+</button>
      <button class="btn btn-icon" data-act="out" aria-label="Zoom out" title="Zoom out (−)">−</button>
      <button class="btn btn-icon" data-act="reset" aria-label="Reset view" title="Reset view (0)">⌂</button>
      <button class="btn btn-icon" data-act="markers" aria-label="Candidate markers" aria-pressed="true" title="Toggle candidate markers">◎</button>
      <details class="viewer-adjust"><summary class="btn btn-icon" aria-label="Field of view, brightness and contrast" title="Field of view, brightness and contrast">☼</summary>
        <div class="viewer-adjust-body">
          <span class="label">Field of view</span>
          <div class="fov" role="group" aria-label="Field of view">
            <button type="button" class="chip" data-act="fit" title="Whole image in view">Fit</button>
            <button type="button" class="chip" data-act="px1" title="One image pixel per screen pixel">1:1</button>
            <button type="button" class="chip" data-act="px2" title="Two screen pixels per image pixel">2:1</button>
          </div>
          <label class="label" for="${root.id || "v"}-bri">Brightness</label>
          <input class="range" id="${root.id || "v"}-bri" data-act="bri" type="range" min="0.5" max="2.5" step="0.05" value="1">
          <label class="label" for="${root.id || "v"}-con">Contrast</label>
          <input class="range" id="${root.id || "v"}-con" data-act="con" type="range" min="0.5" max="2.5" step="0.05" value="1">
          <p class="muted" style="font-size:var(--fs-xs);margin:0">Display only. Not a measurement.</p>
        </div></details>
      <button class="btn btn-icon" data-act="full" aria-label="Fullscreen" title="Fullscreen (F)">⛶</button>
    </div>` : ""}`;

  const stage = /** @type {HTMLElement} */ (root.querySelector(".viewer-stage"));
  const layer = /** @type {HTMLElement} */ (root.querySelector(".viewer-layer"));
  const img = /** @type {HTMLImageElement} */ (root.querySelector("img"));
  const svg = /** @type {SVGSVGElement} */ (root.querySelector(".viewer-markers"));
  const stateEl = /** @type {HTMLElement} */ (root.querySelector(".viewer-state"));
  const hud = /** @type {HTMLElement} */ (root.querySelector(".viewer-hud"));
  const readout = /** @type {HTMLElement} */ (root.querySelector(".viewer-readout"));

  let iw = 0, ih = 0;              // image size in pixels
  let scale = 1, tx = 0, ty = 0;   // current transform
  let userMoved = false;           // false => keep the image fitted when the container resizes
  /** @type {Marker[]} */ let markers = [];
  let markersVisible = true;
  let gen = 0;                     // bumps on every new image/state, so a slow decode can't overwrite newer state

  const fit = () => (iw && ih ? Math.min(stage.clientWidth / iw, stage.clientHeight / ih) : 1);

  function apply() {
    layer.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    layer.style.setProperty("--z", String(scale));
  }

  function clampPan() {
    const keep = 80, w = iw * scale, h = ih * scale;
    tx = Math.min(stage.clientWidth - keep, Math.max(keep - w, tx));
    ty = Math.min(stage.clientHeight - keep, Math.max(keep - h, ty));
  }

  function reset() {
    scale = fit(); tx = (stage.clientWidth - iw * scale) / 2; ty = (stage.clientHeight - ih * scale) / 2;
    userMoved = false; apply();
  }

  /** Zoom about a point given in stage pixels. */
  function zoomAt(/** @type {number} */ factor, /** @type {number} */ px, /** @type {number} */ py) {
    const f = fit(), next = Math.min(f * 24, Math.max(f * 0.5, scale * factor));
    tx = px - (px - tx) * (next / scale); ty = py - (py - ty) * (next / scale); scale = next;
    userMoved = true; clampPan(); apply(); emitView();
  }

  function emitView() { opts.onView?.(getView()); }

  /** @returns {ViewState} */
  function getView() {
    return { zoom: scale / fit(), cx: (stage.clientWidth / 2 - tx) / scale, cy: (stage.clientHeight / 2 - ty) / scale };
  }
  /** Apply a view from a linked viewer without re-emitting. */
  function setView(/** @type {ViewState} */ v) {
    if (!iw) return;
    scale = fit() * v.zoom; tx = stage.clientWidth / 2 - v.cx * scale; ty = stage.clientHeight / 2 - v.cy * scale;
    userMoved = true; clampPan(); apply();
  }

  /** Stage pixel -> image pixel. */
  function toImage(/** @type {number} */ px, /** @type {number} */ py) { return { x: (px - tx) / scale, y: (py - ty) / scale }; }
  function localPoint(/** @type {PointerEvent|WheelEvent} */ e) { const r = stage.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }

  // ---- pointer: drag to pan, click (no drag) to select ----------------------
  let drag = /** @type {null|{x:number,y:number,tx:number,ty:number,moved:boolean}} */ (null);
  stage.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    stage.setPointerCapture(e.pointerId);
    drag = { x: e.clientX, y: e.clientY, tx, ty, moved: false };
  });
  stage.addEventListener("pointermove", e => {
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.hypot(dx, dy) > 4) drag.moved = true;
      if (drag.moved) { tx = drag.tx + dx; ty = drag.ty + dy; userMoved = true; clampPan(); apply(); stage.classList.add("panning"); emitView(); }
    } else if (iw) {
      const p = localPoint(e), i = toImage(p.x, p.y);
      opts.onHover?.(i.x >= 0 && i.y >= 0 && i.x < iw && i.y < ih ? i.x : null, i.y);
    }
  });
  stage.addEventListener("pointerup", e => {
    const wasClick = drag && !drag.moved; drag = null; stage.classList.remove("panning");
    if (!wasClick || !iw) return;
    const p = localPoint(e), i = toImage(p.x, p.y);
    const hit = markersVisible && markers.find(m => Math.hypot(m.x - i.x, m.y - i.y) <= (m.r ?? 18) + 8 / scale);
    if (hit) opts.onMarkerClick?.(hit.id); else opts.onClick?.(i.x, i.y);
  });
  stage.addEventListener("pointercancel", () => { drag = null; stage.classList.remove("panning"); });
  stage.addEventListener("pointerleave", () => opts.onHover?.(null, null));
  stage.addEventListener("wheel", e => {
    e.preventDefault();
    const p = localPoint(e); zoomAt(Math.exp(-e.deltaY * 0.0015), p.x, p.y);
  }, { passive: false });

  // ---- keyboard ------------------------------------------------------------
  stage.addEventListener("keydown", e => {
    const cx = stage.clientWidth / 2, cy = stage.clientHeight / 2, step = 40;
    const moves = /** @type {Record<string,[number,number]>} */ ({ ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] });
    if (moves[e.key]) { e.preventDefault(); tx += moves[e.key][0]; ty += moves[e.key][1]; userMoved = true; clampPan(); apply(); emitView(); }
    else if (e.key === "+" || e.key === "=") zoomAt(1.25, cx, cy);
    else if (e.key === "-" || e.key === "_") zoomAt(0.8, cx, cy);
    else if (e.key === "0") { reset(); emitView(); }
    else if (e.key.toLowerCase() === "f") toggleFullscreen();
  });

  // ---- control bar -----------------------------------------------------------
  function applyFilter() {
    const b = /** @type {HTMLInputElement|null} */ (root.querySelector('[data-act="bri"]'))?.value ?? "1";
    const c = /** @type {HTMLInputElement|null} */ (root.querySelector('[data-act="con"]'))?.value ?? "1";
    img.style.filter = `brightness(${b}) contrast(${c})`;
  }
  function toggleFullscreen() {
    if (document.fullscreenElement === root) { document.exitFullscreen?.(); return; }
    if (root.requestFullscreen) root.requestFullscreen().catch(() => root.classList.toggle("viewer-expanded"));
    else root.classList.toggle("viewer-expanded");
  }
  root.addEventListener("click", e => {
    const t = /** @type {HTMLElement} */ (e.target).closest("[data-act]");
    if (!t) return;
    const cx = stage.clientWidth / 2, cy = stage.clientHeight / 2;
    switch (/** @type {HTMLElement} */ (t).dataset.act) {
      case "in": zoomAt(1.4, cx, cy); break;
      case "out": zoomAt(1 / 1.4, cx, cy); break;
      case "reset": case "fit": reset(); emitView(); break;
      case "px1": zoomAt(1 / scale, cx, cy); break;          // absolute scale 1 (zoomAt clamps to the allowed range)
      case "px2": zoomAt(2 / scale, cx, cy); break;
      case "full": toggleFullscreen(); break;
      case "markers": setMarkersVisible(!markersVisible); break;
    }
  });
  root.addEventListener("input", e => { const a = /** @type {HTMLElement} */ (e.target).dataset.act; if (a === "bri" || a === "con") applyFilter(); });

  const ro = new ResizeObserver(() => { if (iw && !userMoved) reset(); else if (iw) { clampPan(); apply(); } });
  ro.observe(stage);
  document.addEventListener("fullscreenchange", () => { if (iw && !userMoved) reset(); });

  // ---- public API ---------------------------------------------------------
  function drawMarkers() {
    svg.setAttribute("viewBox", `0 0 ${iw || 1} ${ih || 1}`);
    svg.style.display = markersVisible ? "" : "none";
    svg.innerHTML = markers.map(m => `
      <g class="${m.selected ? "mk mk-sel" : "mk"}">
        <circle cx="${m.x}" cy="${m.y}" r="${m.r ?? 18}"></circle>
        ${m.label ? `<text x="${m.x + (m.r ?? 18) + 6}" y="${m.y + 4}">${esc(m.label)}</text>` : ""}
      </g>`).join("");
  }
  function setMarkersVisible(/** @type {boolean} */ v) {
    markersVisible = v; drawMarkers();
    root.querySelector('[data-act="markers"]')?.setAttribute("aria-pressed", String(v));
  }

  return {
    /** Show an image. Keeps the user's zoom/pan when only the picture changes (same size), so blinking stays steady. */
    async setImage(/** @type {{url: string, width: number, height: number}} */ im) {
      const g = ++gen;
      const sameSize = im.width === iw && im.height === ih;
      iw = im.width; ih = im.height;
      img.width = iw; img.height = ih; layer.style.width = `${iw}px`; layer.style.height = `${ih}px`;
      img.src = im.url;
      layer.style.visibility = "";
      // Wait for decoding so blinking never flashes an empty frame, but never stall on it.
      try { await Promise.race([img.decode(), new Promise(r => setTimeout(r, 800))]); } catch { /* shown anyway */ }
      if (g !== gen) return;               // a newer image or state arrived while decoding; don't overwrite it
      img.alt = "Sky image";
      this.setState({ kind: "ready" });
      if (!sameSize || !userMoved) reset(); else apply();
      drawMarkers();
    },
    /** Remove the picture entirely (e.g. a "not comparable" pair must not be shown, not even dimmed). Zoom/pan state is kept. */
    clear() { gen++; img.removeAttribute("src"); img.alt = ""; markers = []; drawMarkers(); stage.classList.remove("dim"); layer.style.visibility = "hidden"; },
    /** @param {Marker[]} m */ setMarkers(m) { markers = m; drawMarkers(); },
    setMarkersVisible,
    /** @param {{ kind: "ready"|"loading"|"processing"|"unavailable"|"failed"|"empty", message?: string, onRetry?: () => void }} s */
    setState(s) {
      if (s.kind !== "ready") gen++;       // supersedes any image still decoding
      stateEl.hidden = s.kind === "ready";
      stage.classList.toggle("dim", s.kind !== "ready" && !!iw);
      if (s.kind === "ready") { stateEl.innerHTML = ""; return; }
      const spin = s.kind === "loading" || s.kind === "processing" ? `<div class="spinner" aria-hidden="true"></div>` : "";
      const retry = s.onRetry ? `<button class="btn" data-retry>Try again</button>` : "";
      stateEl.innerHTML = `${spin}<p>${esc(s.message ?? "")}</p>${retry}`;
      stateEl.querySelector("[data-retry]")?.addEventListener("click", () => s.onRetry?.());
    },
    setHud(/** @type {string} */ html) { hud.innerHTML = html; },
    setReadout(/** @type {string} */ text) { readout.textContent = text; },
    setFilterOpacity(/** @type {number} */ o) { img.style.opacity = String(o); },
    getView, setView, reset, zoomAt, toggleFullscreen,
    get imageSize() { return { width: iw, height: ih }; },
    destroy() { ro.disconnect(); root.innerHTML = ""; },
  };
}
