"""Slow archive downloads: the request waits up to a limit, then answers 202 'processing' while the job continues; the same GET
later returns the finished image. Identical requests share one job; a failed job is reported, never stuck on 'processing'."""
import threading
import time

import pytest

from processing import irsa
from tests.test_api import _observations, env                                   # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic


def _url(c, obs, **params):
    return f"/api/v1/observations/{obs}/image", params


def _first(c):
    _, obs = _observations(c)
    return obs[0]["observationId"]


def _poll(c, obs, seconds=10.0):
    t = time.time()
    while time.time() - t < seconds:
        r = c.get(f"/api/v1/observations/{obs}/image")
        if r.json()["availability"]["status"] != "processing":
            return r
        time.sleep(0.05)
    raise AssertionError("still processing after the time limit")


def test_slow_download_returns_202_processing_then_the_finished_image(env):
    c, cut, _ = env
    c.app.state.services.image_wait_s = 0.15
    obs = _first(c)
    cut.gate = threading.Event()
    r1 = c.get(f"/api/v1/observations/{obs}/image")
    assert r1.status_code == 202
    a = r1.json()["availability"]
    assert a["status"] == "processing" and a["retryAfterSeconds"] == 3 and "imageUrl" not in r1.json()
    r2 = c.get(f"/api/v1/observations/{obs}/image")
    assert r2.status_code == 202 and cut.calls == 1                      # the second request joined the running job
    cut.gate.set()
    done = _poll(c, obs)
    body = done.json()
    assert done.status_code == 200 and body["availability"] == {"status": "available"} and body["width"] == body["height"] > 0
    assert c.get(body["imageUrl"]).status_code == 200
    assert cut.calls == 1                                                 # one download for the whole sequence


def test_a_job_that_fails_in_the_background_is_reported_not_stuck(env):
    c, cut, _ = env
    c.app.state.services.image_wait_s = 0.15
    obs = _first(c)
    cut.gate, cut.fail = threading.Event(), irsa.IrsaError("archive busy", retryable=True)
    assert c.get(f"/api/v1/observations/{obs}/image").status_code == 202
    cut.gate.set()
    r = _poll(c, obs)
    a = r.json()["availability"]
    assert r.status_code == 200 and a["status"] == "failed" and a["retryAfterSeconds"] == 30 and "archive busy" in a["statusReason"]
    cut.fail = None                                                       # the archive recovers: asking again retries and succeeds
    assert _poll(c, obs).json()["availability"] == {"status": "available"}


def test_many_identical_requests_share_one_download(env):
    c, cut, _ = env
    c.app.state.services.image_wait_s = 10.0
    obs = _first(c)
    cut.gate = threading.Event()
    results = []

    def get():
        results.append(c.get(f"/api/v1/observations/{obs}/image").json()["availability"]["status"])

    threads = [threading.Thread(target=get) for _ in range(6)]
    [t.start() for t in threads]
    time.sleep(0.3)
    cut.gate.set()
    [t.join(timeout=15) for t in threads]
    assert results == ["available"] * 6 and cut.calls == 1


def test_a_comparison_waits_for_its_images_so_no_processing_stub_is_stored(env):
    c, cut, _ = env
    c.app.state.services.image_wait_s = 0.1                               # the per-request wait is short ...
    _, obs = _observations(c)
    cut.gate = threading.Event()
    threading.Timer(0.5, cut.gate.set).start()
    r = c.post("/api/v1/comparisons", json={"baseObservationId": obs[0]["observationId"], "targetObservationId": obs[1]["observationId"], "mode": "blink"})
    body = r.json()                                                       # ... but the comparison still waits and stores finished images
    assert r.status_code == 200 and body["baseImage"]["availability"] == {"status": "available"} and body["targetImage"]["availability"] == {"status": "available"}
    assert c.get(f"/api/v1/comparisons/{body['comparisonId']}").json()["baseImage"]["availability"]["status"] == "available"


def test_the_wavelength_at_the_object_appears_on_the_observation_after_its_image_is_made(env):
    c, _, _ = env
    obs = _first(c)
    assert "wavelengthAtObjectUm" not in c.get(f"/api/v1/observations/{obs}").json()
    c.get(f"/api/v1/observations/{obs}/image")
    o = c.get(f"/api/v1/observations/{obs}").json()
    assert o["wavelengthAtObjectUm"] == pytest.approx(2.7725, abs=0.01) and o["bandwidthAtObjectUm"] == pytest.approx(0.03)
    oid = o["objectIds"][0]
    listed = c.get(f"/api/v1/objects/{oid}/observations").json()["items"][0]
    assert listed["wavelengthAtObjectUm"] == o["wavelengthAtObjectUm"]    # survives re-listing
