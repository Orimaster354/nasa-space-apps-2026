"""POST /measurements (aperture_flux) end to end on synthetic data, including every way it must refuse instead of guessing."""
import math

import pytest

from processing import irsa, quality
from tests.conftest import CENTER, make_cutout_fits
from tests.test_api import STAR, URIS, env, _no_nulls, _object      # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

OMEGA = (6.2 / 206264.806247) ** 2


def _first_obs(c):
    oid = _object(c)["objectId"]
    return c.get(f"/api/v1/objects/{oid}/observations").json()["items"][0]["observationId"]


def _body(obs, ra, dec, radius=20.0, kind="aperture_flux", ids=None):
    return {"kind": kind, "observationIds": ids if ids is not None else [obs],
            "region": {"center": {"raDeg": ra, "decDeg": dec}, "radiusArcsec": radius}}


def test_measures_the_injected_star_with_uncertainty_and_provenance(env):
    c, _, _ = env
    obs = _first_obs(c)
    r = c.post("/api/v1/measurements", json=_body(obs, *STAR, radius=20))
    assert r.status_code == 200, r.text
    m = r.json()
    _no_nulls(m)
    assert m["kind"] == "aperture_flux" and m["unit"] == "mJy" and m["observationIds"] == [obs]
    peak, sigma = 100.0, 1.2
    enclosed = 1 - math.exp(-((20 / 6.2) ** 2) / (2 * sigma ** 2))
    expected = peak * 2 * math.pi * sigma ** 2 * enclosed * OMEGA * 1e9
    assert m["value"] == pytest.approx(expected, rel=0.03)
    assert m["uncertainty"] > 0 and m["signalToNoise"] > 3 and m["signalToNoise"] == pytest.approx(m["value"] / m["uncertainty"])
    assert "Archive VARIANCE" in m["uncertaintyBasis"] and "no aperture correction" in m["method"]
    assert m["wavelengthUm"] == pytest.approx(2.7725, abs=0.01) and m["bandwidthUm"] == pytest.approx(0.03)   # from the synthetic WCS-WAVE table
    assert "Wavelength at the aperture centre" in m["method"]
    assert m["region"]["radiusArcsec"] == 20 and m["measurementId"].startswith("meas_")
    assert any(f["code"] == "SOURCE" for f in m["qualityFlags"])            # the archive's own flag is passed through
    again = c.post("/api/v1/measurements", json=_body(obs, *STAR, radius=20)).json()
    assert again["measurementId"] == m["measurementId"] and again["createdAt"] == m["createdAt"]   # stable, stored
    assert c.get(f"/api/v1/measurements/{m['measurementId']}").json() == m


def test_empty_sky_is_reported_as_not_detected(env):
    c, _, _ = env
    obs = _first_obs(c)
    m = c.post("/api/v1/measurements", json=_body(obs, CENTER[0], CENTER[1] - 0.011, radius=12)).json()   # about 40 arcsec from the star
    assert abs(m["signalToNoise"]) < 3
    assert any("no source is significantly detected" in w for w in m["warnings"])


def test_flagged_region_is_refused_not_zero(env, tmp_path):
    c, cut, _ = env
    obs = _first_obs(c)
    uri = next(iter(URIS))
    cut.files[uri] = make_cutout_fits(tmp_path / "bad.fits", *STAR, flag_value=quality.UNUSABLE_MASK & -quality.UNUSABLE_MASK)
    r = c.post("/api/v1/measurements", json=_body(obs, *STAR))
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "DATA_UNAVAILABLE" and err["retryable"] is False and "background pixels" in err["message"]


def test_position_off_the_image_is_refused(env):
    c, _, _ = env
    obs = _first_obs(c)
    r = c.post("/api/v1/measurements", json=_body(obs, CENTER[0] + 0.05, CENTER[1]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "DATA_UNAVAILABLE"


def test_bad_requests(env):
    c, _, _ = env
    obs = _first_obs(c)
    assert c.post("/api/v1/measurements", json=_body(obs, *STAR, kind="flux_change", ids=[obs, obs])).status_code == 400
    assert c.post("/api/v1/measurements", json=_body(obs, *STAR, ids=[obs, obs])).status_code == 400
    assert c.post("/api/v1/measurements", json={"kind": "aperture_flux", "observationIds": [obs]}).status_code == 400   # region required
    assert c.post("/api/v1/measurements", json=_body(obs, *STAR, radius=0)).status_code == 400
    assert c.post("/api/v1/measurements", json=_body(obs, *STAR, radius=500)).status_code == 400
    assert c.post("/api/v1/measurements", json=_body("obs_nope", *STAR)).status_code == 404
    assert c.post("/api/v1/measurements", json=_body(obs, 500, 0)).status_code == 400
    assert c.get("/api/v1/measurements/meas_nope").status_code == 404


def test_archive_failure_is_an_upstream_error(env):
    c, cut, _ = env
    obs = _first_obs(c)
    cut.fail = irsa.IrsaError("IRSA is busy", retryable=True)
    r = c.post("/api/v1/measurements", json=_body(obs, *STAR))
    assert r.status_code == 503 and r.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE" and r.json()["error"]["retryable"] is True
