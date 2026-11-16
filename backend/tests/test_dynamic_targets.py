"""Proper motion for any named object (SIMBAD via Sesame), and targets added at run time that behave like the curated ones."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from services import pm_lookup, spectrum, targets
from services.core import Services
from store.registry import Registry
from tests.conftest import CENTER
from tests.test_api import FakeCutouts, STAR, URIS, _file

pytestmark = pytest.mark.synthetic

# A real Sesame answer for Proxima Centauri, read on 2026-11-15 (comments and the long spectral-type line trimmed).
PROXIMA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<Target option="SNV">
  <name>Proxima Centauri</name>
  <Resolver name="S=Simbad, IDIA (via url)"><!--delay: 0ms [0] -->
    <INFO>from cache</INFO>
    <oid>3379714</oid>
    <oname>NAME Proxima Centauri</oname>
    <otype>PM*</otype>
    <jpos>14:29:42.94 -62:40:46.1</jpos>
    <jradeg>217.42894222160578</jradeg>
    <jdedeg>-62.67949018907555</jdedeg>
    <refPos>2020yCat.1350....0G</refPos>
    <errRAmas>0.0240</errRAmas><errDEmas>0.0344</errDEmas>
    <pm>
      <v>3859.228</v><e>0.060</e><q>A</q><r>2020yCat.1350....0G</r>
      <pa>281.501</pa>
      <pmRA>-3781.741</pmRA><epmRA>0.031</epmRA>
      <pmDE>769.465</pmDE><epmDE>0.051</epmDE>
    </pm>
    <plx><v>768.0665</v><e>0.0499</e><q>A</q><r>2020yCat.1350....0G</r></plx>
    <spType>M5.5Ve</spType>
  </Resolver>
</Target>
</Sesame>"""

NO_PM_XML = """<Sesame><Target><Resolver name="S=Simbad"><oname>M  87</oname><otype>AGN</otype><jradeg>187.7059</jradeg><jdedeg>12.3911</jdedeg><plx></plx></Resolver></Target></Sesame>"""
UNKNOWN_XML = """<Sesame><Target><Resolver name="S=Simbad"><INFO>nothing</INFO></Resolver></Target></Sesame>"""


# ------------------------------------------------------------------------------------------------ the lookup
def test_a_real_sesame_answer_is_parsed_with_motion_in_milliarcseconds_per_year():
    d = pm_lookup.parse(PROXIMA_XML)
    assert d["ra"] == pytest.approx(217.42894222) and d["dec"] == pytest.approx(-62.67949019)
    assert d["pmra"] == pytest.approx(-3781.741) and d["pmdec"] == pytest.approx(769.465)
    assert d["oname"] == "NAME Proxima Centauri" and d["otype"] == "PM*" and d["objectType"] == "High proper-motion star" and d["refPos"] == "2020yCat.1350....0G"


def test_an_object_without_a_listed_motion_still_gives_its_position():
    d = pm_lookup.parse(NO_PM_XML)
    assert d["pmra"] is None and d["pmdec"] is None and d["ra"] == pytest.approx(187.7059) and d["objectType"] == "Active galaxy"


def test_an_answer_with_no_position_is_none():
    assert pm_lookup.parse(UNKNOWN_XML) is None and pm_lookup.parse("<html>502</html>") is None


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_the_second_mirror_is_used_when_the_first_is_down_and_the_name_is_url_encoded():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(503) if "cds.unistra.fr" in str(request.url) else httpx.Response(200, text=PROXIMA_XML)

    d = pm_lookup.fetch_sesame("Teegarden's Star", client=_client(handler))
    assert d["pmra"] == pytest.approx(-3781.741)
    assert "Teegarden%27s%20Star" in seen[0] and "vizier.cfa.harvard.edu" in seen[1]


def test_failures_are_reported_with_a_reason():
    with pytest.raises(pm_lookup.PmLookupError, match="answered 500"):
        pm_lookup.fetch_sesame("x", client=_client(lambda r: httpx.Response(500)))
    with pytest.raises(pm_lookup.PmLookupError, match="could not be reached"):
        def boom(request):
            raise httpx.ConnectError("no network")
        pm_lookup.fetch_sesame("x", client=_client(boom))
    with pytest.raises(pm_lookup.PmLookupError, match="does not know this name"):
        pm_lookup.fetch_sesame("x", client=_client(lambda r: httpx.Response(200, text=UNKNOWN_XML)))


# ----------------------------------------------------------------------------------------- dynamic targets
def make(tmp_path, pm_fetch, name="r.sqlite"):
    cut = FakeCutouts(tmp_path)
    files = [_file(u, s) for u, s in URIS.items()]
    svc = Services(Registry(tmp_path / name), cut, tmp_path / "images", discover=lambda ra, dec: files, resolver=lambda q: CENTER, pm_fetch=pm_fetch)
    return TestClient(create_app(svc)), svc


