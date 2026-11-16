"""P1-02, 04 to 08: candidates from synthetic frames of a curated target moving by its catalogue proper motion among static stars."""
import math
import re
import time

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import schemas
from app.main import create_app
from processing import irsa
from services import candidates, targets
from services.core import Services
from store.registry import Registry
from tests.conftest import make_cutout_fits
from tests.test_api import _file, _no_nulls, env                                       # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

MJD0 = 60800.0


class FieldCutouts:
    """A stand-in archive serving one prepared cutout per file URI. Everything is 'cached', so no downloads are attempted."""

    def __init__(self):
        self.files, self.calls = {}, 0

    def is_cached(self, url):
        return any(u in url for u in self.files)

    def get(self, url, **kw):
        self.calls += 1
        for uri, path in self.files.items():
            if uri in url:
                return path
        raise irsa.IrsaError("not found", retryable=False)


def _offset(ra, dec, dx_arcsec, dy_arcsec):
    return ra + dx_arcsec / 3600.0 / math.cos(math.radians(dec)), dec + dy_arcsec / 3600.0


def _year(mjd):
    return 2000.0 + (mjd - 51544.5) / 365.25


def build_field(tmp_path, name="Epsilon Indi B", days=280.0, frames=14, follow_pm=True, flux_of=lambda i: 150.0, noise=0.05, seed=3, jitter=1.5):
    """Services + client over `frames` synthetic D3 frames. The target follows its catalogue motion (or stays put); 8 static stars surround it."""
    t = targets.find(name)
    vra, vdec = targets.view_center(t)
    rng = np.random.default_rng(seed)
    static = [(*_offset(vra, vdec, dx, dy), 90.0 + 12 * k) for k, (dx, dy) in
              enumerate([(150, 60), (-170, 90), (60, -200), (-90, -150), (240, -40), (-250, -60), (30, 260), (-40, 180)])]
    cut, files, base_pos = FieldCutouts(), [], targets.position_at(t, _year(MJD0))
    for i in range(frames):
        mjd = MJD0 + days * i / (frames - 1)
        pos = targets.position_at(t, _year(mjd)) if follow_pm else base_pos
        jx, jy = rng.normal(0, jitter, 2)
        stars = [(*_offset(ra, dec, jx, jy), pk) for ra, dec, pk in static] + [(*_offset(pos[0], pos[1], jx, jy), flux_of(i))]
        uri = f"ibe/data/spherex/dr1/level2/f{i}/3/f{i}.fits"
        cut.files[uri] = make_cutout_fits(tmp_path / f"c{i}.fits", vra, vdec, n=146, scale_arcsec=6.15, mjd=mjd, detector=3, flag_value=0,
                                          center=(vra, vdec), wave_offset=-1.0, stars=stars, noise=noise, noise_seed=100 + i)
        files.append(_file(uri, dict(mjd=mjd, rotation=0.0, detector=3, band="SPHEREx-D3")))
    svc = Services(Registry(tmp_path / "r.sqlite"), cut, tmp_path / "images", discover=lambda ra, dec: files, resolver=lambda n: (_ for _ in ()).throw(KeyError(n)))
    c = TestClient(create_app(svc))
    oid = c.get("/api/v1/objects/resolve", params={"q": name}).json()["objectId"]
    return svc, c, oid


