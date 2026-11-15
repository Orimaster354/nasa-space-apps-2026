import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from processing import irsa
from processing.archive_status import ArchiveStatus

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
