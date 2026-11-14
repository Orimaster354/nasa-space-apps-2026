// @ts-check
/**
 * Pixel-art landscape along the bottom of every page (one shared illustration).
 *
 * Drawn on a 4 px grid at 1:1 CSS pixels (no viewBox scaling), so pixels stay square and sharp
 * at every screen width. Ridges are seamless <pattern>s that repeat across any width; landmarks
 * sit at fixed pixel offsets on a plateau of the middle ridge. Colours are the approved palette
 * only, separated by opacity:
 *   far ridge  Slate Blue  (lighter than the sky, with Warm Ivory snow)
 *   mid ridge  Midnight Charcoal at 60%   near ground  Midnight Charcoal
 *   observatory Warm Ivory dome, Slate Blue building, Solar Orange light.
 * Decorative only: aria-hidden, never over data (it sits below the content).
 */

const U = 4;          // one art pixel = 4 CSS pixels
const H = 128;        // scene height in CSS pixels (32 art rows)

/** Height (in art pixels) of a seamless ridge at column c, from triangular peaks [centre, height, halfWidth]. */
function ridge(/** @type {number} */ cols, /** @type {number[][]} */ peaks, /** @type {number} */ base) {
  const h = [];
  for (let c = 0; c < cols; c++) {
    let v = base;
    for (const [pc, ph, hwl, hwr = hwl] of peaks) {
      // Signed wrap-around distance, so the tile repeats seamlessly; separate left/right slopes
      // give asymmetric, more mountain-like silhouettes than plain triangles.
      let d = c - pc; if (d > cols / 2) d -= cols; if (d < -cols / 2) d += cols;
      const hw = d < 0 ? hwl : hwr;
      v = Math.max(v, Math.round(ph - (Math.abs(d) * ph) / hw));
    }
    // Shoulders: a one-pixel ledge on long slopes breaks up the straight diagonal (never on the base).
    if (v > base + 4 && c % 9 === 4) v -= 1;
    h.push(v);
  }
  return h;
}

/** Stepped outline path for column heights (art pixels) inside a pattern of `cols` columns. */
function stepPath(/** @type {number[]} */ h) {
  let d = `M0 ${H}`;
  h.forEach((v, c) => { d += ` V${H - v * U} H${(c + 1) * U}`; });
  return `${d} V${H} Z`;
}

/** Snow caps: the top `depth` art pixels of every column taller than `line`. */
function capsPath(/** @type {number[]} */ h, /** @type {number} */ line, /** @type {number} */ depth) {
  return h.map((v, c) => v > line ? `M${c * U} ${H - v * U}h${U}v${Math.min(depth, v - line) * U}h-${U}z` : "").join("");
}

/** A rectangle in art pixels, relative to an origin in CSS pixels. */
const px = (/** @type {number} */ x, /** @type {number} */ y, /** @type {number} */ w, /** @type {number} */ h, /** @type {string} */ fill, op = 1) =>
  `<rect x="${x * U}" y="${y * U}" width="${w * U}" height="${h * U}" fill="${fill}"${op < 1 ? ` fill-opacity="${op}"` : ""}/>`;

/** Observatory: dome with an open lit slit and a telescope, on a small building. Origin = bottom-left of the building. */
function observatory() {
  const IV = "#f2f0e9", SB = "#405a6b", CH = "#101820", OR = "#f28c58";
  let s = "";
  const r = 9, bw = 2 * r + 2, by = -7;                       // building width, building top (art px, y up = negative)
  // building
  s += px(0, by, bw, 7, SB) + px(0, by, bw, 1, IV, 0.35) + px(-1, -1, bw + 2, 1, SB);
  s += px(3, by + 2, 2, 2, OR, 0.9) + px(bw - 5, by + 2, 2, 2, OR, 0.9) + px(r, by + 3, 2, 3, CH, 0.85);  // windows, door
  // dome: one row per art pixel, half-width from the circle equation (no anti-aliasing)
  for (let y = 0; y < r; y++) {
    const half = Math.round(Math.sqrt(r * r - (r - y - 0.5) ** 2));
    s += px(1 + r - half, by - r + y, half * 2, 1, IV, 0.92);
    s += px(1 + r - half, by - r + y, Math.max(1, Math.round(half * 0.45)), 1, SB, 0.55);   // shaded left side
  }
  // slit, warm light and telescope tube leaning out of it
  s += px(r + 1, by - r, 2, r, CH) + px(r + 1, by - 3, 2, 3, OR, 0.7);
  s += px(r + 3, by - r - 1, 1, 1, SB) + px(r + 4, by - r - 2, 1, 1, SB) + px(r + 5, by - r - 3, 2, 1, SB) + px(r + 5, by - r - 4, 1, 1, IV, 0.6);
  return s;
}