# ---- the candidate itself -------------------------------------------------------------------------------------------------------
def test_a_moving_target_becomes_a_validated_candidate_with_evidence_a_track_and_a_citable_measurement(tmp_path):
    svc, c, oid = build_field(tmp_path)
    svc.compute_candidates(oid)
    page = c.get(f"/api/v1/objects/{oid}/candidates").json()
    assert page["total"] == 1 and "coverageNote" not in page
    cand = page["items"][0]
    m = schemas.Candidate.model_validate(cand)
    _no_nulls(cand)
    assert m.status == "candidate" and m.isProbability is False and m.kind == "positional_change" and 0 < m.score <= 1
    t = targets.find("Epsilon Indi B")
    assert cand["motion"]["value"] == pytest.approx(t.pm_total_arcsec_yr, abs=max(1.0, 3 * cand["motion"]["uncertainty"]))
    assert cand["motion"]["method"].startswith("Derived from observations")
    assert cand["properMotion"]["provenanceKind"] == "catalog" and cand["properMotion"]["catalogId"] == t.catalog_id          # catalogue kept apart (P1-06)
    assert any("south-east" in e or "east" in e for e in cand["evidence"]) and any("catalogue gives" in e for e in cand["evidence"])
    assert len(cand["observationIds"]) == len(cand["track"]) == 12 and [x["observationId"] for x in cand["track"]] == cand["observationIds"]
    for x in cand["track"]:
        assert 0 <= x["pixel"]["x"] < 300 and 0 <= x["pixel"]["y"] < 300 and -90 < x["sky"]["decDeg"] < 90
    assert all(abs(x["pixel"]["x"] - 150) < 8 and abs(x["pixel"]["y"] - 150) < 8 for x in cand["track"])    # near the field centre, where it is expected
    ref = cand["evidenceRefs"][0]
    assert ref["targetType"] == "measurement"
    meas = c.get(f"/api/v1/measurements/{ref['targetId']}").json()
    schemas.Measurement.model_validate(meas)
    assert meas["kind"] == "derived_motion" and meas["unit"] == "arcsec/yr" and meas["value"] == pytest.approx(cand["motion"]["value"], abs=1e-3)
    assert c.get(f"/api/v1/candidates/{cand['candidateId']}").json() == cand
    assert c.get("/api/v1/candidates/cand_nope").status_code == 404
    again = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]["candidateId"]
    svc.compute_candidates(oid)
    assert c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]["candidateId"] == again == cand["candidateId"]   # stable id


def test_without_pointing_jitter_the_track_drifts_east_and_south_on_the_raster(tmp_path):
    """East is left and south is down on the image (raster origin top-left). Epsilon Indi B moves east and south, so x falls and y rises."""
    svc, c, oid = build_field(tmp_path, jitter=0.0)
    svc.compute_candidates(oid)
    track = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]["track"]
    xs, ys = [p["pixel"]["x"] for p in track], [p["pixel"]["y"] for p in track]
    assert xs[-1] - xs[0] == pytest.approx(-0.77 * 3.98 / 2.0, abs=0.4)                             # about 1.5 px to the left over 0.77 years (2 arcsec per pixel)
    assert ys[-1] - ys[0] == pytest.approx(0.77 * 2.47 / 2.0, abs=0.4)                              # about 1 px down
    assert all(b <= a + 0.3 for a, b in zip(xs, xs[1:])) and all(b >= a - 0.3 for a, b in zip(ys, ys[1:]))      # steadily, not randomly


def test_nothing_in_a_candidate_is_called_a_discovery_a_confirmation_or_a_probability(tmp_path):
    svc, c, oid = build_field(tmp_path)
    svc.compute_candidates(oid)
    cand = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]

    def strings(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for x in v.values():
                yield from strings(x)
        elif isinstance(v, list):
            for x in v:
                yield from strings(x)

    text = " ".join(strings({k: v for k, v in cand.items() if k != "scoreBasis"}))
    assert not re.search(r"confirm|discover|proven|proves|probab|confidence|certain", text, re.I)
    assert "not a probability" in cand["scoreBasis"] and cand["isProbability"] is False        # the one place the word appears is to deny it
    assert any("not an established finding" in x for x in cand["limitations"])


def test_a_target_that_does_not_move_gets_no_candidate_and_an_honest_note(tmp_path):
    svc, c, oid = build_field(tmp_path, follow_pm=False)
    svc.compute_candidates(oid)
    page = c.get(f"/api/v1/objects/{oid}/candidates").json()
    assert page["items"] == [] and page["total"] == 0
    assert "Nothing stands out" in page["coverageNote"] and "no significant motion" in page["coverageNote"] and "steady" in page["coverageNote"]


def test_a_brightness_change_at_matching_wavelength_is_a_brightness_candidate(tmp_path):
    svc, c, oid = build_field(tmp_path, follow_pm=False, flux_of=lambda i: 150.0 if i < 7 else 400.0)
    svc.compute_candidates(oid)
    cand = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]
    assert cand["kind"] == "brightness_change" and "motion" not in cand
    assert any("Brightness differs between same-wavelength frames" in e for e in cand["evidence"])
    assert cand["score"] > 0 and not cand.get("evidenceRefs")


