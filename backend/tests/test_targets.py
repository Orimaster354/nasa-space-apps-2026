"""The curated challenge targets: every name the UI and the challenge use resolves offline, with sourced proper motion, and the image
field is centred where the object is expected during the survey (not at the 26-year-old catalogue position)."""
import math

import pytest

from services import targets
from services.core import NotFound
from tests.test_api import env                                                       # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic


@pytest.mark.parametrize("query, expected", [
    ("Barnard's Star", "Barnard's Star"), ("barnard's star", "Barnard's Star"), ("Barnards Star", "Barnard's Star"),
    ("BARNARD’S STAR", "Barnard's Star"), ("GJ 699", "Barnard's Star"), ("  Barnard star ", "Barnard's Star"),
    ("WISE 0855-0714", "WISE 0855-0714"), ("WISE 0855", "WISE 0855-0714"), ("wise0855", "WISE 0855-0714"),
    ("WISE J085510.83-071442.5", "WISE 0855-0714"),
    ("RW Aurigae", "RW Aurigae"), ("rw aur", "RW Aurigae"), ("RW Aur A", "RW Aurigae"),
    ("R Draconis", "R Draconis"), ("r dra", "R Draconis"),
    ("Epsilon Indi B", "Epsilon Indi B"), ("eps Ind B", "Epsilon Indi B"), ("Epsilon Indi Ba", "Epsilon Indi B"),
])
def test_every_name_the_ui_and_challenge_use_is_found(query, expected):
    assert targets.find(query).name == expected


@pytest.mark.parametrize("query", ["", "   ", "Barnard", "R", "Epsilon Indi", "WISE", "Vega", "RW", "Draconis", "699"])
def test_ambiguous_or_unknown_names_are_not_guessed(query):
    assert targets.find(query) is None
    assert targets.find(None) is None


def test_the_table_is_complete_and_unambiguous():
    assert [t.name for t in targets.TARGETS] == ["Barnard's Star", "WISE 0855-0714", "RW Aurigae", "R Draconis", "Epsilon Indi B"]
    seen = {}
    for t in targets.TARGETS:
        for n in (t.name, *t.aliases):
            key = targets.normalize(n)
            assert seen.setdefault(key, t.name) == t.name, f"{n!r} belongs to two targets"


def test_proper_motion_carries_the_position_forward_in_the_right_direction():
    b = targets.find("Barnard's Star")
    ra, dec = targets.position_at(b, 2026.0)
    assert (dec - b.dec) * 3600 == pytest.approx(10.362394 * 26.0, rel=1e-6)                # 269 arcsec north
    assert (ra - b.ra) * 3600 * math.cos(math.radians(b.dec)) == pytest.approx(-0.801551 * 26.0, rel=1e-2)   # a little west
    assert targets.position_at(b, 2000.0) == (b.ra, b.dec)
    w = targets.find("WISE 0855")
    wra, wdec = targets.view_center(w)
    assert wra < w.ra and wdec > w.dec                                                      # moves west and slightly north
    assert (w.ra - wra) * 3600 * math.cos(math.radians(w.dec)) == pytest.approx(8.1237 * 26.0, rel=0.01)


def test_wise_0855_agrees_with_the_challenge_text_coordinate_at_its_own_epoch():
    """The challenge gives (133.795129, -7.245147). If that is the position at epoch 2010.4, our table plus proper motion must land on it."""
    w = targets.find("WISE 0855")
    ra, dec = targets.position_at(w, 2010.4)
    assert (ra - 133.795129) * 3600 * math.cos(math.radians(dec)) == pytest.approx(0.0, abs=1.5)
    assert (dec - -7.245147) * 3600 == pytest.approx(0.0, abs=1.5)


def test_slow_movers_barely_shift():
    for name in ("RW Aurigae", "R Draconis"):
        t = targets.find(name)
        ra, dec = targets.view_center(t)
        assert math.hypot((ra - t.ra) * math.cos(math.radians(t.dec)), dec - t.dec) * 3600 < 1.0


def test_proper_motion_model_states_its_source_and_convention():
    pm = targets.proper_motion_model(targets.find("Barnard's Star"))
    assert pm["provenanceKind"] == "catalog" and pm["unit"] == "arcsec/yr" and pm["value"] == pytest.approx(10.393, abs=0.001)
    assert pm["referenceEpoch"] == "J2000" and "cos(dec)" in pm["convention"] and "2026-11-15" in pm["source"]
    assert pm["pmRaMasPerYr"] == -801.551 and pm["pmDecMasPerYr"] == 10362.394


# ---- through the API ---------------------------------------------------------------------------------------------------
def test_resolving_a_target_works_without_the_name_resolver_and_returns_proper_motion(env):
    c, *_ = env
    for q, name in (("WISE 0855-0714", "WISE 0855-0714"), ("barnards star", "Barnard's Star"), ("eps Ind B", "Epsilon Indi B")):
        r = c.get("/api/v1/objects/resolve", params={"q": q})          # the fake resolver raises NotFound for these, so the table did it
        assert r.status_code == 200, q
        o = r.json()
        assert o["name"] == name and o["resolver"]["name"] == "Curated demo target" and o["objectType"]
        assert o["properMotion"]["provenanceKind"] == "catalog" and o["properMotion"]["unit"] == "arcsec/yr"
        assert "viewCenter" not in o                                  # internal only
        assert c.get(f"/api/v1/objects/{o['objectId']}").json()["name"] == name
    search = c.get("/api/v1/objects/search", params={"q": "RW Aurigae"}).json()
    assert search["total"] == 1 and search["items"][0]["name"] == "RW Aurigae"


def test_object_ids_are_stable_so_existing_caches_stay_valid(env):
    c, *_ = env
    a = c.get("/api/v1/objects/resolve", params={"q": "Barnard's star"}).json()["objectId"]
    b = c.get("/api/v1/objects/resolve", params={"q": "GJ 699"}).json()["objectId"]
    assert a == b == "obj_a62a69bc32"                               # the id from the pre-warm run, derived from the catalogue position


def test_images_are_centred_on_the_expected_position_not_the_catalogue_position(env):
    c, *_ = env
    o = c.get("/api/v1/objects/resolve", params={"q": "Barnard's Star"}).json()
    page = c.get(f"/api/v1/objects/{o['objectId']}/observations").json()
    assert page["total"] == 3                                       # the fake archive returns its files for any position
    img = c.get(f"/api/v1/observations/{page['items'][0]['observationId']}/image").json()
    vra, vdec = targets.view_center(targets.find("Barnard's Star"))
    assert img["wcs"]["crval"] == pytest.approx([vra, vdec])
    assert img["wcs"]["crval"][1] - o["coordinates"]["decDeg"] == pytest.approx(10.362394 * 26.0 / 3600, rel=1e-6)
    # an explicit centre from the client still wins
    other = c.get(f"/api/v1/observations/{page['items'][0]['observationId']}/image", params={"centerRa": 269.0, "centerDec": 4.5}).json()
    assert other["wcs"]["crval"] == pytest.approx([269.0, 4.5])


def test_names_that_are_not_curated_still_use_the_resolver(env):
    c, *_ = env
    assert c.get("/api/v1/objects/resolve", params={"q": "Synthetic Test Field"}).json()["resolver"]["name"] == "CDS Sesame via astropy"
    r = c.get("/api/v1/objects/resolve", params={"q": "definitely not a place"})
    assert r.status_code == 404
