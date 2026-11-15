// @ts-check
/**
 * MOCK imagery. Procedurally generates a synthetic star field per observation so the
 * viewer, timeline and blinking have something to show. NOT NASA data.
 *
 * This file stands in for the backend renderer. In real mode, images come from the
 * contract's `imageUrl`, and nothing here is used. Sources that "move" are drawn from the
 * same candidate definitions the candidates panel uses (proposed.json), so overlays line up.
 */

import { loadFixture, hash, rng } from "./fixtures.js";

const urlCache = new Map();

/** Starfield for an object: identical in every frame (stars do not move). */
function drawStars(/** @type {CanvasRenderingContext2D} */ ctx, /** @type {string} */ objectId, w, h) {
  const r = rng(hash(objectId));
  for (let i = 0; i < 420; i++) {
    const x = r() * w, y = r() * h, m = Math.pow(r(), 3.2);
    star(ctx, x, y, 0.8 + m * 3.2, 0.25 + m * 0.75);
  }
}

function star(/** @type {CanvasRenderingContext2D} */ ctx, x, y, radius, alpha) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, radius * 2.2);
  g.addColorStop(0, `rgba(255,252,240,${Math.min(1, alpha)})`);
  g.addColorStop(0.4, `rgba(220,235,255,${alpha * 0.45})`);
  g.addColorStop(1, "rgba(220,235,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(x - radius * 2.2, y - radius * 2.2, radius * 4.4, radius * 4.4);
}

/**
 * Candidate pixel position in frame `index` (shared with the Candidate `track`).
 * @param {{startPx:{x:number,y:number}, velPx:{x:number,y:number}}} c
 */
export function candidatePosition(c, index) {
  return { x: c.startPx.x + c.velPx.x * index, y: c.startPx.y + c.velPx.y * index };
}

/**
 * Render one observation to an image URL (blob).
 * @param {{ observationId: string, qualitySummary: string }} obs
 * @param {string} objectId
 * @param {number} index  position of this observation in the object's time order
 */
export async function renderObservation(obs, objectId, index) {
  const key = obs.observationId;
  if (urlCache.has(key)) return urlCache.get(key);

  const proposed = await loadFixture("proposed.json");
  const { width: w, height: h } = proposed.imageSize;
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const ctx = /** @type {CanvasRenderingContext2D} */ (canvas.getContext("2d"));

  ctx.fillStyle = "#05080c"; ctx.fillRect(0, 0, w, h);
  drawStars(ctx, objectId, w, h);
  star(ctx, w / 2, h / 2, 3.4, 1);                 // the target itself

  for (const c of proposed.candidates[objectId] ?? []) {
    const p = candidatePosition(c, index);
    const bright = 1 + c.brightnessAmp * Math.sin(index * 1.3 + hash(c.candidateId) % 7);
    star(ctx, p.x, p.y, 2.6 * Math.max(0.5, bright), Math.min(1, 0.65 * bright + 0.25));
  }

  // Per-observation noise speckles; degraded/poor frames get visible artefacts.
  const nr = rng(hash(obs.observationId));
  for (let i = 0; i < 160; i++) { ctx.fillStyle = `rgba(200,220,255,${0.05 + nr() * 0.12})`; ctx.fillRect(nr() * w, nr() * h, 1, 1); }
  if (obs.qualitySummary === "degraded") { ctx.fillStyle = "rgba(120,150,180,0.10)"; ctx.fillRect(0, h * 0.82, w, 22); }
  if (obs.qualitySummary === "poor") { ctx.fillStyle = "rgba(0,0,0,0.85)"; ctx.fillRect(w * 0.55, 0, 80, h); }

  const blob = await new Promise(res => canvas.toBlob(res, "image/png"));
  const result = { url: URL.createObjectURL(/** @type {Blob} */ (blob)), width: w, height: h, canvas };
  urlCache.set(key, result);
  return result;
}

/**
 * MOCK difference image |target - base|. In real mode the backend computes this.
 * @param {{canvas: HTMLCanvasElement, width: number, height: number}} a
 * @param {{canvas: HTMLCanvasElement}} b
 */
export async function renderDifference(a, b, key) {
  if (urlCache.has(key)) return urlCache.get(key);
  const { width: w, height: h } = a;
  const out = document.createElement("canvas"); out.width = w; out.height = h;
  const ctx = /** @type {CanvasRenderingContext2D} */ (out.getContext("2d"));
  const da = /** @type {CanvasRenderingContext2D} */ (a.canvas.getContext("2d")).getImageData(0, 0, w, h);
  const db = /** @type {CanvasRenderingContext2D} */ (b.canvas.getContext("2d")).getImageData(0, 0, w, h);
  const o = ctx.createImageData(w, h);
  for (let i = 0; i < o.data.length; i += 4) {
    const d = Math.min(255, Math.abs(da.data[i] - db.data[i]) * 3);
    o.data[i] = d * 0.5; o.data[i + 1] = d; o.data[i + 2] = d * 0.9; o.data[i + 3] = 255;
  }
  ctx.putImageData(o, 0, 0);
  const blob = await new Promise(res => out.toBlob(res, "image/png"));
  const result = { url: URL.createObjectURL(/** @type {Blob} */ (blob)), width: w, height: h, canvas: out };
  urlCache.set(key, result);
  return result;
}
