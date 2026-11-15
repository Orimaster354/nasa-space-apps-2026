"""Measure whether a source MOVES across many SPHEREx frames, relative to the stars around it. Deterministic code; no LLM.

Idea (the same one used for the Epsilon Indi B investigation, where it recovered 4.55 arcsec/yr against a catalogue value of 4.68):
  1. Put every frame on one sky grid (already done by imaging.to_grid).
  2. In each frame find the target (the detected source nearest its expected position) and the other bright sources (field stars).
  3. Match the field stars to a reference frame. Their median offset is the pointing/registration error of THIS frame, so subtracting it
     leaves the target's displacement relative to the field.
  4. Fit a straight line to that displacement against time; the slope is the motion, its error comes from the scatter around the line.

Validation (item P1-05). A fitted slope is only reported as a measured motion when ALL of these hold; the thresholds are our own
heuristics, stated here and tested:
  * at least MIN_FRAMES frames with at least MIN_STARS matched field stars each,
  * a time baseline of at least MIN_SPAN_YR years,
  * scatter of the points around the line at most MAX_RMS_ARCSEC,
  * the total motion at least MIN_SIGNIFICANCE times its uncertainty.
Otherwise the result is an "unvalidated displacement": shown with its reasons, never called a motion.

Known limits: the uncertainty assumes independent errors from frame to frame; the baseline is short (months), so a straight line is
assumed; a saturated or masked source cannot be centroided (no result), and a neighbour near the expected position can be
mistaken for the target (the fit then shows no motion).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import alignment

MIN_FRAMES = 8
MIN_SPAN_YR = 0.3
MIN_STARS = 3
MAX_RMS_ARCSEC = 3.0
MIN_SIGNIFICANCE = 5.0
TARGET_MATCH_ARCSEC = 8.0
STAR_MATCH_ARCSEC = 10.0


@dataclass
class FramePosition:
    key: str                     # whatever identifies the frame (an observation id)
    t_year: float
    raw_x: float                 # target centroid on the common grid, as it appears in this frame (grid pixels, y up)
    raw_y: float
    dx_arcsec: float             # displacement from the reference frame after removing this frame's field offset; +x west
    dy_arcsec: float             # +y north
    n_stars: int
    star_scatter_arcsec: float


@dataclass
class MotionFit:
    n: int
    span_years: float
    west_rate: float             # arcsec per year, +west
    west_err: float
    north_rate: float            # arcsec per year, +north
    north_err: float
    total: float
    total_err: float
    rms_arcsec: float            # scatter of the points around the fitted lines
    mean_stars: float

    @property
    def significance(self) -> float:
        return self.total / self.total_err if self.total_err > 0 else 0.0

    @property
    def direction_deg(self) -> float:
        """Direction of motion in degrees east of north (0 = north, 90 = east)."""
        return math.degrees(math.atan2(-self.west_rate, self.north_rate)) % 360.0


def track(frames: list[tuple[str, float, np.ndarray]], expected: Callable[[float], tuple[float, float]], scale_arcsec: float) -> list[FramePosition]:
    """frames = (key, year, image on the common grid). `expected(year)` gives the target's expected grid pixel (x, y).
    Frames where the target or too few field stars are found are left out (the caller counts them)."""
    ref = None
    out: list[FramePosition] = []
    for key, year, arr in frames:
        src = alignment.find_sources(arr, scale_arcsec)
        if len(src) < MIN_STARS + 1:
            continue
        ex, ey = expected(year)
        d = np.hypot(src[:, 0] - ex, src[:, 1] - ey) * scale_arcsec
        k = int(np.argmin(d))
        if d[k] > TARGET_MATCH_ARCSEC:
            continue
        target, others = src[k], np.delete(src, k, axis=0)
        if ref is None:
            ref = {"others": others, "target": target, "year": year}
        dist = np.hypot(ref["others"][:, None, 0] - others[None, :, 0], ref["others"][:, None, 1] - others[None, :, 1]) * scale_arcsec
        pairs = [(int(np.argmin(dist[i])), i) for i in range(len(ref["others"])) if dist[i].min() <= STAR_MATCH_ARCSEC]
        if len(pairs) < MIN_STARS:
            continue
        off = np.array([others[j, :2] - ref["others"][i, :2] for j, i in pairs]) * scale_arcsec
        g = np.median(off, axis=0)
        p = (target[:2] - ref["target"][:2]) * scale_arcsec - g
        scatter = float(1.4826 * np.median(np.abs(off - g), axis=0).mean())
        out.append(FramePosition(key, year, float(target[0]), float(target[1]), float(p[0]), float(p[1]), len(pairs), scatter))
    return out


def fit_motion(rows: list[FramePosition]) -> MotionFit | None:
    """Least-squares straight line of displacement against time, per axis. None when there are too few points to fit at all."""
    if len(rows) < 4:
        return None
    t = np.array([r.t_year for r in rows]) - rows[0].t_year
    if float(t.max() - t.min()) <= 0:
        return None
    a = np.vstack([t, np.ones_like(t)]).T
    inv = np.linalg.inv(a.T @ a)
    res, rms_sq = [], 0.0
    for col in ("dx_arcsec", "dy_arcsec"):
        y = np.array([getattr(r, col) for r in rows])
        coef = np.linalg.lstsq(a, y, rcond=None)[0]
        resid = y - a @ coef
        sigma = float(resid.std(ddof=2))
        res.append((float(coef[0]), math.sqrt(float((sigma ** 2) * inv[0, 0]))))
        rms_sq += sigma ** 2
    (w, we), (n, ne) = res
    total = math.hypot(w, n)
    total_err = math.sqrt((w * we) ** 2 + (n * ne) ** 2) / total if total > 0 else math.hypot(we, ne)
    return MotionFit(len(rows), float(t.max() - t.min()), w, we, n, ne, total, total_err, math.sqrt(rms_sq / 2.0),
                     float(np.mean([r.n_stars for r in rows])))


def validate(fit: MotionFit | None) -> list[str]:
    """Reasons the motion is NOT validated (an empty list means it passes every check)."""
    if fit is None:
        return ["too few frames to fit a motion"]
    why = []
    if fit.n < MIN_FRAMES:
        why.append(f"only {fit.n} usable frames (need {MIN_FRAMES})")
    if fit.span_years < MIN_SPAN_YR:
        why.append(f"the time baseline is only {fit.span_years:.2f} years (need {MIN_SPAN_YR:g})")
    if fit.rms_arcsec > MAX_RMS_ARCSEC:
        why.append(f"the positions scatter by {fit.rms_arcsec:.1f} arcsec around a straight line (limit {MAX_RMS_ARCSEC:g})")
    if fit.significance < MIN_SIGNIFICANCE:
        why.append(f"the motion is only {fit.significance:.1f} times its uncertainty (need {MIN_SIGNIFICANCE:g})")
    return why
