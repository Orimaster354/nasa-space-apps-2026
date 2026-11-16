"""A successful search for a target that is not preloaded adds it to the preloaded set, with the same treatment the shipped targets got."""
import logging

import pytest

from app.config import settings
from services import targets
from services.promotion import Promoter
from tests.test_dynamic_targets import moving
from tests.test_spectrum_epochs import _env
from tests.test_api import _object

pytestmark = pytest.mark.synthetic

FRAMES = [(60800.7 + 12 * k, 100.0, 0.0) for k in range(9)]


def setup(tmp_path, **kw):
    c, cut, svc = _env(tmp_path, FRAMES)
    svc._pm_fetch = lambda n: moving(n)
    svc.promoter = Promoter(svc, min_d3=kw.pop("min_d3", 3), **kw)
    return c, cut, svc


def search(c, name="Searched Fast Star"):
    o = c.get("/api/v1/objects/resolve", params={"q": name}).json()
    c.get(f"/api/v1/objects/{o['objectId']}/observations", params={"limit": 5})            # the visitor's search retrieves the list
    return o


def test_a_successful_search_adds_the_target_with_pictures_spectrum_and_a_candidate_attempt(tmp_path, caplog):
    c, cut, svc = setup(tmp_path)
    with caplog.at_level(logging.INFO, logger="spx.promotion"):
        o = search(c)
        svc.promoter.wait(o["objectId"])
    body = svc.registry.get_object(o["objectId"])
    assert body["promotedAt"] and body["promotion"]["ok"] is True and body["promotion"]["frames"] == 9
    # pictures: every default-band frame has a rendered picture
    items = svc.list_observations(o["objectId"], bandpass="SPHEREx-D3", limit=200)["items"]
    assert len(items) == 9 and all(svc.get_image(i["observationId"], wait_s=1)["availability"]["status"] == "available" for i in items)
    # spectrum stored (served without rebuilding), candidate search recorded with its note
    assert svc.get_spectrum(o["objectId"], wait_s=1)["availability"]["status"] in ("available", "unavailable")
    run = svc.registry.get_candidate_run(o["objectId"])
    assert run is not None and "note" in run
    assert body["promotion"]["spectrum"] in ("available", "unavailable")
    # the visitor-facing trail
    assert any("PRELOAD_ADDED name=Searched Fast Star" in r.message for r in caplog.records)
    lib = c.get("/api/v1/library", params={"fresh": True}).json()
    item = next(i for i in lib["items"] if i["objectId"] == o["objectId"])
    assert item["origin"] == "added from a search" and item["addedAt"] and item["curated"] is False and item["defaultBandStored"] == 9


def test_the_motion_candidate_search_runs_because_a_proper_motion_was_found(tmp_path):
    c, cut, svc = setup(tmp_path)
    o = search(c)
    svc.promoter.wait(o["objectId"])
    assert targets.find("Searched Fast Star") is not None                           # the target behaves like a curated one
    note = svc.registry.get_candidate_run(o["objectId"])["note"] or ""
    assert "none could be obtained" not in note and "not one of the catalogue targets" not in note      # it was searched, not skipped for lack of a motion


def test_without_a_proper_motion_the_target_is_still_added_and_the_reason_is_recorded(tmp_path):
    c, cut, svc = setup(tmp_path)
    svc._pm_fetch = lambda n: {**moving(n), "pmra": None, "pmdec": None}
    o = search(c, "Static Galaxy Thing")
    svc.promoter.wait(o["objectId"])
    assert svc.registry.get_object(o["objectId"])["promotion"]["ok"] is True
    assert "lists no proper motion" in (svc.registry.get_candidate_run(o["objectId"])["note"] or "")


def test_a_target_is_added_only_once_and_survives_being_resolved_again(tmp_path):
    c, cut, svc = setup(tmp_path)
    o = search(c)
    svc.promoter.wait(o["objectId"])
    first = svc.registry.get_object(o["objectId"])["promotedAt"]
    assert svc.promoter.consider(o["objectId"], 99, True) is False                 # already added
    c.get("/api/v1/objects/resolve", params={"q": "Searched Fast Star"})            # a later visitor resolves it again
    assert svc.registry.get_object(o["objectId"])["promotedAt"] == first           # the bookkeeping is kept


def test_too_few_observations_or_an_incomplete_listing_are_not_added(tmp_path):
    c, cut, svc = setup(tmp_path, min_d3=50)
    o = search(c)
    assert svc.promoter.consider(o["objectId"], 9, True) is False and "promotedAt" not in svc.registry.get_object(o["objectId"])
    svc.promoter.min_d3 = 3
    assert svc.promoter.consider(o["objectId"], 9, False) is False                  # the archive answered only in part


def test_curated_targets_are_never_added(tmp_path):
    c, cut, svc = setup(tmp_path)
    o = c.get("/api/v1/objects/resolve", params={"q": "Barnard's Star"}).json()
    assert svc.promoter.consider(o["objectId"], 99, True) is False


def test_the_daily_cap_and_the_storage_cap_hold(tmp_path, caplog):
    c, cut, svc = setup(tmp_path, per_day=1)
    first = search(c, "Star Number One")
    svc.promoter.wait(first["objectId"])
    other = c.get("/api/v1/objects/resolve", params={"q": "Star Number Two"}).json()
    with caplog.at_level(logging.INFO, logger="spx.promotion"):
        queued = svc.promoter.consider(other["objectId"], 9, True) if other["objectId"] != first["objectId"] else False
    assert queued is False
    svc.promoter.per_day = 99
    svc.promoter.max_data_mb = 0.0001                                               # pretend the disk is full
    assert svc.promoter.consider(other["objectId"], 9, True) is False or other["objectId"] == first["objectId"]


def test_a_failed_job_records_why_and_is_not_retried_for_an_hour(tmp_path, caplog):
    c, cut, svc = setup(tmp_path)
    cut.known = set()                                                                # the archive's picture service is down: nothing can be downloaded
    with caplog.at_level(logging.INFO, logger="spx.promotion"):
        o = search(c, "Failing Star")
        svc.promoter.wait(o["objectId"])
    body = svc.registry.get_object(o["objectId"])
    assert body["promotion"]["ok"] is False and body["promotion"]["reason"] and "promotedAt" not in body
    assert any("PRELOAD_FAILED name=Failing Star" in r.message for r in caplog.records)
    assert svc.promoter.consider(o["objectId"], 9, True) is False                    # not retried for an hour
    lib = c.get("/api/v1/library", params={"fresh": True}).json()
    assert all(i["objectId"] != o["objectId"] for i in lib["items"])                 # a failed target is not listed as preloaded


def test_it_is_off_unless_switched_on(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    c, cut, svc = _env(tmp_path, FRAMES)
    assert settings.auto_preload is False
    create_app(svc)
    assert getattr(svc, "promoter", None) is None


def test_the_spectrum_of_an_added_target_is_final_so_a_visit_never_rebuilds_it(tmp_path):
    """A refusal or a spectrum with frames the archive never delivered must still be stored as the final answer (previously such a result was
    marked partial, not stored, and every visit retried)."""
    c, cut, svc = setup(tmp_path)
    o = search(c)
    svc.promoter.wait(o["objectId"])
    from services import spectrum as sp
    rec = svc.registry.get_spectrum(sp.spectrum_id(o["objectId"], None, None, sp.DEFAULT_RADIUS_ARCSEC))
    assert rec is not None and rec["partial"] is False
    assert "spectrumReason" in svc.registry.get_object(o["objectId"])["promotion"]