def test_a_target_that_moves_and_brightens_is_both(tmp_path):
    svc, c, oid = build_field(tmp_path, flux_of=lambda i: 150.0 if i < 7 else 400.0)
    svc.compute_candidates(oid)
    assert c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]["kind"] == "both"


def test_motion_that_fails_validation_is_listed_as_an_unvalidated_displacement_not_a_motion(tmp_path):
    svc, c, oid = build_field(tmp_path, name="Barnard's Star", days=70.0, frames=14)           # a fast mover, but only about 0.2 years of baseline
    svc.compute_candidates(oid)
    cand = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]
    assert cand["motion"]["method"].startswith("Unvalidated displacement") and "not a measured motion" in cand["motion"]["method"]
    assert any("Unvalidated displacement, not a measured motion" in w and "time baseline" in w for w in cand["warnings"])
    assert "has not passed the checks" in cand["description"]
    meas = c.get(f"/api/v1/measurements/{cand['evidenceRefs'][0]['targetId']}").json()
    assert any("Unvalidated displacement" in w for w in meas["warnings"]) and meas["method"].startswith("Derived from observations")


def test_an_object_that_is_not_a_catalogue_target_gets_no_seeded_candidates(env):
    c, *_ = env
    oid = c.get("/api/v1/objects/resolve", params={"q": "Synthetic Test Field"}).json()["objectId"]
    c.app.state.services.compute_candidates(oid)
    page = c.get(f"/api/v1/objects/{oid}/candidates").json()
    assert page["items"] == [] and "seeded from a known proper motion" in page["coverageNote"]


def test_too_few_usable_frames_is_reported_not_guessed(tmp_path):
    svc, c, oid = build_field(tmp_path, frames=5)
    svc.compute_candidates(oid)
    note = c.get(f"/api/v1/objects/{oid}/candidates").json()["coverageNote"]
    assert "too few" in note and "5" in note


# ---- listing, paging, background search ---------------------------------------------------------------------------------------
def test_the_listing_filters_pages_and_validates(tmp_path):
    svc, c, oid = build_field(tmp_path)
    svc.compute_candidates(oid)
    base = f"/api/v1/objects/{oid}/candidates"
    assert c.get(base, params={"minScore": 0.99}).json()["items"] == [] and c.get(base, params={"minScore": 0.01}).json()["total"] == 1
    assert c.get(base, params={"sort": "score"}).json()["total"] == 1
    assert c.get(base, params={"limit": 1, "offset": 1}).json()["items"] == []
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"minScore": 2}, {"sort": "name"}):
        r = c.get(base, params=bad)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST", bad
    assert c.get("/api/v1/objects/obj_nope/candidates").status_code == 404


def test_the_first_request_starts_a_background_search_and_a_later_one_returns_the_result(tmp_path):
    svc, c, oid = build_field(tmp_path)
    first = c.get(f"/api/v1/objects/{oid}/candidates").json()
    assert first["items"] == [] and "Looking for candidates" in first["coverageNote"]            # not ready yet, and we say so
    t = time.time()
    while time.time() - t < 30:
        page = c.get(f"/api/v1/objects/{oid}/candidates").json()
        if page["items"]:
            break
        time.sleep(0.2)
    assert page["total"] == 1 and "coverageNote" not in page


def test_an_incomplete_search_is_repeated_after_a_while_but_a_complete_one_is_kept(tmp_path):
    svc, c, oid = build_field(tmp_path)
    svc.registry.put_candidates(oid, [], {"computedAt": "2026-11-15T00:00:00Z", "note": "partial run", "partial": True, "candidateIds": []})
    svc.candidate_partial_ttl_s = 10 ** 9
    assert "partial run" in c.get(f"/api/v1/objects/{oid}/candidates").json()["coverageNote"]       # a recent incomplete run is reused
    svc.candidate_partial_ttl_s = 0
    first = c.get(f"/api/v1/objects/{oid}/candidates").json()
    assert "Looking for candidates" in first["coverageNote"]                                           # an old one triggers a fresh search
    svc.registry.put_candidates(oid, [], {"computedAt": "2026-11-15T00:00:00Z", "note": "complete run", "partial": False, "candidateIds": []})
    assert "complete run" in c.get(f"/api/v1/objects/{oid}/candidates").json()["coverageNote"]       # a complete one is kept whatever the ttl