def moving(name):
    return {"ra": CENTER[0], "dec": CENTER[1], "pmra": 3000.0, "pmdec": -2000.0, "oname": "NAME " + name, "otype": "PM*", "objectType": "High proper-motion star", "refPos": "x", "source": "s"}


def test_a_searched_name_with_a_proper_motion_is_centred_where_it_is_during_the_survey(tmp_path):
    c, svc = make(tmp_path, lambda n: moving(n))
    o = c.get("/api/v1/objects/resolve", params={"q": "Some Fast Star"}).json()
    assert o["properMotion"]["provenanceKind"] == "catalog" and o["properMotion"]["pmRaMasPerYr"] == 3000.0 and "retrieved" in o["properMotion"]["source"]
    assert o["resolver"]["name"] == "CDS Sesame (SIMBAD data)" and o["objectType"] == "High proper-motion star"
    stored = svc.registry.get_object(o["objectId"])
    t = targets.find("Some Fast Star")
    assert t is not None and stored["viewCenter"]["decDeg"] == pytest.approx(CENTER[1] + (-2000.0 / 3.6e6) * 26.0, abs=1e-6)       # 26 years of motion since J2000
    assert stored["viewCenter"]["decDeg"] != stored["coordinates"]["decDeg"]


def test_measurements_follow_the_motion_for_a_searched_target(tmp_path):
    c, svc = make(tmp_path, lambda n: moving(n))
    o = c.get("/api/v1/objects/resolve", params={"q": "Another Fast Star"}).json()
    ra, dec = spectrum.position_for(svc.registry.get_object(o["objectId"]), 2026.0, None, None)
    assert dec - CENTER[1] == pytest.approx(-2000.0 / 3.6e6 * 26.0, abs=1e-6) and ra != CENTER[0]


def test_a_target_without_a_motion_stays_a_plain_object_and_remembers_why(tmp_path):
    c, svc = make(tmp_path, lambda n: {**moving(n), "pmra": None, "pmdec": None})
    o = c.get("/api/v1/objects/resolve", params={"q": "Static Thing"}).json()
    assert "properMotion" not in o and targets.find("Static Thing") is None
    assert "no proper motion" in svc.registry.get_object(o["objectId"])["pmLookup"]["reason"]
    from services import candidates
    found, info = candidates.build(svc, o, lambda: "now")
    assert found == [] and "none could be obtained for this object: SIMBAD knows this object but lists no proper motion" in info["note"]


def test_a_failed_lookup_never_stops_the_search_and_its_reason_is_shown(tmp_path):
    def down(name):
        raise pm_lookup.PmLookupError("The CDS name service could not be reached (ConnectError).")
    c, svc = make(tmp_path, down)
    r = c.get("/api/v1/objects/resolve", params={"q": "Whatever Star"})
    assert r.status_code == 200 and "properMotion" not in r.json()
    import time
    for _ in range(100):                                                          # the search runs in the background; the first answer is 'looking for candidates'
        page = c.get(f"/api/v1/objects/{r.json()['objectId']}/candidates").json()
        if "Looking for candidates" not in page.get("coverageNote", ""):
            break
        time.sleep(0.1)
    assert page["items"] == [] and "could not be reached" in page["coverageNote"]
    boom = make(tmp_path, lambda n: 1 / 0, name="b.sqlite")[0].get("/api/v1/objects/resolve", params={"q": "Crashy Star"})
    assert boom.status_code == 200                                                # an unexpected error in the lookup is swallowed too


def test_without_a_lookup_function_nothing_is_fetched(tmp_path):
    c, svc = make(tmp_path, None)
    o = c.get("/api/v1/objects/resolve", params={"q": "Offline Star"}).json()
    assert "properMotion" not in o


def test_a_searched_target_is_restored_after_a_restart(tmp_path):
    c, svc = make(tmp_path, lambda n: moving(n))
    c.get("/api/v1/objects/resolve", params={"q": "Restart Star"})
    targets._DYNAMIC.clear()                                                      # a new process starts with none
    assert targets.find("Restart Star") is None
    make(tmp_path, None)                                                          # new Services on the same data directory
    t = targets.find("Restart Star")
    assert t is not None and t.pmra_mas_yr == 3000.0 and t.pmdec_mas_yr == -2000.0


def test_a_curated_name_is_never_looked_up(tmp_path):
    calls = []
    c, svc = make(tmp_path, lambda n: calls.append(n) or moving(n))
    c.get("/api/v1/objects/resolve", params={"q": "Barnard's Star"})
    assert calls == []
