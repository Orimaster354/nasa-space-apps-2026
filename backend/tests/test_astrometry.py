"""Motion tracking recovered from synthetic star fields with a known mover, frame shifts, noise, and the validation branches (P1-04, P1-05)."""
import math

import numpy as np
import pytest

from processing import astrometry

pytestmark = pytest.mark.synthetic

SCALE = 2.0
N = 150
SIGMA_PX = (7.4 / 2.355) / SCALE


def _stars(n=9, seed=1):
    r = np.random.default_rng(seed)
    return [(float(r.uniform(15, N - 15)), float(r.uniform(15, N - 15)), float(r.uniform(80, 200))) for _ in range(n)]


def _image(stars, mover, shift_px, noise, seed):
    yy, xx = np.mgrid[0:N, 0:N]
    img = np.zeros((N, N))
    for x, y, p in stars:
        img += p * np.exp(-((xx - x - shift_px[0]) ** 2 + (yy - y - shift_px[1]) ** 2) / (2 * SIGMA_PX ** 2))
    mx, my, mp = mover
    img += mp * np.exp(-((xx - mx - shift_px[0]) ** 2 + (yy - my - shift_px[1]) ** 2) / (2 * SIGMA_PX ** 2))
    return img + np.random.default_rng(seed).normal(0, noise, img.shape) + 3.0


def _sequence(west=-3.0, north=2.0, years=0.8, frames=24, noise=0.6, frame_shift_arcsec=1.5, seed=5):
    """A target moving west/north (arcsec per year) among static stars; every frame also gets a random pointing shift."""
    stars = _stars()
    rng = np.random.default_rng(seed)
    out, truth = [], []
    for i in range(frames):
        t = 2025.3 + years * i / (frames - 1)
        dt = t - 2025.3
        pos = (60 + west * dt / SCALE * -1.0 * -1.0, 70 + north * dt / SCALE)            # +x is west on the grid
        pos = (60 + west * dt / SCALE, pos[1])
        shift = (rng.normal(0, frame_shift_arcsec) / SCALE, rng.normal(0, frame_shift_arcsec) / SCALE)
        out.append((f"f{i}", t, _image(stars, (pos[0], pos[1], 150.0), (shift[0], shift[1]), noise, 100 + i)))
        truth.append((t, pos))
    expected = lambda year: (60 + west * (year - 2025.3) / SCALE, 70 + north * (year - 2025.3) / SCALE)
    return out, expected


def test_a_known_motion_is_recovered_despite_random_pointing_shifts():
    frames, expected = _sequence(west=-3.0, north=2.0)
    rows = astrometry.track(frames, expected, SCALE)
    assert len(rows) >= 20
    fit = astrometry.fit_motion(rows)
    assert fit.west_rate == pytest.approx(-3.0, abs=3 * fit.west_err + 0.3) and fit.north_rate == pytest.approx(2.0, abs=3 * fit.north_err + 0.3)
    assert fit.total == pytest.approx(math.hypot(3.0, 2.0), abs=0.5) and fit.span_years == pytest.approx(0.8, abs=0.05)
    assert fit.significance > 5 and fit.mean_stars >= 3
    assert astrometry.validate(fit) == []                                                  # clean data passes every check
    assert fit.direction_deg == pytest.approx(math.degrees(math.atan2(3.0, 2.0)), abs=8)   # moving west and north: between north and west


def test_a_target_that_does_not_move_is_measured_as_not_moving():
    frames, expected = _sequence(west=0.0, north=0.0)
    fit = astrometry.fit_motion(astrometry.track(frames, expected, SCALE))
    assert fit.total < 1.0 or fit.significance < 5
    assert any("times its uncertainty" in w for w in astrometry.validate(fit)) or fit.total < 1.0


def test_the_pointing_shift_of_each_frame_is_removed_not_mistaken_for_motion():
    frames, expected = _sequence(west=0.0, north=0.0, frame_shift_arcsec=4.0, seed=9)
    fit = astrometry.fit_motion(astrometry.track(frames, expected, SCALE))
    assert fit.total < 1.5                                                                   # large random shifts, yet no apparent motion


def test_each_validation_check_can_fail_on_its_own():
    base = astrometry.MotionFit(n=20, span_years=0.8, west_rate=-3.0, west_err=0.3, north_rate=2.0, north_err=0.3, total=3.6, total_err=0.3,
                                rms_arcsec=1.0, mean_stars=8)
    assert astrometry.validate(base) == []
    from dataclasses import replace
    assert any("only 5 usable frames" in w for w in astrometry.validate(replace(base, n=5)))
    assert any("time baseline" in w for w in astrometry.validate(replace(base, span_years=0.1)))
    assert any("scatter" in w for w in astrometry.validate(replace(base, rms_arcsec=5.0)))
    assert any("times its uncertainty" in w for w in astrometry.validate(replace(base, total_err=2.0)))
    assert astrometry.validate(None) == ["too few frames to fit a motion"]
    several = astrometry.validate(replace(base, n=3, span_years=0.05, rms_arcsec=9.0, total_err=3.0))
    assert len(several) == 4                                                                 # every failed check is reported, not just the first


def test_frames_without_the_target_or_enough_field_stars_are_skipped_not_guessed():
    frames, expected = _sequence()
    blank = ("blank", 2025.6, np.full((N, N), 3.0) + np.random.default_rng(3).normal(0, 0.6, (N, N)))
    far = lambda year: (130.0, 20.0)                                                         # expected far from any source
    assert astrometry.track([blank], expected, SCALE) == []
    assert astrometry.track(frames[:3], far, SCALE) == []
    assert astrometry.fit_motion([]) is None and astrometry.fit_motion(astrometry.track(frames[:2], expected, SCALE)) is None


def test_too_short_a_baseline_cannot_be_validated():
    frames, expected = _sequence(years=0.1, frames=12)
    fit = astrometry.fit_motion(astrometry.track(frames, expected, SCALE))
    assert any("time baseline" in w for w in astrometry.validate(fit))