# ---- scoring and brightness pairs (P1-08) ---------------------------------------------------------------------------------
GOOD = {"n": 0, "max_abs_z": 0.0, "median_abs_z": 0.0, "significant": False}
BRIGHT = {"n": 6, "max_abs_z": 8.0, "median_abs_z": 6.0, "significant": True}


def test_the_score_follows_the_stated_criteria():
    s = candidates.score
    assert s(10, True, GOOD, 1.0) == 1.0 and s(5, True, GOOD, 1.0) == 0.5 and s(0, True, GOOD, 1.0) == 0.0
    assert s(5, True, GOOD, 1.0) > s(3, True, GOOD, 1.0)                                              # stronger motion ranks higher
    assert s(8, True, GOOD, 1.0) > s(8, False, GOOD, 1.0) == pytest.approx(s(8, True, GOOD, 1.0) / 2)  # an unvalidated motion counts half
    assert s(5, True, GOOD, 0.4) == pytest.approx(0.25) and s(5, True, GOOD, 1.0) == 0.5               # poor frame coverage lowers it, but never below half
    assert s(None, False, BRIGHT, 1.0) == 1.0 and s(5, True, BRIGHT, 1.0) == 1.0                      # brightness term, and the bonus for both, are capped
    for args in [(0, True, GOOD, 0), (50, True, BRIGHT, 5), (3, False, GOOD, 0.1), (None, False, GOOD, 1.0)]:
        assert 0.0 <= s(*args) <= 1.0
    assert "not a probability" in candidates.SCORE_BASIS and "probab" not in candidates.SCORE_BASIS.replace("not a probability", "")


def test_brightness_pairs_use_only_matching_wavelengths_and_enough_time_between_frames():
    def pt(i, wl, flux, days=0.0, sigma=0.2):
        return {"key": f"k{i}", "mjd": MJD0 + days, "flux": flux, "sigma": sigma, "wl": wl, "bw": 0.04}

    steady = [pt(i, 1.80, 30.0 + 0.1 * i, days=100 * i) for i in range(5)]
    r = candidates.brightness_pairs(steady)
    assert r["n"] == 10 and not r["significant"] and r["max_abs_z"] < 1.0
    varying = [pt(i, 1.80, 30.0 if i < 3 else 45.0, days=100 * i) for i in range(6)]
    assert candidates.brightness_pairs(varying)["significant"]
    different_wavelengths = [pt(i, 1.60 + 0.03 * i, 30.0 if i % 2 else 60.0, days=100 * i) for i in range(6)]
    assert candidates.brightness_pairs(different_wavelengths)["n"] == 0                              # 0.03 apart is wider than a quarter of 0.04
    same_week = [pt(i, 1.80, 30.0 + 20 * i, days=i) for i in range(5)]
    assert candidates.brightness_pairs(same_week)["n"] == 0                                          # frames days apart are not compared
    two = candidates.brightness_pairs([pt(0, 1.80, 30.0, days=0), pt(9, 1.80, 45.0, days=900)])
    assert two["n"] == 1 and two["significant"] is False                                              # one pair is too few to say anything


def test_a_flux_change_that_is_only_the_systematic_floor_is_not_significant():
    """The floor (3 percent per frame) stops two high-signal-to-noise frames that agree to about 3 percent from looking like a change."""
    from processing import photometry
    pts = [{"key": "a", "mjd": MJD0, "flux": 30.0, "sigma": 0.2, "wl": 1.7, "bw": 0.04}, {"key": "b", "mjd": MJD0 + 100, "flux": 31.0, "sigma": 0.2, "wl": 1.7, "bw": 0.04},
           {"key": "c", "mjd": MJD0 + 200, "flux": 29.2, "sigma": 0.2, "wl": 1.7, "bw": 0.04}]
    r = candidates.brightness_pairs(pts)
    assert r["n"] == 3 and r["max_abs_z"] < 2.0 and photometry.SYSTEMATIC_FRACTION == 0.03


