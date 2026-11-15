"""Alignment residual recovered from synthetic star fields with a known shift, a moving object, noise and brightness changes."""
import numpy as np
import pytest

from processing import alignment

pytestmark = pytest.mark.synthetic

SCALE = 2.0
N = 150
SIGMA_PX = (7.4 / 2.355) / SCALE


def _field(stars, shift_arcsec=(0.0, 0.0), gain=1.0, noise=0.5, seed=0):
    yy, xx = np.mgrid[0:N, 0:N]
    img = np.zeros((N, N))
    for x, y, peak in stars:
        cx, cy = x + shift_arcsec[0] / SCALE, y + shift_arcsec[1] / SCALE
        img += gain * peak * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * SIGMA_PX ** 2))
    return img + np.random.default_rng(seed).normal(0, noise, img.shape) + 3.0


def _stars(n=8, seed=1):
    r = np.random.default_rng(seed)
    return [(float(r.uniform(15, N - 15)), float(r.uniform(15, N - 15)), float(r.uniform(60, 200))) for _ in range(n)]


def test_recovers_a_known_shift():
    st = _stars()
    est = alignment.estimate_offset(_field(st, seed=1), _field(st, shift_arcsec=(3.0, -2.0), seed=2))
    assert est.measured and est.n_matched >= 6
    assert est.dx_arcsec == pytest.approx(3.0, abs=0.5) and est.dy_arcsec == pytest.approx(-2.0, abs=0.5)
    assert est.residual_arcsec == pytest.approx(np.hypot(3.0, 2.0), abs=0.6)


def test_identical_frames_have_near_zero_residual():
    st = _stars()
    est = alignment.estimate_offset(_field(st, seed=1), _field(st, seed=2))
    assert est.measured and est.residual_arcsec < 0.4


def test_brightness_change_does_not_bias_the_offset():
    st = _stars()
    est = alignment.estimate_offset(_field(st, seed=1), _field(st, shift_arcsec=(1.0, 1.0), gain=0.6, seed=2))
    assert est.measured and est.dx_arcsec == pytest.approx(1.0, abs=0.5) and est.dy_arcsec == pytest.approx(1.0, abs=0.5)


def test_a_moving_object_does_not_drag_the_median():
    st = _stars(9)
    moved = list(st)
    x, y, p = moved[0]
    moved[0] = (x + 4.0, y, p)                                    # one object really moved by 8 arcsec
    est = alignment.estimate_offset(_field(st, seed=1), _field(moved, shift_arcsec=(1.0, 0.0), seed=2))
    assert est.measured and est.dx_arcsec == pytest.approx(1.0, abs=0.5)


def test_too_few_sources_is_not_measured_not_zero():
    est = alignment.estimate_offset(_field(_stars(2), seed=1), _field(_stars(2), seed=2))
    assert est.measured is False and est.residual_arcsec is None and "Too few" in est.reason or "matched" in est.reason


def test_blank_or_nan_images_are_not_measured():
    est = alignment.estimate_offset(np.full((N, N), np.nan), np.full((N, N), np.nan))
    assert est.measured is False
