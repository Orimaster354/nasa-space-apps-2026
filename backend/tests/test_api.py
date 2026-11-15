"""End-to-end API tests on synthetic data: fake archive + synthetic FITS, real processing/rendering/serialization."""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from processing import irsa
from services.core import NotFound, Services, observation_id_for
from store.registry import Registry
from tests.conftest import CENTER, make_cutout_fits

pytestmark = pytest.mark.synthetic

STAR = (CENTER[0] + 0.0008, CENTER[1] + 0.0005)
URIS = {
    "ibe/data/spherex/dr1/level2/a/3/f1.fits": dict(mjd=60800.7, rotation=0.0, detector=3, band="SPHEREx-D3"),
    "ibe/data/spherex/dr1/level2/b/3/f2.fits": dict(mjd=60990.2, rotation=60.0, detector=3, band="SPHEREx-D3"),
    "ibe/data/spherex/qr3/level2/c/6/f3.fits": dict(mjd=61100.9, rotation=120.0, detector=6, band="SPHEREx-D6"),
}


def _file(uri, spec):
    coll = "spherex_qr3" if "/qr3/" in uri else "spherex_dr1"
    return irsa.ArchiveFile(
        uri=uri, collection=coll, obs_id="2025W19_1B_0116_1", bandpass=spec["band"], center_ra=134.41, center_dec=-9.0,
        footprint=((133.2, -6.8), (136.5, -7.9), (135.6, -11.2), (132.2, -10.1)), wave_lo_um=1.63, wave_hi_um=2.43, res_power=40.6,
        mjd_start=spec["mjd"], mjd_end=spec["mjd"] + 0.01, exposure_s=113.6, pixel_arcsec=6.15,
        release_date="2026-09-27 23:59:28", target_name="SPS_1", est_size_bytes=65791)


class FakeCutouts:
    """Stands in for CutoutCache: serves synthetic FITS files, counts fetches, can fail on demand."""

    def __init__(self, tmp_path):
        self.tmp, self.calls, self.fail = tmp_path, 0, None
        self.files = {u: make_cutout_fits(tmp_path / f"{i}.fits", *STAR, rotation_deg=s["rotation"], mjd=s["mjd"], detector=s["detector"],
                                           flag_value=(1 << 21))
                      for i, (u, s) in enumerate(URIS.items())}

    def get(self, url):
        self.calls += 1
        if self.fail:
            raise self.fail
        for uri, path in self.files.items():
            if uri in url:
                return path
        raise irsa.IrsaError("not found", retryable=False)


@pytest.fixture
def env(tmp_path):
    cut = FakeCutouts(tmp_path)
    state = {"files": [_file(u, s) for u, s in URIS.items()], "discover_error": None}

    def discover(ra, dec):
        if state["discover_error"]:
            raise state["discover_error"]
        return state["files"]

    def resolver(name):
        if name.lower() == "wise 0855":
            return CENTER
        raise NotFound(f"No object found for '{name}'.")

    svc = Services(Registry(tmp_path / "r.sqlite"), cut, tmp_path / "images", discover=discover, resolver=resolver)
    return TestClient(create_app(svc)), cut, state


def _no_nulls(v, path="$"):
    assert v is not None, f"null at {path}"
    if isinstance(v, dict):
        [_no_nulls(x, f"{path}.{k}") for k, x in v.items()]
    elif isinstance(v, list):
        [_no_nulls(x, f"{path}[{i}]") for i, x in enumerate(v)]


def _object(client):
    r = client.get("/api/v1/objects/resolve", params={"q": "WISE 0855"})
    assert r.status_code == 200
    return r.json()