# ---- the companion --------------------------------------------------------------------------------------------------------------
def test_the_companion_is_told_about_the_candidate_and_never_gets_discovery_language(tmp_path):
    from ai import facts
    svc, c, oid = build_field(tmp_path)
    svc.compute_candidates(oid)
    cand = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]
    pack = facts.build(svc, {"objectId": oid, "candidateId": cand["candidateId"], "mode": "beginner"})
    f = next(x for x in pack.facts if x.target_type == "candidate")
    assert f.target_id == cand["candidateId"] and cand["label"] in f.text and "ranking heuristic" in f.text and "not a probability" in f.text
    assert f.plain and "worth a closer look" in f.plain and any(abs(n - cand["motion"]["value"]) < 0.01 for n in f.numbers)
    assert not re.search(r"confirm|discover|proven|proves", f.text + f.plain, re.I)
    assert any("not an established finding" in x for x in [f.text])


# ---- the guided investigation (P1-09) ---------------------------------------------------------------------------------------------
def test_the_guided_investigation_is_built_from_the_real_candidate(tmp_path):
    svc, c, oid = build_field(tmp_path, jitter=0.0)
    svc.compute_candidates(oid)
    cand = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]
    r = c.get("/api/v1/guided/spot-the-shift")
    assert r.status_code == 200
    g = r.json()
    assert g["guidedId"] == "spot-the-shift" and g["objectId"] == oid and g["answerCandidateId"] == cand["candidateId"]
    assert [s["id"] for s in g["steps"]] == ["browse", "find", "confirm"]                            # the steps the UI's card already runs
    assert g["tolerancePx"] == 14 and g["title"] == "Spot the shift"
    find = g["steps"][1]
    assert len(find["hints"]) == 3 and cand["label"] in find["correct"] and find["incorrect"] and find["prompt"].startswith("Click the object")
    hint3 = find["hints"][2]
    assert "to the left" in hint3 and "to the right" not in hint3 and "upward" not in hint3          # east is left, south is down: never the opposite way
    assert re.search(r"about \d+ arcseconds", hint3) and "zoom in" in hint3                             # honest that the shift is small
    assert "middle of the field" in find["hints"][1]
    text = " ".join([g["intro"], g["explanation"]] + [str(v) for st in g["steps"] for v in st.values() if isinstance(v, str)])
    assert "illustrative" not in text.lower() and not re.search(r"confirmed|discover(y|ed)|proves|proven", text, re.I)
    assert "not established findings" in g["explanation"] and f"{cand['motion']['value']:.1f}" in g["explanation"]
    assert c.get(f"/api/v1/guided/spot-the-shift?objectId={oid}").json()["objectId"] == oid
    assert c.get("/api/v1/guided/other").status_code == 404


def test_the_guided_click_target_matches_the_candidates_track(tmp_path):
    """The UI accepts a click within tolerancePx of any track point. Clicking the star must pass; clicking a far-off spot must not."""
    svc, c, oid = build_field(tmp_path, jitter=0.0)
    svc.compute_candidates(oid)
    g = c.get("/api/v1/guided/spot-the-shift").json()
    cand = c.get(f"/api/v1/objects/{oid}/candidates").json()["items"][0]
    hit = lambda x, y: any(math.hypot(t["pixel"]["x"] - x, t["pixel"]["y"] - y) <= g["tolerancePx"] for t in cand["track"])
    assert hit(150, 150) and hit(cand["track"][0]["pixel"]["x"] + 5, cand["track"][0]["pixel"]["y"])
    assert not hit(40, 40) and not hit(260, 250) and not hit(150, 150 + 40)


def test_without_any_candidate_there_is_no_guided_investigation_to_offer(tmp_path):
    svc, c, oid = build_field(tmp_path, follow_pm=False)                                              # a target that does not move
    svc.compute_candidates(oid)
    r = c.get("/api/v1/guided/spot-the-shift")
    assert r.status_code == 404 and "No candidate with a measured shift" in r.json()["error"]["message"]
    assert c.get("/api/v1/guided/spot-the-shift?objectId=obj_nope").status_code == 404
