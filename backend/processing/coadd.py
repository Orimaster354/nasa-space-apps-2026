"""Co-adding (stacking) aligned frames of one object into a deeper picture (challenge "extra": stack images per detector/bandpass).

Pure functions, no I/O. The inputs are frames that have ALREADY been resampled onto one common sky grid (imaging.to_grid), so a given
pixel is the same piece of sky in every frame.

What it does, and why:
  1. Each frame's own sky level is estimated (median) and removed. The zodiacal-light background differs from date to date, and
     averaging frames with different backgrounds would only add a pedestal; the weighted mean background is added back at the end.
  2. Each frame's noise is estimated from the frame itself (robust scatter, 1.4826 x MAD) and frames are weighted by 1/noise^2, so a
     noisy frame counts for less.
  3. At every pixel, values more than SIGMA_CLIP noise units from the median of the frames are dropped before averaging. This removes
     cosmic-ray hits and a transient present in one frame only (with 3 frames or more; with two there is nothing to compare against).
  4. The noise of the result is measured the same way as the inputs, and compared with what pure averaging of independent noise would
     give. "Noise" here is the pixel-to-pixel scatter: a smooth running median (15 pixels) is subtracted first, so slowly varying sky
     structure (which differs from date to date and does not average like noise) does not count. The scatter of the whole picture,
     smooth structure included, is reported too (`large_scale_gain`); on real fields it improves less. Both are measured, not assumed.

What it does NOT do: it does not move frames by sub-pixel amounts (the WCS alignment of the archive is used as is), and it does not
make anything moving look sharper; a moving object is smeared across the stack. The caller states both.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

SIGMA_CLIP = 4.0
MIN_FRAMES = 2
METHOD_VERSION = 2                # part of a stack's ID: changing the method must never serve an older stored result
SMOOTH_PX = 15                    # size of the running median removed before measuring pixel-to-pixel noise


@dataclass(frozen=True)
class StackResult:
    image: np.ndarray                 # the stacked picture, same grid as the inputs
    frames_used: int
    single_frame_sigma: float         # median of the frames' own pixel-to-pixel noise (image units)
    stack_sigma: float                # pixel-to-pixel noise of the stack, measured the same way
    measured_gain: float              # single_frame_sigma / stack_sigma
    ideal_gain: float                 # what averaging independent noise of these frames would give
    large_scale_gain: float           # the same ratio for the whole picture, smooth structure included
    clipped_fraction: float           # share of pixel values removed by the clip


def robust_sigma(a: np.ndarray) -> float:
    """Scatter of the typical pixels (1.4826 x median absolute deviation); stars and hot pixels hardly move it."""
    v = np.asarray(a, dtype=np.float64)
    v = v[np.isfinite(v)]
    if v.size < 10:
        return float("nan")
    return float(1.4826 * np.median(np.abs(v - np.median(v))))


def pixel_noise(a: np.ndarray) -> float:
    """Pixel-to-pixel scatter: robust sigma of the picture minus its own running median (slow structure removed)."""
    v = np.asarray(a, dtype=np.float64)
    finite = np.isfinite(v)
    if finite.sum() < 100:
        return float("nan")
    filled = np.where(finite, v, np.median(v[finite]))
    resid = np.where(finite, v - ndimage.median_filter(filled, size=SMOOTH_PX, mode="nearest"), np.nan)
    return robust_sigma(resid)


def stack(images: list[np.ndarray]) -> StackResult:
    """Combine frames on one grid. Raises ValueError when fewer than MIN_FRAMES frames are usable."""
    prepared, sigmas, levels = [], [], []
    for img in images:
        a = np.asarray(img, dtype=np.float64)
        finite = a[np.isfinite(a)]
        if finite.size < 100:
            continue
        level = float(np.median(finite))
        flat = a - level
        s = pixel_noise(flat)
        if not np.isfinite(s) or s <= 0:
            continue
        prepared.append(flat), sigmas.append(s), levels.append(level)
    if len(prepared) < MIN_FRAMES:
        raise ValueError(f"need at least {MIN_FRAMES} usable frames, got {len(prepared)}")

    cube = np.stack(prepared)                                   # (N, ny, nx), background removed
    sig = np.asarray(sigmas)[:, None, None]
    w = 1.0 / sig ** 2
    keep = np.isfinite(cube)
    clipped = 0.0
    if len(prepared) >= 3:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(cube, axis=0)
        outlier = np.abs(cube - med) > SIGMA_CLIP * sig
        clipped = float((outlier & keep).sum() / max(1, keep.sum()))
        keep &= ~outlier
    wk = np.where(keep, w, 0.0)
    wsum = wk.sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(wsum > 0, (np.where(keep, cube, 0.0) * wk).sum(axis=0) / wsum, np.nan)
    wl = np.asarray(levels)
    out = out + float(np.average(wl, weights=1.0 / np.asarray(sigmas) ** 2))

    single = float(np.median(sigmas))
    stack_sigma = pixel_noise(out)
    ideal = single * float(np.sqrt(np.sum(1.0 / np.asarray(sigmas) ** 2)))        # single / (1/sqrt(sum w)), in noise units
    whole_single = float(np.median([robust_sigma(p) for p in prepared]))
    whole_stack = robust_sigma(out - np.nanmedian(out))
    gain = single / stack_sigma if stack_sigma > 0 else float("nan")
    large = whole_single / whole_stack if whole_stack > 0 else float("nan")
    return StackResult(out, len(prepared), single, stack_sigma, gain, ideal, large, clipped)


def best_wavelength_group(items: list[tuple[str, float, float]], fraction: float) -> list[str]:
    """Of (id, wavelength_um, bandwidth_um) triples, the ids of the largest group whose wavelengths all lie within `fraction` of one
    bandwidth of each other (the same 'matched wavelength' idea as comparisons). Ties go to the earlier-listed group. Stacking frames
    that sample the object at different wavelengths would blend different colours of the same object."""
    pts = sorted(items, key=lambda t: t[1])
    if not pts:
        return []
    width = fraction * float(np.median([b for _, _, b in pts]))
    best: list[tuple[str, float, float]] = []
    j = 0
    for i in range(len(pts)):
        while pts[i][1] - pts[j][1] > width + 1e-12:
            j += 1
        if i - j + 1 > len(best):
            best = pts[j:i + 1]
    order = {t[0]: k for k, t in enumerate(items)}
    return sorted((t[0] for t in best), key=lambda i: order[i])