/** Tripod telescope silhouette (Slate Blue, with a faint ivory rim on the lens). Origin = ground point under the mount. */
function telescope(/** @type {number} */ flip = 1) {
  const CH = "#405a6b", IV = "#f2f0e9";                 // Slate Blue silhouette reads against the dark ground and mid ridge
  let s = "";
  s += px(0, -5, 1, 5, CH) + px(-2 , -3, 1, 3, CH) + px(-3, -1, 1, 1, CH) + px(2, -3, 1, 3, CH) + px(3, -1, 1, 1, CH);   // legs
  s += px(-1, -6, 3, 1, CH);                                                                       // mount
  const tube = flip > 0 ? [[-2, -7], [0, -8], [2, -9], [4, -10]] : [[2, -7], [0, -8], [-2, -9], [-4, -10]];
  for (const [x, y] of tube) s += px(x, y, 2, 1, CH);
  const [hx, hy] = tube[3]; s += px(hx, hy - 1, 2, 1, IV, 0.35);                                 // faint rim light on the lens end
  return s;
}

/** A few pixel stars in the strip above the ridges (deterministic). */
function stars(/** @type {number} */ cols) {
  let s = "", seed = 7;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < 9; i++) s += px(Math.floor(rnd() * cols), Math.floor(rnd() * 8), 1, 1, "#f2f0e9", 0.25 + rnd() * 0.35);
  return s;
}

export function sceneSVG() {
  // Pattern widths are multiples of the 4 px grid so tiles meet exactly.
  const FAR = 96, MID = 128, NEAR = 64;                                 // columns per tile
  const far = ridge(FAR, [[8, 16, 14, 11], [26, 24, 19, 13], [47, 27, 16, 21], [66, 18, 10, 14], [84, 23, 15, 12]], 9);
  const mid = ridge(MID, [[46, 15, 10, 18], [78, 11, 8, 10], [106, 16, 16, 9]], 6).map((v, c) => (c >= 12 && c <= 38 ? 9 : v));  // plateau for the observatory
  const near = ridge(NEAR, [[12, 4, 8], [40, 3, 9]], 2);
  return `<svg class="scene-svg" width="100%" height="${H}" shape-rendering="crispEdges" aria-hidden="true" focusable="false">
    <defs>
      <pattern id="sc-stars" width="${FAR * U}" height="${H}" patternUnits="userSpaceOnUse">${stars(FAR)}</pattern>
      <pattern id="sc-far" width="${FAR * U}" height="${H}" patternUnits="userSpaceOnUse">
        <path d="${stepPath(far)}" fill="#405a6b" fill-opacity="0.5"/><path d="${capsPath(far, 18, 2)}" fill="#f2f0e9" fill-opacity="0.3"/></pattern>
      <pattern id="sc-mid" width="${MID * U}" height="${H}" patternUnits="userSpaceOnUse">
        <path d="${stepPath(mid)}" fill="#101820" fill-opacity="0.6"/></pattern>
      <pattern id="sc-near" width="${NEAR * U}" height="${H}" patternUnits="userSpaceOnUse">
        <path d="${stepPath(near)}" fill="#101820"/></pattern>
    </defs>
    <rect width="100%" height="${H}" fill="url(#sc-stars)"/>
    <rect width="100%" height="${H}" fill="url(#sc-far)"/>
    <rect width="100%" height="${H}" fill="url(#sc-mid)"/>
    <g transform="translate(${15 * U} ${H - 9 * U})">${observatory()}</g>
    <svg x="100%" y="0" width="1" height="${H}" overflow="visible"><g transform="translate(${-30 * U} ${H - 2 * U})">${telescope(1)}</g>
      <g transform="translate(${-62 * U} ${H - 2 * U})">${telescope(-1)}</g></svg>
    <rect width="100%" height="${H}" fill="url(#sc-near)"/>
  </svg>`;
}

/** @param {HTMLElement|null} el */
export function mountScene(el) { if (el) el.innerHTML = sceneSVG(); }
