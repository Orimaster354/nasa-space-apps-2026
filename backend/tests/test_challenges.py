"""P2-04: challenges are offered only when the evidence supports them, and their answers come from the numbers."""
import pytest

from services import spectrum as spectrum_mod
from tests.test_api import _object
from tests.test_spectrum_epochs import _env

pytestmark = pytest.mark.synthetic


def _render(c, oid):
    for o in c.get(f"/api/v1/objects/{oid}/observations", params={"bandpass": "SPHEREx-D3", "limit": 200}).json()["items"]:
        assert c.get(f"/api/v1/observations/{o['observationId']}/image", params={"fovArcmin": 5}).json()["availability"]["status"] == "available"


def _challenges(c, oid):
    r = c.get(f"/api/v1/objects/{oid}/challenges")
    assert r.status_code == 200
    return r.json()


def test_an_unchanged_matched_pair_gives_a_no_change_brightness_challenge_with_its_evidence(tmp_path):
    c, *_ = _env(tmp_path, [(60800.7, 100.0, 0.0), (60960.7, 100.0, 0.0), (61000.7, 100.0, 0.0)])
    oid = _object(c)["objectId"]
    _render(c, oid)
    j = _challenges(c, oid)
    ch = next(x for x in j["items"] if x["kind"] == "brightness_check")
    assert ch["illustrative"] is False and "same wavelength" in ch["prompt"]
    assert len(ch["evidence"]["observationIds"]) == 2 and ch["evidence"]["measurementIds"]
    assert "consistent with no change" in ch["answer"]["text"] and ch["answer"]["significant"] is False
    assert c.get(f"/api/v1/measurements/{ch['evidence']['measurementIds'][0]}").json()["kind"] == "flux_change"     # the evidence really exists


def test_a_large_change_is_called_a_lead_never_a_finding(tmp_path):
    c, *_ = _env(tmp_path, [(60800.7, 100.0, 0.0), (60960.7, 300.0, 0.0)])
    oid = _object(c)["objectId"]
    _render(c, oid)
    ch = next(x for x in _challenges(c, oid)["items"] if x["kind"] == "brightness_check")
    assert ch["answer"]["significant"] is True and "lead to check, not proof" in ch["answer"]["text"]
    assert "discover" not in ch["answer"]["text"].lower() and "confirm" not in ch["answer"]["text"].lower()


def test_frames_less_than_100_days_apart_or_at_different_wavelengths_give_no_brightness_challenge(tmp_path):
    c, *_ = _env(tmp_path, [(60800.7, 100.0, 0.0), (60820.7, 100.0, 0.0), (60960.7, 100.0, 300.0)])
    oid = _object(c)["objectId"]
    _render(c, oid)
    j = _challenges(c, oid)
    assert not any(x["kind"] == "brightness_check" for x in j["items"])
    assert j["total"] == 0 and "not enough evidence" in j["note"] and "100 days apart" in j["note"]


def test_a_spectrum_challenge_needs_a_stored_spectrum_with_enough_bands(tmp_path):
    c, cut, svc = _env(tmp_path, [(60800.7, 100.0, 0.0)])
    oid = _object(c)["objectId"]
    sid = spectrum_mod.spectrum_id(oid, None, None, spectrum_mod.DEFAULT_RADIUS_ARCSEC)
    few = [{"wavelengthUm": 1.0 + i / 10, "bandIndex": i + 1, "available": True, "flux": 10.0 - i} for i in range(3)]
    svc.registry.put_spectrum(sid, {"result": {"spectrumId": sid, "availability": {"status": "available"}, "points": few}, "savedAt": "2026-11-15T00:00:00Z", "partial": False})
    assert not any(x["kind"] == "spectrum_shape" for x in _challenges(c, oid)["items"])
    many = [{"wavelengthUm": 1.0 + i / 10, "bandIndex": i + 1, "available": i != 4, "flux": 50.0 - 4 * i} for i in range(12)]
    svc.registry.put_spectrum(sid, {"result": {"spectrumId": sid, "availability": {"status": "available"}, "points": many}, "savedAt": "2026-11-15T00:00:00Z", "partial": False})
    ch = next(x for x in _challenges(c, oid)["items"] if x["kind"] == "spectrum_shape")
    assert ch["evidence"] == {"spectrumId": sid} and ch["answer"]["brightestUm"] == 1.0 and "11 measured bands" in ch["answer"]["text"]     # the gap is not counted


def test_a_validated_candidate_gives_the_find_the_mover_challenge_and_points_to_the_guide(tmp_path):
    c, cut, svc = _env(tmp_path, [(60800.7, 100.0, 0.0)])
    oid = _object(c)["objectId"]
    cand = {"candidateId": "cand_x", "label": "SX-1", "objectId": oid, "kind": "positional_change", "score": 0.7,
            "motion": {"value": 4.5, "unit": "arcsec/yr"}, "track": [{}, {}, {}], "evidenceRefs": [{"targetType": "measurement", "targetId": "meas_1"}]}
    svc.registry.put_candidates(oid, [cand], {"computedAt": "2026-11-15T00:00:00Z", "note": "", "partial": False, "candidateIds": ["cand_x"]})
    ch = next(x for x in _challenges(c, oid)["items"] if x["kind"] == "find_the_mover")
    assert ch["evidence"] == {"candidateId": "cand_x", "measurementIds": ["meas_1"]} and "not a confirmed discovery" in ch["answer"]["text"]
    assert ch["activity"] == f"/api/v1/guided/spot-the-shift?objectId={oid}"


def test_unknown_object_is_404(tmp_path):
    c, *_ = _env(tmp_path, [(60800.7, 100.0, 0.0)])
    assert c.get("/api/v1/objects/obj_nope/challenges").status_code == 404
