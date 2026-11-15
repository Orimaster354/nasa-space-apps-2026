"""P2-08: catalogue overlays: conversion, proper-motion propagation, placement on the image grid, caching, failure handling (injected rows)."""
import pytest

from app.config import settings
from processing import imaging
from services import catalog
from tests.test_api import _object, env                                              # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "catalog_overlay", True)


def _rows(ra, dec):
    return [
        {"id": "G1", "ra": ra, "dec": dec, "pmra": 0.0, "pmdec": 0.0, "plx": 10.0, "mag": 12.5},                       # at the field centre, not moving
        {"id": "G2", "ra": ra, "dec": dec, "pmra": 0.0, "pmdec": 1000.0, "plx": 50.0, "mag": 9.0},                     # moves 10 arcsec north in 10 years
        {"id": "G3", "ra": ra + 5.0, "dec": dec, "pmra": 1.0, "pmdec": 1.0, "plx": None, "mag": 15.0},                 # far outside the picture
        {"id": "G4", "ra": ra + 0.01, "dec": dec + 0.01, "pmra": None, "pmdec": None, "plx": None, "mag": None},       # no proper motion, no magnitude
    ]


def _center(c, oid):
    o = c.get(f"/api/v1/objects/{oid}").json()
    v = o.get("viewCenter") or o["coordinates"]
    return v["raDeg"], v["decDeg"]


def test_sources_are_placed_on_the_image_grid_carried_to_the_epoch_and_labelled(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    ra, dec = _center(c, oid)
    c.app.state.services.catalog_fetch = lambda source, r, d, rad, lim: _rows(ra, dec)
    j = c.get(f"/api/v1/objects/{oid}/catalog", params={"source": "gaia", "fovArcmin": 10, "epoch": 2026.0}).json()
    assert j["catalogName"] == "Gaia DR3" and j["provenance"]["kind"] == "catalog" and j["provenance"]["table"] == "I/355/gaiadr3"
    assert j["provenance"]["catalogEpoch"] == 2016.0 and j["provenance"]["positionsAtEpoch"] == 2026.0
    by = {s["id"]: s for s in j["sources"]}
    assert set(by) == {"G1", "G2", "G4"}                                       # G3 is outside the picture
    g = imaging.GridSpec(ra, dec, 10)
    x1, y1 = g.sky_to_raster(ra, dec)
    assert by["G1"]["raster"]["x"] == pytest.approx(x1, abs=0.01) and by["G1"]["raster"]["y"] == pytest.approx(y1, abs=0.01)
    assert by["G2"]["sky"]["decDeg"] - dec == pytest.approx(10.0 / 3600.0, rel=1e-3)                   # 1000 mas/yr over 10 years
    assert by["G2"]["raster"]["y"] < by["G1"]["raster"]["y"] - 4.0                                   # north is up: it moved up the picture (about 5 pixels)
    assert by["G4"]["sky"]["raDeg"] == pytest.approx(ra + 0.01, abs=1e-6) and "magnitude" not in by["G4"] and "properMotion" not in by["G4"]
    assert by["G1"]["magnitude"] == 12.5 and by["G1"]["parallaxMas"] == 10.0
    assert j["grid"]["width"] == g.npix and any("not from SPHEREx" in w for w in j["warnings"])


def test_a_fetched_catalogue_is_stored_and_reused_when_the_service_is_down(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    ra, dec = _center(c, oid)
    svc = c.app.state.services
    calls = []
    svc.catalog_fetch = lambda *a: calls.append(1) or _rows(ra, dec)
    first = c.get(f"/api/v1/objects/{oid}/catalog").json()
    c.get(f"/api/v1/objects/{oid}/catalog")
    assert calls == [1]                                                          # the second request came from the stored copy

    def down(*a):
        raise catalog.CatalogUnavailable("down")
    svc.catalog_fetch = down
    assert c.get(f"/api/v1/objects/{oid}/catalog").json()["count"] == first["count"]


def test_service_down_and_nothing_stored_is_a_clear_retryable_error(env):
    c, *_ = env
    oid = _object(c)["objectId"]

    def down(*a):
        raise catalog.CatalogUnavailable("The Gaia DR3 catalogue service did not answer (TimeoutError).")
    c.app.state.services.catalog_fetch = down
    r = c.get(f"/api/v1/objects/{oid}/catalog", params={"source": "allwise"})
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True and "nothing is shown rather than a guess" in r.json()["error"]["message"]


def test_bad_parameters_and_unknown_objects_are_rejected(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    assert c.get(f"/api/v1/objects/{oid}/catalog", params={"source": "simbad"}).status_code == 400
    assert c.get(f"/api/v1/objects/{oid}/catalog", params={"fovArcmin": 31}).status_code == 400
    assert c.get(f"/api/v1/objects/{oid}/catalog", params={"epoch": 1500}).status_code == 400
    assert c.get("/api/v1/objects/obj_nope/catalog").status_code == 404


def test_proper_motion_helper_follows_the_catalogue_convention():
    ra, dec = catalog.propagate(100.0, 60.0, 1000.0, 0.0, 2016.0, 2026.0)             # pmRA already includes cos(dec)
    assert ra - 100.0 == pytest.approx(10.0 / 3600.0 / 0.5, rel=1e-3) and dec == pytest.approx(60.0)
    assert catalog.propagate(100.0, 60.0, None, 5.0, 2016.0, 2026.0) == (100.0, 60.0)


def test_the_overlay_is_off_by_default_and_says_why(env, monkeypatch):
    monkeypatch.setattr(settings, "catalog_overlay", False)
    c, *_ = env
    oid = _object(c)["objectId"]
    r = c.get(f"/api/v1/objects/{oid}/catalog")
    assert r.status_code == 503 and r.json()["error"]["retryable"] is False and "SPX_CATALOG_OVERLAY" in r.json()["error"]["message"]
