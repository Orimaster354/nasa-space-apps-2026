"""Measure the residual offset between two images already resampled onto the same sky grid.

Method: detect bright point-like peaks in each image, centroid them to sub-pixel precision, match peaks between the two images
within a small radius, and take the MEDIAN offset. A median is used so that a genuinely moving object (the thing we are looking
for) and mismatched sources do not drag the estimate. Only stationary stars should agree; the median reports how far the frames
disagree about where those stars are.

Limits (stated, not hidden): needs at least MIN_MATCHES matched sources, otherwise the result is "not measured". The grid images
come from bilinear resampling of roughly 6 arcsec pixels, so centroids carry that resampling's limits. Sources with very
different brightness at the two wavelengths still match by position only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

MIN_MATCHES = 3
MAX_SOURCES = 40
DETECT_SIGMA = 5.0
MATCH_RADIUS_ARCSEC = 10.0
WINDOW_PX = 3                 # half-width of the centroid window on the grid (7 x 7 grid pixels)


@dataclass
class AlignmentEstimate:
    measured: bool
    n_matched: int
    dx_arcsec: float | None = None        # target minus base, grid +x (west)
    dy_arcsec: float | None = None        # target minus base, grid +y in FITS orientation (north)
    residual_arcsec: float | None = None  # length of the median offset vector
    scatter_arcsec: float | None = None   # robust scatter of the per-source offsets
    uncertainty_arcsec: float | None = None
    reason: str | None = None


def _robust_sigma(a: np.ndarray) -> float:
    return float(1.4826 * np.nanmedian(np.abs(a - np.nanmedian(a))))


def find_sources(img: np.ndarray, scale_arcsec: float) -> np.ndarray:
    """Sub-pixel centroids (x, y, peak_height) of bright isolated peaks, brightest first. Coordinates are 0-based grid pixels."""
    finite = np.isfinite(img)
    if finite.sum() < 100:
        return np.empty((0, 3))
    filled = np.where(finite, img, np.nanmedian(img))
    bkg = float(np.median(filled[finite]))
    sigma = _robust_sigma(img[finite])
    if not np.isfinite(sigma) or sigma <= 0:
        return np.empty((0, 3))
    smooth = ndimage.gaussian_filter(filled - bkg, sigma=1.5 / (scale_arcsec / 2.0))
    ssig = _robust_sigma(smooth[finite])
    sep = max(3, int(round(14.0 / scale_arcsec)))
    peaks = (smooth == ndimage.maximum_filter(smooth, size=2 * sep + 1)) & (smooth > DETECT_SIGMA * max(ssig, 1e-30)) & finite
    ys, xs = np.nonzero(peaks)
    out = []
    ny, nx = img.shape
    for y, x in zip(ys, xs):
        if y < WINDOW_PX or x < WINDOW_PX or y >= ny - WINDOW_PX or x >= nx - WINDOW_PX:
            continue
        win = filled[y - WINDOW_PX:y + WINDOW_PX + 1, x - WINDOW_PX:x + WINDOW_PX + 1] - bkg
        if not finite[y - WINDOW_PX:y + WINDOW_PX + 1, x - WINDOW_PX:x + WINDOW_PX + 1].all():
            continue
        w = np.clip(win, 0, None)
        if w.sum() <= 0:
            continue
        gy, gx = np.mgrid[-WINDOW_PX:WINDOW_PX + 1, -WINDOW_PX:WINDOW_PX + 1]
        out.append((x + float((w * gx).sum() / w.sum()), y + float((w * gy).sum() / w.sum()), float(smooth[y, x])))
    arr = np.array(out) if out else np.empty((0, 3))
    return arr[np.argsort(-arr[:, 2])][:MAX_SOURCES] if len(arr) else arr


def estimate_offset(base: np.ndarray, target: np.ndarray, scale_arcsec: float = 2.0) -> AlignmentEstimate:
    sb, st = find_sources(base, scale_arcsec), find_sources(target, scale_arcsec)
    if len(sb) < MIN_MATCHES or len(st) < MIN_MATCHES:
        return AlignmentEstimate(False, 0, reason=f"Too few bright point sources to measure alignment ({len(sb)} in the base frame, {len(st)} in the target).")
    radius = MATCH_RADIUS_ARCSEC / scale_arcsec
    d = np.hypot(sb[:, None, 0] - st[None, :, 0], sb[:, None, 1] - st[None, :, 1])
    pairs, used = [], set()
    for i in np.argsort(-sb[:, 2]):
        j = int(np.argmin(d[i]))
        if d[i, j] <= radius and j not in used:
            used.add(j)
            pairs.append((st[j, 0] - sb[i, 0], st[j, 1] - sb[i, 1]))
    if len(pairs) < MIN_MATCHES:
        return AlignmentEstimate(False, len(pairs), reason=f"Only {len(pairs)} sources matched between the frames (need {MIN_MATCHES}).")
    p = np.array(pairs) * scale_arcsec
    dx, dy = float(np.median(p[:, 0])), float(np.median(p[:, 1]))
    scatter = float(np.hypot(_robust_sigma(p[:, 0]), _robust_sigma(p[:, 1])))
    return AlignmentEstimate(True, len(pairs), dx, dy, float(np.hypot(dx, dy)), scatter, 1.2533 * scatter / np.sqrt(len(pairs)))
