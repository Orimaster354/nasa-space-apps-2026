"""Automated source differencing: find compact bright or dark residuals in a difference picture (P2-02).

Pure functions on arrays already on a common sky grid, no I/O. This produces LEADS, never findings, and it is meant to be asked for
explicitly (it is not part of the automatic candidate search): a difference of two SPHEREx pictures contains noise, star residuals from
pointing and from the different wavelength at each pixel, cosmic-ray hits and edge effects, and a detector that finds "something" in all of those.

What it does:
  1. Match the two sky backgrounds, subtract, and remove slow structure (a running median) so only compact features remain.
  2. Measure the pixel-to-pixel noise and keep connected groups of pixels beyond NSIGMA times that noise, at least MIN_PIXELS across.
  3. Throw away groups touching the picture edge or a missing-data pixel, and groups that are just the residual of a bright star
     (the residual is small compared with the star's own brightness; a real mover or flare is not).
  4. Pair a bright group with a dark group within PAIR_PX pixels: a moved source leaves exactly that pair. Unpaired groups are reported as
     brighter or fainter in the later picture.
Measured behaviour (tests/test_differencing.py and docs): on noise-only pairs the expected false-detection rate at NSIGMA is about
zero groups; stars that are the same in both pictures leave residuals from pointing and calibration, which is what the star-residual gate removes. Every result is flagged as a lead.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .coadd import SMOOTH_PX, robust_sigma

NSIGMA = 5.0
MIN_PIXELS = 3
PAIR_PX = 10.0                 # a bright and a dark group closer than this are one moved source (10 pixels = 20 arcsec on the 2 arcsec grid)
STAR_NEIGHBOURHOOD_PX = 4      # the residual is compared with the brightest part of the picture within this many pixels of it (the star's core)
EDGE_PX = 4                    # groups this close to the picture edge or to missing data are dropped
STAR_RESIDUAL_FRACTION = 0.30  # a residual smaller than this share of the local star brightness is a star residual, not a source
MAX_DETECTIONS = 12


@dataclass(frozen=True)
class Detection:
    x: float                   # grid pixel (FITS orientation: x right, y up)
    y: float
    polarity: int              # +1 brighter in the target, -1 fainter
    peak_sigma: float          # strongest pixel in noise units
    n_pixels: int
    kind: str                  # "moved" (paired bright and dark), "brighter", "fainter"
    pair_x: float | None = None
    pair_y: float | None = None


@dataclass(frozen=True)
class Result:
    detections: list[Detection]
    noise: float               # pixel-to-pixel noise of the difference (image units)
    groups_found: int          # before the gates
    dropped_edge: int
    dropped_star_residual: int


def _highpass(a: np.ndarray) -> np.ndarray:
    finite = np.isfinite(a)
    filled = np.where(finite, a, np.median(a[finite]))
    return np.where(finite, a - ndimage.median_filter(filled, size=SMOOTH_PX, mode="nearest"), np.nan)


def detect(base: np.ndarray, target: np.ndarray, nsigma: float = NSIGMA) -> Result:
    """Detections in (target - base) on one grid. Both inputs are in the same units."""
    b, t = np.asarray(base, float), np.asarray(target, float)
    fb, ft = np.isfinite(b), np.isfinite(t)
    if fb.sum() < 400 or ft.sum() < 400:
        return Result([], float("nan"), 0, 0, 0)
    diff = (t - np.nanmedian(t)) - (b - np.nanmedian(b))
    hp = _highpass(diff)
    noise = robust_sigma(hp)
    if not np.isfinite(noise) or noise <= 0:
        return Result([], float("nan"), 0, 0, 0)
    sig = hp / noise
    bad = ~np.isfinite(hp)
    near_bad = ndimage.binary_dilation(bad, iterations=EDGE_PX)
    edge = np.zeros_like(bad)
    edge[:EDGE_PX, :] = edge[-EDGE_PX:, :] = edge[:, :EDGE_PX] = edge[:, -EDGE_PX:] = True
    keep_out = near_bad | edge
    groups: list[tuple[int, np.ndarray]] = []
    n_found = dropped_edge = dropped_star = 0
    for pol in (+1, -1):
        mask = (pol * np.nan_to_num(sig, nan=0.0)) > nsigma
        lab, n = ndimage.label(mask)
        for k in range(1, n + 1):
            cells = lab == k
            if cells.sum() < MIN_PIXELS:
                continue
            n_found += 1
            if (cells & keep_out).any():
                dropped_edge += 1
                continue
            groups.append((pol, cells))
    # star-residual gate: compare the residual with the brightness of the picture where it sits (smoothed so a single hot pixel does not count)
    sb, st = ndimage.gaussian_filter(np.nan_to_num(b - np.nanmedian(b)), 1.0), ndimage.gaussian_filter(np.nan_to_num(t - np.nanmedian(t)), 1.0)
    cands = []
    for pol, cells in groups:
        ys, xs = np.nonzero(cells)
        w = np.abs(hp[ys, xs])
        cy, cx = float(np.average(ys, weights=w)), float(np.average(xs, weights=w))
        peak_resid = float(np.max(np.abs(hp[cells])))
        near = ndimage.binary_dilation(cells, iterations=STAR_NEIGHBOURHOOD_PX)
        local_star = float(max(np.max(sb[near]), np.max(st[near]), 0.0))
        if local_star > 0 and peak_resid < STAR_RESIDUAL_FRACTION * local_star:
            dropped_star += 1
            continue
        cands.append(Detection(cx, cy, pol, float(np.max(np.abs(sig[cells]))), int(cells.sum()), "brighter" if pol > 0 else "fainter"))
    used, out = set(), []
    pos = [i for i, d in enumerate(cands) if d.polarity > 0]
    neg = [i for i, d in enumerate(cands) if d.polarity < 0]
    for i in pos:
        best, bd = None, PAIR_PX
        for j in neg:
            if j in used:
                continue
            dd = float(np.hypot(cands[i].x - cands[j].x, cands[i].y - cands[j].y))
            if dd <= bd:
                best, bd = j, dd
        if best is not None:
            used.update({i, best})
            p, n = cands[i], cands[best]
            out.append(Detection((p.x + n.x) / 2, (p.y + n.y) / 2, 0, max(p.peak_sigma, n.peak_sigma), p.n_pixels + n.n_pixels, "moved", p.x, p.y))
    out += [d for k, d in enumerate(cands) if k not in used]
    out.sort(key=lambda d: -d.peak_sigma)
    return Result(out[:MAX_DETECTIONS], noise, n_found, dropped_edge, dropped_star)