def test_resolve_by_name_coordinates_and_search(env):
    c, _, _ = env
    o = _object(c)
    assert o["objectId"].startswith("obj_") and o["resolver"]["name"] and o["coordinates"]["raDeg"] == pytest.approx(CENTER[0])
    by_coords = c.get("/api/v1/objects/resolve", params={"ra": CENTER[0], "dec": CENTER[1]}).json()
    assert by_coords["objectId"] == o["objectId"]                       # same position -> same stable ID
    assert c.get("/api/v1/objects/search", params={"q": "133.795 -7.245"}).json()["total"] == 1
    empty = c.get("/api/v1/objects/search", params={"q": "nothing here"}).json()
    assert empty["items"] == [] and "coverageNote" in empty
    assert c.get(f"/api/v1/objects/{o['objectId']}").json()["name"] == "WISE 0855"
    _no_nulls(o)


def test_bad_inputs_use_the_error_envelope(env):
    c, _, _ = env
    r = c.get("/api/v1/objects/resolve")
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    r = c.get("/api/v1/objects/resolve", params={"ra": 500, "dec": 0})
    assert r.status_code == 400
    r = c.get("/api/v1/objects/obj_unknown")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_observations_list_contract_shape_sorting_and_filters(env):
    c, _, _ = env
    oid = _object(c)["objectId"]
    page = c.get(f"/api/v1/objects/{oid}/observations").json()
    assert page["total"] == 3 and [i["observedAt"][:4] for i in page["items"]] == ["2025", "2025", "2026"]
    first = page["items"][0]
    assert first["dataRelease"] == "dr1" and first["bandpass"] == "SPHEREx-D3" and first["detector"] == "3"
    assert first["image"] == {"status": "available"} and first["wavelengthRangeUm"] == {"min": 1.63, "max": 2.43}
    assert first["center"] == {"raDeg": 134.41, "decDeg": -9.0} and len(first["footprint"]) == 4   # full detector image, from the archive
    assert first["sourceMetadata"]["pixelScaleArcsec"] == 6.15 and first["irsaObservationId"] == "f1"
    assert first["objectIds"] == [oid] and first["observationId"] == observation_id_for("ibe/data/spherex/dr1/level2/a/3/f1.fits")
    _no_nulls(page)
    desc = c.get(f"/api/v1/objects/{oid}/observations", params={"sort": "-observedAt"}).json()
    assert desc["items"][0]["dataRelease"] == "qr3"
    assert c.get(f"/api/v1/objects/{oid}/observations", params={"dataRelease": "qr3"}).json()["total"] == 1
    assert c.get(f"/api/v1/objects/{oid}/observations", params={"from": "2025-06-01", "to": "2026-12-31"}).json()["total"] == 2
    d3 = c.get(f"/api/v1/objects/{oid}/observations", params={"bandpass": "SPHEREx-D3"}).json()
    assert d3["total"] == 2 and {i["bandpass"] for i in d3["items"]} == {"SPHEREx-D3"}   # same-band frames for a blink
    nm = c.get(f"/api/v1/objects/{oid}/observations", params={"dataRelease": "dr9"}).json()
    assert nm["items"] == [] and "match the filters" in nm["coverageNote"]
    assert c.get(f"/api/v1/objects/{oid}/observations", params={"limit": 1, "offset": 1}).json()["items"][0]["observedAt"] == page["items"][1]["observedAt"]
    assert c.get(f"/api/v1/observations/{first['observationId']}").json()["observationId"] == first["observationId"]
    assert c.get("/api/v1/observations/obs_nope").status_code == 404


def test_no_coverage_is_an_empty_page_with_a_note_not_an_error(env):
    c, _, state = env
    oid = _object(c)["objectId"]
    state["files"] = []
    page = c.get(f"/api/v1/objects/{oid}/observations").json()
    assert page["items"] == [] and page["total"] == 0 and "coverageNote" in page


def test_archive_down_is_a_retryable_503(env):
    c, _, state = env
    oid = _object(c)["objectId"]
    state["discover_error"] = irsa.IrsaError("archive down")
    r = c.get(f"/api/v1/objects/{oid}/observations")
    assert r.status_code == 503 and r.json()["error"] == {**r.json()["error"], "code": "UPSTREAM_UNAVAILABLE", "retryable": True}


