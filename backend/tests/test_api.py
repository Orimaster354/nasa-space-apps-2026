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
