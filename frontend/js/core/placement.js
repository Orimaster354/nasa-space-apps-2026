// @ts-check
/**
 * Collision-aware placement for small floating UI (Navi's whisper bubble and assistant panel).
 * Tries each side of an anchor in order, keeps the box inside the viewport, and picks the first
 * position that overlaps none of the "avoid" rectangles (illustrations, imagery, controls).
 * If every side collides it returns the position with the least overlap.
 */

/** @typedef {{ left: number, top: number, right: number, bottom: number }} Box */
/** @typedef {"left"|"right"|"top"|"bottom"} Side */

/** Overlap area of two boxes. */
export function overlap(/** @type {Box} */ a, /** @type {Box} */ b) {
  const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return w > 0 && h > 0 ? w * h : 0;
}

/** Viewport rectangles of every visible element matching `selector`. */
export function rectsOf(/** @type {string} */ selector, /** @type {Element[]} */ except = []) {
  if (!selector) return [];
  return [...document.querySelectorAll(selector)]
    .filter(el => !except.some(x => x === el || x.contains(el) || el.contains(x)))
    .map(el => el.getBoundingClientRect())
    .filter(r => r.width > 0 && r.height > 0);
}

/**
 * @param {Box} anchor  viewport rect to sit beside
 * @param {number} w    box width
 * @param {number} h    box height
 * @param {Box[]} avoid viewport rects the box must not cover
 * @param {{ sides?: Side[], gap?: number, margin?: number, align?: "center"|"start"|"end" }} [o]
 * @returns {{ x: number, y: number, side: Side, clean: boolean }} viewport coordinates of the box's top-left
 */
export function placeBeside(anchor, w, h, avoid, o = {}) {
  const sides = o.sides ?? ["left", "right", "top", "bottom"], gap = o.gap ?? 10, m = o.margin ?? 8;
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  const cx = (anchor.left + anchor.right) / 2, cy = (anchor.top + anchor.bottom) / 2;
  const clampX = (/** @type {number} */ x) => Math.min(vw - m - w, Math.max(m, x));
  const clampY = (/** @type {number} */ y) => Math.min(vh - m - h, Math.max(m, y));

  /** @type {{ x: number, y: number, side: Side, cost: number }|null} */ let best = null;
  // For each side, try the preferred alignment first, then slide the box to either end of that side.
  const aligns = [o.align ?? "center", "start", "end"].filter((a, i, all) => all.indexOf(a) === i);
  for (const side of sides) for (const al of aligns) {
    const pos = (/** @type {number} */ c, /** @type {number} */ size, /** @type {number} */ lo, /** @type {number} */ hi) => al === "start" ? lo : al === "end" ? hi - size : c - size / 2;
    let x, y;
    if (side === "left") { x = anchor.left - gap - w; y = clampY(pos(cy, h, anchor.top, anchor.bottom)); }
    else if (side === "right") { x = anchor.right + gap; y = clampY(pos(cy, h, anchor.top, anchor.bottom)); }
    else if (side === "top") { y = anchor.top - gap - h; x = clampX(pos(cx, w, anchor.left, anchor.right)); }
    else { y = anchor.bottom + gap; x = clampX(pos(cx, w, anchor.left, anchor.right)); }
    const box = { left: x, top: y, right: x + w, bottom: y + h };
    // Leaving the viewport on the placement axis is the worst kind of collision.
    const outside = (x < m || x + w > vw - m || y < m || y + h > vh - m) ? w * h * 4 : 0;
    const cost = outside + avoid.reduce((sum, r) => sum + overlap(box, r), 0);
    if (cost === 0) return { x, y, side, clean: true };
    if (!best || cost < best.cost) best = { x, y, side, cost };
  }
  const b = /** @type {{ x: number, y: number, side: Side, cost: number }} */ (best);
  return { x: clampX(b.x), y: clampY(b.y), side: b.side, clean: false };
}
