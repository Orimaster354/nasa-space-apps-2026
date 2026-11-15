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


# ---- flux_change -------------------------------------------------------------------------------------------------------
def _obs_ids(c):
    oid = _object(c)["objectId"]
    return [i["observationId"] for i in c.get(f"/api/v1/objects/{oid}/observations").json()["items"]]    # [D3 2025-05, D3 2025-11, D6 2026-03]


def _change(c, base, target, ra=STAR[0], dec=STAR[1], radius=20.0):
    return c.post("/api/v1/measurements", json=_body(None, ra, dec, radius=radius, kind="flux_change", ids=[base, target]))


def _flux_of(peak):
    sigma = 1.2
    return peak * 2 * math.pi * sigma ** 2 * (1 - math.exp(-((20 / 6.2) ** 2) / (2 * sigma ** 2))) * OMEGA * 1e9


def _brighten(cut, tmp_path, peak):
    uri = list(URIS)[1]
    cut.files[uri] = make_cutout_fits(tmp_path / "brighter.fits", *STAR, rotation_deg=60.0, mjd=60990.2, detector=3,
                                      flag_value=(1 << 21), peak=peak)


def test_flux_change_measures_an_injected_brightening_with_propagated_uncertainty(env, tmp_path):
    c, cut, _ = env
    ids = _obs_ids(c)
    _brighten(cut, tmp_path, peak=150.0)
    r = _change(c, ids[0], ids[1])
    assert r.status_code == 200, r.text
    m = r.json()
    _no_nulls(m)
    assert m["kind"] == "flux_change" and m["unit"] == "mJy" and m["observationIds"] == ids[:2]
    assert m["value"] == pytest.approx(_flux_of(150.0) - _flux_of(100.0), rel=0.06)
    one = c.post("/api/v1/measurements", json=_body(ids[0], *STAR, radius=20)).json()
    two = c.post("/api/v1/measurements", json=_body(ids[1], *STAR, radius=20)).json()
    floor = 0.03 * math.hypot(one["value"], two["value"])                                            # the systematic floor, 3 percent of each frame's flux
    assert m["uncertainty"] == pytest.approx(math.sqrt(one["uncertainty"] ** 2 + two["uncertainty"] ** 2 + floor ** 2))
    assert m["signalToNoise"] == pytest.approx(m["value"] / m["uncertainty"]) and m["signalToNoise"] > 3
    assert "Quadrature sum" in m["uncertaintyBasis"] and "systematic floor of 3 percent" in m["uncertaintyBasis"] and "matched to within a quarter of one bandwidth" in m["method"]
    assert any("does not by itself show the source varied" in w for w in m["warnings"])
    assert not any("detect" in w.lower() and "significantly detects" not in w and "does not" not in w for w in m["warnings"])   # no detection claims
    assert any(w.startswith("calibration:") for w in m["warnings"]) and any(w.startswith("alignment:") for w in m["warnings"])
    assert _change(c, ids[0], ids[1]).json()["measurementId"] == m["measurementId"]             # stable and stored
    swapped = _change(c, ids[1], ids[0]).json()
    assert swapped["value"] == pytest.approx(-m["value"], rel=1e-6) and swapped["measurementId"] != m["measurementId"]


def test_unchanged_source_is_consistent_with_no_change(env):
    c, _, _ = env
    ids = _obs_ids(c)
    m = _change(c, ids[0], ids[1]).json()
    assert abs(m["signalToNoise"]) < 3
    assert any("consistent with no change" in w for w in m["warnings"])


def test_empty_sky_says_neither_frame_detects_a_source(env):
    c, _, _ = env
    ids = _obs_ids(c)
    m = _change(c, ids[0], ids[1], ra=CENTER[0], dec=CENTER[1] - 0.011, radius=12).json()
    assert any("Neither frame significantly detects a source" in w for w in m["warnings"])


def test_different_wavelengths_are_refused_not_reported_as_change(env, tmp_path):
    c, cut, _ = env
    ids = _obs_ids(c)
    cut.files[list(URIS)[1]] = make_cutout_fits(tmp_path / "shifted.fits", *STAR, rotation_deg=60.0, mjd=60990.2, detector=3,
                                                flag_value=(1 << 21), wave_shift_x=400.0)
    r = _change(c, ids[0], ids[1])
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "DATA_UNAVAILABLE" and "same wavelength" in err["message"] and "as a spectrum" in err["message"]


def test_different_bands_are_refused(env):
    c, _, _ = env
    ids = _obs_ids(c)
    r = _change(c, ids[0], ids[2])
    assert r.status_code == 422 and "same wavelength" in r.json()["error"]["message"]


def test_flux_change_needs_two_different_measurable_frames(env, tmp_path):
    c, cut, _ = env
    ids = _obs_ids(c)
    assert _change(c, ids[0], ids[0]).status_code == 400
    assert c.post("/api/v1/measurements", json=_body(ids[0], *STAR, kind="flux_change", ids=[ids[0]])).status_code == 400
    cut.files[list(URIS)[1]] = make_cutout_fits(tmp_path / "bad2.fits", *STAR, flag_value=quality.UNUSABLE_MASK & -quality.UNUSABLE_MASK)
    r = _change(c, ids[0], ids[1])
    assert r.status_code == 422 and r.json()["error"]["code"] == "DATA_UNAVAILABLE"
