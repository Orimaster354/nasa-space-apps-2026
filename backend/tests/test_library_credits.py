"""The library (what is stored here) and the credits (P1-18)."""
import pytest

from tests.test_api import env, _object                                           # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

SPHEREX_ACK = ("This publication makes use of data products from the Spectro-Photometer for the History of the Universe, Epoch of Reionization and "
               "Ices Explorer (SPHEREx), which is a joint project of the Jet Propulsion Laboratory and the California Institute of Technology, and is "
               "funded by the National Aeronautics and Space Administration.")
IRSA_ACK = ("This research has made use of the NASA/IPAC Infrared Science Archive, which is funded by the National Aeronautics and Space "
            "Administration and operated by the California Institute of Technology.")


def test_credits_carry_irsas_acknowledgments_word_for_word(env):
    c, *_ = env
    j = c.get("/api/v1/credits").json()
    by = {a["id"]: a for a in j["acknowledgments"]}
    assert by["spherex"]["text"] == SPHEREX_ACK and by["irsa"]["text"] == IRSA_ACK            # copied from IRSA's pages, never paraphrased
    assert "SIMBAD" in by["simbad"]["text"] and "Astropy" in by["astropy"]["text"]
    assert j["retrievedOn"] == "2026-11-15"


def test_credits_list_dois_papers_and_the_wiseview_reference(env):
    c, *_ = env
    j = c.get("/api/v1/credits").json()
    assert {d["doi"] for d in j["datasets"]} == {"10.26131/IRSA678", "10.26131/IRSA662"}
    assert all(d["url"] == f"https://doi.org/{d['doi']}" for d in j["datasets"])
    assert len(j["papers"]) == 4 and all(p["url"].startswith("https://") for p in j["papers"])
    wv = next(r for r in j["resources"] if "WiseView" in r["label"])
    assert "No WiseView code is used" in wv["note"] and "not SPHEREx" in wv["note"]
    assert "not scientific findings" in j["aiNote"] and "not NASA observations" in j["dataNote"]


def test_the_library_is_empty_before_anything_is_stored_then_lists_what_is(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    first = c.get("/api/v1/library", params={"fresh": True}).json()
    assert first["items"] == [] and first["totals"]["targets"] == 0
    for o in c.get(f"/api/v1/objects/{oid}/observations").json()["items"]:
        c.get(f"/api/v1/observations/{o['observationId']}/image", params={"fovArcmin": 2})
    j = c.get("/api/v1/library", params={"fresh": True}).json()
    assert j["totals"]["targets"] == 1 and j["totals"]["storedFrames"] == 3
    it = j["items"][0]
    assert it["objectId"] == oid and it["storedFrames"] == 3 and it["observationsListed"] == 3 and it["worksOffline"] is True
    assert it["storedByBand"] == {"SPHEREx-D3": 2, "SPHEREx-D6": 1} and it["spectrum"] is None and it["candidates"] is None
    assert "keep working when NASA's archive is unreachable" in j["note"]


def test_the_library_reports_a_stored_spectrum_and_candidate_run(env):
    from services import spectrum as sp
    c, *_ = env
    oid = _object(c)["objectId"]
    svc = c.app.state.services
    for o in c.get(f"/api/v1/objects/{oid}/observations").json()["items"]:
        c.get(f"/api/v1/observations/{o['observationId']}/image", params={"fovArcmin": 2})
    sid = sp.spectrum_id(oid, None, None, sp.DEFAULT_RADIUS_ARCSEC)
    pts = [{"wavelengthUm": 1.2, "bandIndex": 20, "available": True, "flux": 3.0}, {"wavelengthUm": 3.8, "bandIndex": 80, "available": True, "flux": 1.0},
           {"wavelengthUm": 2.0, "bandIndex": 40, "available": False}]
    svc.registry.put_spectrum(sid, {"result": {"availability": {"status": "available"}, "points": pts}, "savedAt": "2026-11-15T00:00:00Z", "partial": False})
    svc.registry.put_candidates(oid, [], {"computedAt": "2026-11-15T00:00:00Z", "note": "Nothing stands out.", "partial": False, "candidateIds": []})
    it = c.get("/api/v1/library", params={"fresh": True}).json()["items"][0]
    assert it["spectrum"] == {"status": "available", "bands": 2, "fromUm": 1.2, "toUm": 3.8}
    assert it["candidates"] == {"count": 0, "note": "Nothing stands out."}


def test_the_library_is_cached_briefly_and_fresh_bypasses_the_cache(env):
    c, *_ = env
    a = c.get("/api/v1/library").json()
    oid = _object(c)["objectId"]
    for o in c.get(f"/api/v1/objects/{oid}/observations").json()["items"]:
        c.get(f"/api/v1/observations/{o['observationId']}/image", params={"fovArcmin": 2})
    assert c.get("/api/v1/library").json() == a                                      # still the cached answer
    assert c.get("/api/v1/library", params={"fresh": True}).json()["totals"]["storedFrames"] == 3
