"""P2-01: the difference picture removes each frame's sky level, says so, and states what its grey scale means."""
import io

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from tests.conftest import make_cutout_fits
from tests.test_api import STAR, URIS, _cmp, _observations, env                    # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic


def _with_pedestal(path, offset):
    with fits.open(path, mode="update") as h:
        h["IMAGE"].data = h["IMAGE"].data + offset
    return path


def test_a_uniform_sky_difference_is_removed_reported_and_does_not_shift_the_grey_scale(env, tmp_path):
    c, cut, _ = env
    _, obs = _observations(c)
    uris = list(URIS)
    cut.files[uris[1]] = _with_pedestal(make_cutout_fits(tmp_path / "bright_sky.fits", *STAR, rotation_deg=60.0, mjd=60990.2, flag_value=1 << 21), 40.0)
    j = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "difference").json()
    q = j["differenceQuality"]
    assert q["backgroundMatched"] is True and q["unit"] == "MJy/sr"
    assert q["removedOffset"] == pytest.approx(40.0, abs=1.0)                       # the pedestal we added is what was taken out
    assert q["targetSkyLevel"] - q["baseSkyLevel"] == pytest.approx(q["removedOffset"])
    png = np.array(Image.open(io.BytesIO(c.get(j["differenceImage"]["imageUrl"]).content)))
    lum, alpha = png[..., 0].astype(float), png[..., 1]
    assert abs(np.median(lum[alpha > 0]) - 127.5) < 6                               # grey stays at "no change": no pedestal leaks into the picture


def test_the_scale_is_explained_in_noise_units_and_the_caveats_reach_the_warnings(env):
    c, cut, _ = env
    _, obs = _observations(c)
    j = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "difference").json()
    q = j["differenceQuality"]
    assert q.get("noiseSigma") is None or q["noiseSigma"] > 0          # identical synthetic frames: no noise to measure, so the field is omitted
    joined = " ".join(q["caveats"])
    assert "Background matched" in joined and "not a detection" in joined and "wavelength" in joined and ("pixel-to-pixel noise" in joined or "could not be related to the noise" in joined)
    assert any("Background matched" in w for w in j["warnings"]) and any(("pixel-to-pixel noise" in w or "related to the noise" in w) for w in j["warnings"])      # shown by the existing UI


def test_other_modes_carry_no_difference_quality(env):
    c, cut, _ = env
    _, obs = _observations(c)
    j = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "blink").json()
    assert "differenceQuality" not in j


def test_the_scale_sentence_depends_on_the_number(env, tmp_path):
    """Wide scale (set by star residuals): faint changes stay near grey; narrow scale: most of what is seen is noise. Never the wrong one."""
    c, cut, _ = env
    _, obs = _observations(c)
    uris = list(URIS)
    cut.files[uris[1]] = make_cutout_fits(tmp_path / "noisy.fits", *STAR, rotation_deg=60.0, mjd=60990.2, flag_value=1 << 21, noise=1.0, noise_seed=5)
    j = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "difference").json()
    q = j["differenceQuality"]
    line = next(x for x in q["caveats"] if x.startswith("Grey is no change") or x.startswith("The scale"))
    if q.get("fullScaleSigma") is not None and q["fullScaleSigma"] >= 6:
        assert "bright stars leave the largest residuals" in line and "most of what you see is noise" not in line
    elif q.get("fullScaleSigma") is not None:
        assert "most of what you see is noise" in line