def test_archive_down_serves_the_last_saved_listing_with_a_note(env):
    c, _, state = env
    oid = _object(c)["objectId"]
    first = c.get(f"/api/v1/objects/{oid}/observations").json()
    assert first["total"] == 3 and "coverageNote" not in first
    c.app.state.services._disc_cache.clear()                                    # forget the in-memory copy; the saved snapshot remains
    state["discover_error"] = irsa.IrsaError("archive down")
    r = c.get(f"/api/v1/objects/{oid}/observations")
    assert r.status_code == 200
    page = r.json()
    assert page["total"] == 3 and "last saved listing" in page["coverageNote"] and "may be out of date" in page["coverageNote"]
    assert [i["observationId"] for i in page["items"]] == [i["observationId"] for i in first["items"]]
    assert all(i["footprint"] for i in page["items"])                           # polygons survive the save and reload


def test_a_listing_missing_a_collection_is_flagged_and_never_saved(env):
    c, _, state = env
    oid = _object(c)["objectId"]
    partial = irsa.Discovery(state["files"][:2])
    partial.incomplete = ["spherex_qr3"]
    state["files"] = partial
    page = c.get(f"/api/v1/objects/{oid}/observations").json()
    assert page["total"] == 2 and "may be incomplete" in page["coverageNote"] and "spherex_qr3" in page["coverageNote"]
    c.app.state.services._disc_cache.clear()
    state["discover_error"] = irsa.IrsaError("archive down")
    assert c.get(f"/api/v1/objects/{oid}/observations").status_code == 503      # the partial listing was not saved as a snapshot


def _observations(c):
    oid = _object(c)["objectId"]
    return oid, c.get(f"/api/v1/objects/{oid}/observations").json()["items"]


