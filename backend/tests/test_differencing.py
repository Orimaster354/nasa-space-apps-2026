"""P2-02: automated source differencing: behaviour on synthetic pairs, and that the endpoint is off unless switched on."""
import numpy as np
import pytest

from app.config import settings
from processing import differencing as D
from tests.test_api import _cmp, _observations, env                                  # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

N = 200
YY, XX = np.mgrid[0:N, 0:N]


def star(x, y, peak, s=2.5):
    return peak * np.exp(-((XX - x) ** 2 + (YY - y) ** 2) / (2 * s ** 2))


def field(rng, stars, bg=5.0):
    img = bg + rng.normal(0, 1.0, (N, N))
    for x, y, p in stars:
        img = img + star(x, y, p)
    return img


STARS = [(40, 40, 300.0), (150, 60, 120.0), (60, 150, 250.0), (150, 150, 60.0)]


def test_noise_only_pairs_give_no_detections():
    rng = np.random.default_rng(1)
    assert sum(len(D.detect(field(rng, []), field(rng, [], bg=7.0)).detections) for _ in range(6)) == 0


def test_static_stars_with_calibration_and_pointing_scatter_are_gated_out_as_star_residuals():
    rng = np.random.default_rng(2)
    total = 0
    for _ in range(6):
        jit = rng.normal(0, 0.18, 2)
        b = field(rng, STARS)
        t = field(rng, [(x + jit[0], y + jit[1], p * rng.uniform(0.97, 1.03)) for x, y, p in STARS], bg=6.0)
        total += len(D.detect(b, t).detections)
    assert total == 0


def test_a_moved_source_a_flare_and_a_fading_star_are_found_with_the_right_kind():
    rng = np.random.default_rng(3)
    b = field(rng, STARS + [(100, 100, 120.0), (170, 30, 40.0), (30, 170, 150.0)])
    t = field(rng, STARS + [(108, 100, 120.0), (170, 30, 140.0), (30, 170, 40.0)])
    kinds = {(d.kind, round(d.x / 10), round(d.y / 10)) for d in D.detect(b, t).detections}
    assert ("moved", 10, 10) in kinds and ("brighter", 17, 3) in kinds and ("fainter", 3, 17) in kinds


def test_groups_touching_the_edge_or_missing_data_are_dropped():
    rng = np.random.default_rng(4)
    b = field(rng, STARS + [(100, 100, 120.0)])
    t = field(rng, STARS + [(100, 100, 220.0)])
    t[95:106, 95:106] = np.nan                                                  # missing data right on the source
    r = D.detect(b, t)
    assert not any(abs(d.x - 100) < 8 and abs(d.y - 100) < 8 for d in r.detections)


def test_too_little_data_returns_nothing_not_an_error():
    a = np.full((50, 50), np.nan)
    r = D.detect(a, a)
    assert r.detections == [] and np.isnan(r.noise)


def test_the_endpoint_is_off_by_default_and_says_why(env):
    c, cut, _ = env
    _, obs = _observations(c)
    cid = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "difference").json()["comparisonId"]
    j = c.get(f"/api/v1/comparisons/{cid}/sources").json()
    assert j["availability"]["status"] == "unavailable" and j["sources"] == []
    reason = j["availability"]["statusReason"]
    assert "switched off" in reason and "SPX_AUTO_DIFFERENCING" in reason and "star" in reason
    assert c.get("/api/v1/comparisons/cmp_nope/sources").status_code == 404


def test_when_enabled_the_endpoint_returns_flagged_leads_with_positions(env, monkeypatch):
    c, cut, _ = env
    monkeypatch.setattr(settings, "auto_differencing", True)
    _, obs = _observations(c)
    cid = _cmp(c, obs[0]["observationId"], obs[1]["observationId"], "difference").json()["comparisonId"]
    j = c.get(f"/api/v1/comparisons/{cid}/sources").json()
    assert j["availability"]["status"] == "available" and isinstance(j["sources"], list)
    assert any("not candidates and not findings" in x for x in j["limitations"]) and any("false positives" in x for x in j["limitations"])
    for s in j["sources"]:
        assert s["kind"] in ("moved", "brighter", "fainter") and 0 <= s["raster"]["x"] < 300 and 0 <= s["raster"]["y"] < 300
