"""P1-14: the band selected on the Spectrum page reaches the AI as its own fact; P1-15: field-of-view limits."""
import pytest

from ai import facts
from services import spectrum as spectrum_mod
from tests.test_api import env, _object                                           # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic


def _spectrum(oid):
    pts = [
        {"wavelengthUm": 1.20, "wavelengthMinUm": 1.18, "wavelengthMaxUm": 1.22, "bandIndex": 20, "available": True, "flux": 31.5, "fluxUncertainty": 0.8,
         "qualityFlags": [], "observationId": "obs_a", "observedAt": "2026-03-02T10:00:00Z"},
        {"wavelengthUm": 1.25, "wavelengthMinUm": 1.22, "wavelengthMaxUm": 1.28, "bandIndex": 21, "available": True, "flux": 28.25, "fluxUncertainty": 0.7,
         "qualityFlags": [], "observationId": "obs_b", "observedAt": "2026-03-09T10:00:00Z"},
        {"wavelengthUm": 1.31, "wavelengthMinUm": 1.28, "wavelengthMaxUm": 1.34, "bandIndex": 22, "available": False, "unavailableReason": "No observation reached this band.",
         "qualityFlags": []},
        {"wavelengthUm": 1.38, "bandIndex": 23, "available": True, "flux": 20.5, "fluxUncertainty": 0.6, "qualityFlags": [], "observationId": "obs_c", "observedAt": "2026-04-01T10:00:00Z"},
    ]
    sid = spectrum_mod.spectrum_id(oid, None, None, spectrum_mod.DEFAULT_RADIUS_ARCSEC)
    return sid, {"spectrumId": sid, "objectId": oid, "availability": {"status": "available"}, "fluxUnit": "mJy", "apertureRadiusArcsec": 12.0,
                 "extractionMethod": "test", "warnings": [], "points": pts}


@pytest.fixture
def stored(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    svc = c.app.state.services
    sid, body = _spectrum(oid)
    svc.registry.put_spectrum(sid, {"result": body, "savedAt": "2026-11-15T00:00:00Z", "partial": False})
    return svc, oid, sid


def band_fact(svc, ctx):
    return next((f for f in facts.build(svc, ctx).facts if f.text.startswith("SELECTED BAND")), None)


def test_a_selected_band_becomes_its_own_fact_with_neighbours(stored):
    svc, oid, sid = stored
    f = band_fact(svc, {"objectId": oid, "bandIndex": 21, "activeView": "spectrum"})          # no spectrumId: found from the object
    assert f is not None and f.target_type == "spectrum" and f.target_id == sid
    assert "band 21" in f.text.lower() and "1.250 micrometres" in f.text and "28.25" in f.text and "0.7" in f.text
    assert "previous band (20" in f.text and "31.5" in f.text and "next band (22" not in f.text      # band 22 has no measurement, so it is not quoted
    assert 28.25 in f.numbers and 31.5 in f.numbers


def test_different_bands_give_different_facts(stored):
    svc, oid, _ = stored
    a = band_fact(svc, {"objectId": oid, "bandIndex": 20})
    b = band_fact(svc, {"objectId": oid, "bandIndex": 23})
    assert a.text != b.text and "31.5" in a.text and "20.5" in b.text and "20.5" not in a.text.replace("20", "")


def test_a_band_without_data_says_why_and_never_borrows_a_neighbour(stored):
    svc, oid, _ = stored
    f = band_fact(svc, {"objectId": oid, "bandIndex": 22})
    assert "NO measurement" in f.text and "No observation reached this band." in f.text
    assert "28.25" not in f.text and "20.5" not in f.text


def test_a_band_that_is_not_in_the_spectrum_is_reported_as_such(stored):
    svc, oid, _ = stored
    f = [x for x in facts.build(svc, {"objectId": oid, "bandIndex": 99}).facts if "Band 99" in x.text]
    assert f and "not part of this object's stored spectrum" in f[0].text


def test_no_stored_spectrum_means_no_band_fact_and_no_crash(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    pack = facts.build(c.app.state.services, {"objectId": oid, "bandIndex": 5})
    assert not any(f.text.startswith("SELECTED BAND") for f in pack.facts)


def test_chat_context_accepts_a_band_and_rejects_nonsense(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    ok = c.post("/api/v1/chat/messages", json={"message": "what is this band?", "context": {"objectId": oid, "bandIndex": 21, "activeView": "spectrum"}})
    assert ok.status_code == 200
    bad = c.post("/api/v1/chat/messages", json={"message": "x", "context": {"bandIndex": 500}})
    assert bad.status_code == 400


def test_the_prompt_rule_tells_the_model_how_to_use_a_selected_band():
    from ai import companion
    assert "SELECTED BAND" in companion.SYSTEM_PROMPT and "never fill the gap" in companion.SYSTEM_PROMPT


def test_fov_limit_is_enforced_with_a_clear_message(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    first = c.get(f"/api/v1/objects/{oid}/observations").json()["items"][0]["observationId"]
    r = c.get(f"/api/v1/observations/{first}/image", params={"fovArcmin": 30.5})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST" and "30" in r.json()["error"]["message"]
    r0 = c.get(f"/api/v1/observations/{first}/image", params={"fovArcmin": 0})
    assert r0.status_code == 400