def test_images_share_one_grid_and_are_real_pngs(env):
    c, cut, _ = env
    _, obs = _observations(c)
    imgs = [c.get(f"/api/v1/observations/{o['observationId']}/image", params={"fovArcmin": 2}).json() for o in obs]
    assert all(i["availability"] == {"status": "available"} for i in imgs)
    assert len({i["gridId"] for i in imgs}) == 1 and len({(i["width"], i["height"]) for i in imgs}) == 1
    i = imgs[0]
    assert i["wcs"]["approximate"] is False and i["stretch"]["mode"] == "asinh" and i["width"] == 60
    _no_nulls(i)
    png = c.get(i["imageUrl"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(png.content)).size == (i["width"], i["height"])
    n = cut.calls
    c.get(f"/api/v1/observations/{obs[0]['observationId']}/image", params={"fovArcmin": 2})
    assert cut.calls == n                                                   # second request served from the rendered cache
    other = c.get(f"/api/v1/observations/{obs[0]['observationId']}/image", params={"fovArcmin": 4}).json()
    assert other["gridId"] != i["gridId"]


def test_pixel_to_sky_matches_the_star_in_the_rendered_image(env):
    c, _, _ = env
    _, obs = _observations(c)
    oid = obs[1]["observationId"]                                           # a rotated pointing
    img = c.get(f"/api/v1/observations/{oid}/image", params={"fovArcmin": 2, "stretch": "linear"}).json()
    arr = np.array(Image.open(io.BytesIO(c.get(img["imageUrl"]).content))).astype(float)
    mask = arr > 0.5 * arr.max()
    yy, xx = np.nonzero(mask)
    x, y = (xx * arr[mask]).sum() / arr[mask].sum(), (yy * arr[mask]).sum() / arr[mask].sum()
    sky = c.get(f"/api/v1/observations/{oid}/pixel-to-sky", params={"x": x, "y": y, "fovArcmin": 2}).json()["sky"]
    err = np.hypot((sky["raDeg"] - STAR[0]) * np.cos(np.deg2rad(STAR[1])), sky["decDeg"] - STAR[1]) * 3600
    assert err < 1.5                                                        # arcsec


def test_quality_comes_from_real_flags_after_the_image_is_read(env):
    c, _, _ = env
    _, obs = _observations(c)
    assert obs[0]["qualitySummary"] == "unknown"
    c.get(f"/api/v1/observations/{obs[0]['observationId']}/image")
    after = c.get(f"/api/v1/observations/{obs[0]['observationId']}").json()
    assert after["qualitySummary"] == "good"                                # SOURCE-flagged pixels are not bad data
    assert after["qualityFlags"][0]["code"] == "SOURCE" and after["qualityFlags"][0]["origin"] == "source"


def test_download_failure_is_reported_in_availability_not_as_http_error(env):
    c, cut, _ = env
    _, obs = _observations(c)
    cut.fail = irsa.IrsaError("archive busy", retryable=True)
    r = c.get(f"/api/v1/observations/{obs[0]['observationId']}/image")
    body = r.json()
    assert r.status_code == 200 and body["availability"]["status"] == "failed" and body["availability"]["retryAfterSeconds"] == 30
    assert "imageUrl" not in body
    cut.fail = irsa.IrsaError("gone", retryable=False)
    assert c.get(f"/api/v1/observations/{obs[1]['observationId']}/image").json()["availability"]["status"] == "unavailable"


def test_invalid_image_parameters(env):
    c, _, _ = env
    _, obs = _observations(c)
    oid = obs[0]["observationId"]
    assert c.get(f"/api/v1/observations/{oid}/image", params={"stretch": "bogus"}).status_code == 400
    assert c.get(f"/api/v1/observations/{oid}/image", params={"fovArcmin": 500}).status_code == 400
    assert c.get(f"/api/v1/observations/{oid}/image", params={"centerRa": 10}).status_code == 400
    assert c.get(f"/api/v1/observations/{oid}/image", params={"lowPercentile": 90, "highPercentile": 10}).status_code == 400


# ---- comparisons -----------------------------------------------------------------------------------------------------
def _cmp(c, base, target, mode):
    return c.post("/api/v1/comparisons", json={"baseObservationId": base, "targetObservationId": target, "mode": mode})


def test_blink_comparison_same_band(env):
    c, _, _ = env
    _, obs = _observations(c)                                   # [D3 2025-05, D3 2025-11, D6 2026-03]
    r = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "blink")
    assert r.status_code == 200
    cmp_ = r.json()
    assert cmp_["comparisonKind"] == "same_band_epochs" and cmp_["availability"] == {"status": "available"}
    assert cmp_["compatibility"]["verdict"] == "comparable_with_caveats"
    by = {k["name"]: k["result"] for k in cmp_["compatibility"]["checks"]}
    assert by["wavelength"] == "pass" and by["calibration"] == "notEvaluated" and by["alignment"] == "notEvaluated"
    assert cmp_["baseImage"]["gridId"] == cmp_["targetImage"]["gridId"] and "differenceImage" not in cmp_
    assert (cmp_["baseImage"]["width"], cmp_["baseImage"]["height"]) == (cmp_["targetImage"]["width"], cmp_["targetImage"]["height"])
    _no_nulls(cmp_)
    assert c.get(cmp_["baseImage"]["imageUrl"]).status_code == 200
    assert c.get(f"/api/v1/comparisons/{cmp_['comparisonId']}").json() == cmp_
    assert _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "blink").json()["comparisonId"] == cmp_["comparisonId"]   # stable id


def test_cross_band_difference_is_refused_with_reasons_and_no_images(env):
    c, _, _ = env
    _, obs = _observations(c)
    cmp_ = _cmp(c, obs[0]["observationId"], obs[2]["observationId"], "difference").json()
    assert cmp_["comparisonKind"] == "cross_band" and cmp_["compatibility"]["verdict"] == "not_comparable"
    assert {k["name"]: k["result"] for k in cmp_["compatibility"]["checks"]}["wavelength"] == "fail"
    assert not any(k in cmp_ for k in ("baseImage", "targetImage", "differenceImage"))


def test_cross_band_blink_is_allowed_but_labelled(env):
    c, _, _ = env
    _, obs = _observations(c)
    cmp_ = _cmp(c, obs[0]["observationId"], obs[2]["observationId"], "blink").json()
    assert cmp_["comparisonKind"] == "cross_band" and cmp_["compatibility"]["verdict"] == "comparable_with_caveats"
    assert "baseImage" in cmp_ and any("spectral" in w for w in cmp_["warnings"])


