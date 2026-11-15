import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from processing import irsa
from processing.archive_status import ArchiveStatus
from tests.test_api import env                                           # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_unknown_until_something_is_known_and_no_probe_without_opt_in():
    s = ArchiveStatus(probe=None)
    assert s.snapshot()["status"] == "unknown"


def test_real_traffic_sets_the_status_and_the_newest_word_wins():
    s = ArchiveStatus()
    s.note(False)
    snap = s.snapshot()
    assert snap["status"] == "down" and "not answering" in snap["message"] and snap["checkedAt"]
    s.note(True)
    assert s.snapshot()["status"] == "up"


def test_stale_status_starts_one_background_probe_that_never_blocks():
    import threading
    gate, calls = threading.Event(), []

    def probe():
        calls.append(1)
        gate.wait(5)
        return False

    clock = Clock()
    s = ArchiveStatus(probe=probe, ttl_s=30, clock=clock)
    first = s.snapshot()                       # returns at once although the probe is still running
    assert first["status"] == "unknown"
    s.snapshot(); s.snapshot()
    assert len(calls) <= 1                     # only one probe in flight
    gate.set()
    for _ in range(100):
        if s.snapshot()["status"] == "down":
            break
        import time; time.sleep(0.02)
    assert s.snapshot()["status"] == "down"


def test_a_probe_that_raises_counts_as_down():
    s = ArchiveStatus(probe=lambda: 1 / 0)
    s.snapshot()
    for _ in range(100):
        if s.snapshot()["status"] != "unknown":
            break
        import time; time.sleep(0.02)
    assert s.snapshot()["status"] == "down"


def test_fresh_status_is_not_probed_again():
    calls = []
    clock = Clock()
    s = ArchiveStatus(probe=lambda: calls.append(1) or True, ttl_s=30, clock=clock)
    s.note(True)
    clock.t = 10
    s.snapshot()
    assert calls == []
    clock.t = 31
    s.snapshot()
    import time; time.sleep(0.2)
    assert calls == [1]


def test_endpoint_exists_and_leaves_health_unchanged():
    c = TestClient(create_app())
    assert c.get("/api/v1/health").json() == {"status": "ok", "contractVersion": "0.2.0"}
    r = c.get("/api/v1/archive-status")
    assert r.status_code == 200 and r.json()["status"] in ("up", "down", "unknown")


def test_a_failed_search_marks_the_archive_down(monkeypatch):
    import httpx
    from processing import archive_status as mod

    fresh = ArchiveStatus()
    monkeypatch.setattr(irsa, "archive", fresh)
    monkeypatch.setattr(irsa, "SIA_BACKOFF_S", 0.0)

    class R:
        status_code = 502
        content = b""

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    with pytest.raises(irsa.IrsaError):
        irsa._default_fetch("spherex_dr1", 10.0, 10.0)
    assert fresh.snapshot()["status"] == "down"


# ---------------------------------------------------------------------------------------------- fail fast
def _breaker(clock):
    s = ArchiveStatus(probe=lambda: True, clock=clock)
    s.enable_probe(lambda: True)
    return s


def test_fail_fast_is_off_unless_switched_on():
    s = ArchiveStatus()
    s.note(False)
    assert s.should_try() is True


def test_while_down_requests_fail_fast_and_one_trial_goes_through_every_15_seconds():
    clock = Clock()
    s = _breaker(clock)
    assert s.should_try() is True                        # unknown: go ahead
    s.note(False)
    assert s.should_try() is False                       # just failed: answer at once
    clock.t = 14.9
    assert s.should_try() is False
    clock.t = 15.0
    assert s.should_try() is True                        # one trial
    assert s.should_try() is False                       # ... only one while it is in flight
    s.note(False)                                        # the trial failed: wait another 15 s
    clock.t = 29.0
    assert s.should_try() is False
    clock.t = 30.0
    assert s.should_try() is True
    s.note(True)                                         # the trial worked
    assert s.should_try() is True and s.should_try() is True and s.snapshot()["status"] == "up"


def test_a_trial_that_never_reports_back_does_not_block_forever():
    clock = Clock()
    s = _breaker(clock)
    s.note(False)
    clock.t = 15.0
    assert s.should_try() is True
    clock.t = 44.0
    assert s.should_try() is False                       # still within the trial timeout (30 s)
    clock.t = 45.0
    assert s.should_try() is True


def test_a_position_that_is_not_stored_fails_instantly_while_the_archive_is_down(env, monkeypatch):
    from services import core
    from tests.test_api import _object
    c, cut, state = env
    clock = Clock()
    s = _breaker(clock)
    monkeypatch.setattr(core, "archive", s)
    calls = []
    real = state["files"]
    svc = c.app.state.services
    svc._discover = lambda ra, dec: calls.append(1) or real
    oid = _object(c)["objectId"]
    s.note(False)
    r = c.get(f"/api/v1/objects/{oid}/observations")
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True and "not answering right now" in r.json()["error"]["message"]
    assert calls == []                                   # the archive was not even asked
    clock.t = 16.0                                       # time for a trial: it goes through, succeeds, and everything flows again
    ok = c.get(f"/api/v1/objects/{oid}/observations")
    assert ok.status_code == 200 and calls == [1]


def test_a_saved_listing_is_still_served_while_the_archive_is_down(env, monkeypatch):
    from services import core
    from tests.test_api import _object
    c, cut, state = env
    clock = Clock()
    s = _breaker(clock)
    monkeypatch.setattr(core, "archive", s)
    oid = _object(c)["objectId"]
    assert c.get(f"/api/v1/objects/{oid}/observations").status_code == 200       # saves the listing
    svc = c.app.state.services
    svc.snapshot_max_age_s = 0.0                                                    # force the 'archive is asked' branch
    svc._disc_cache.clear()
    s.note(False)
    r = c.get(f"/api/v1/objects/{oid}/observations")
    assert r.status_code == 200 and "last saved listing" in (r.json().get("coverageNote") or "")