def test_difference_image_shows_a_moved_star_as_a_bright_and_a_dark_lobe(env, tmp_path):
    c, cut, _ = env
    _, obs = _observations(c)
    moved = (STAR[0], STAR[1] + 12 / 3600.0)                      # target star is 12 arcsec further north
    uris = list(URIS)
    cut.files[uris[1]] = make_cutout_fits(tmp_path / "moved.fits", *moved, rotation_deg=60.0, mjd=60990.2, flag_value=1 << 21)
    cmp_ = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "difference").json()
    d = cmp_["differenceImage"]
    assert d["stretch"]["lowValue"] == -d["stretch"]["highValue"] and any("not a detection" in w for w in cmp_["warnings"])
    png = Image.open(io.BytesIO(c.get(d["imageUrl"]).content))
    assert png.mode == "LA"
    lum = np.array(png)[..., 0].astype(float)
    alpha = np.array(png)[..., 1]
    assert alpha.min() == 0 and alpha.max() == 255                 # no-data margins are transparent, not "no change"
    from processing.imaging import GridSpec, DEFAULT_FOV_ARCMIN
    g = GridSpec(*CENTER, DEFAULT_FOV_ARCMIN)
    new_x, new_y = g.sky_to_raster(*moved)
    old_x, old_y = g.sky_to_raster(*STAR)
    py, px = np.unravel_index(np.argmax(np.where(alpha > 0, lum, -1)), lum.shape)
    ny, nx = np.unravel_index(np.argmin(np.where(alpha > 0, lum, 999)), lum.shape)
    assert np.hypot(px - new_x, py - new_y) < 5 and np.hypot(nx - old_x, ny - old_y) < 5     # bright where it is now, dark where it was


def test_comparison_input_errors(env):
    c, _, _ = env
    _, obs = _observations(c)
    a = obs[0]["observationId"]
    assert _cmp(c, a, a, "blink").status_code == 400
    assert _cmp(c, a, "obs_nope", "blink").status_code == 404
    assert c.post("/api/v1/comparisons", json={"baseObservationId": a, "targetObservationId": obs[1]["observationId"], "mode": "bogus"}).status_code == 400
    assert c.get("/api/v1/comparisons/cmp_nope").status_code == 404


def test_comparison_download_failure_is_reported_and_not_stored(env):
    c, cut, _ = env
    _, obs = _observations(c)
    cut.fail = irsa.IrsaError("archive busy", retryable=True)
    r = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "blink").json()
    assert r["availability"]["status"] == "failed" and r["compatibility"]["verdict"] == "unknown"
    cut.fail = None                                                # a retry now succeeds, so the failure was not cached
    assert _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "blink").json()["availability"]["status"] == "available"


def test_comparison_warns_when_the_object_sits_at_different_wavelengths(env, tmp_path):
    """Same bandpass, but the second pointing places the object 400 detector pixels further along the band."""
    c, cut, _ = env
    _, obs = _observations(c)
    uris = list(URIS)
    cut.files[uris[1]] = make_cutout_fits(tmp_path / "shifted.fits", *STAR, rotation_deg=60.0, mjd=60990.2, detector=3,
                                          flag_value=(1 << 21), wave_shift_x=400.0)
    cmp_ = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "blink").json()
    wl = next(k for k in cmp_["compatibility"]["checks"] if k["name"] == "wavelength")
    assert wl["result"] == "warn" and "different wavelengths" in wl["detail"]
    assert any("different wavelengths" in w for w in cmp_["warnings"])


def test_candidates_endpoint_is_honestly_empty_until_detection_exists(env):
    c, _, _ = env
    oid = _object(c)["objectId"]
    page = c.get(f"/api/v1/objects/{oid}/candidates").json()
    assert page["items"] == [] and page["total"] == 0 and "not available yet" in page["coverageNote"]
    assert c.get("/api/v1/objects/obj_unknown/candidates").status_code == 404
