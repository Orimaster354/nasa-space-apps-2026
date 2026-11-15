"""P1-19: stacking. Unit tests of the method on synthetic frames, then the service/API on stored synthetic cutouts."""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from processing import coadd, irsa
from services.core import Services
from store.registry import Registry
from tests.conftest import CENTER, make_cutout_fits
from tests.test_api import STAR, _file, _object

pytestmark = pytest.mark.synthetic


# ---------------------------------------------------------------------------------------------- the method
def _frames(n, sigma=1.0, seed=0, size=120, background=None):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    star = 40.0 * np.exp(-((xx - 60) ** 2 + (yy - 60) ** 2) / (2 * 2.0 ** 2))
    out = []
    for k in range(n):
        bg = (background[k] if background else 10.0)
        out.append(bg + star + rng.normal(0, sigma, (size, size)))
    return out


def test_stacking_independent_noise_improves_it_close_to_the_square_root_of_n():
    r = coadd.stack(_frames(9, sigma=1.0))
    assert r.frames_used == 9
    assert 2.4 < r.measured_gain < 3.4                       # ideal is 3
    assert r.ideal_gain == pytest.approx(3.0, rel=0.05)
    assert r.stack_sigma < r.single_frame_sigma / 2.4


def test_each_frames_own_sky_level_is_removed_and_the_star_is_kept():
    bgs = [5.0, 20.0, 12.0, 30.0, 8.0, 16.0]
    r = coadd.stack(_frames(6, sigma=0.5, seed=1, background=bgs))
    star_peak = r.image[60, 60] - np.median(r.image)
    assert 36 < star_peak < 44                                # the 40-unit star survives
    assert np.median(r.image) == pytest.approx(np.average(bgs, weights=None), abs=6.0)   # the pedestal is a weighted mean level, not a mess
    assert robust(r.image) < 0.5                              # backgrounds of 5..30 did not add structure


def robust(a):
    return coadd.robust_sigma(a - np.median(a))


def test_a_cosmic_ray_in_one_frame_is_clipped_but_a_real_source_is_not():
    frames = _frames(6, sigma=0.5, seed=2)
    frames[3][20, 20] += 500.0                                # one-frame hit
    r = coadd.stack(frames)
    assert r.image[20, 20] - np.median(r.image) < 5.0
    assert r.image[60, 60] - np.median(r.image) > 30.0
    assert r.clipped_fraction > 0


def test_a_noisy_frame_counts_for_less():
    good = _frames(4, sigma=0.5, seed=3)
    bad = _frames(1, sigma=8.0, seed=4)
    r = coadd.stack(good + bad)
    plain = coadd.stack(good)
    assert r.stack_sigma < plain.stack_sigma * 1.25           # the very noisy frame barely hurts


def test_needs_two_usable_frames_and_ignores_empty_ones():
    with pytest.raises(ValueError):
        coadd.stack(_frames(1))
    with pytest.raises(ValueError):
        coadd.stack(_frames(1) + [np.full((120, 120), np.nan)])
    assert coadd.stack(_frames(2) + [np.full((120, 120), np.nan)]).frames_used == 2


def test_wavelength_groups_pick_the_largest_matched_set_and_keep_listing_order():
    items = [("a", 1.640, 0.04), ("b", 1.645, 0.04), ("c", 2.00, 0.04), ("d", 1.648, 0.04), ("e", 2.30, 0.04), ("f", 1.90, 0.04)]
    assert coadd.best_wavelength_group(items, 0.25) == ["a", "b", "d"]
    assert coadd.best_wavelength_group([], 0.25) == []
    assert coadd.best_wavelength_group([("x", 1.0, 0.04), ("y", 1.5, 0.04)], 0.25) in (["x"], ["y"])


# ------------------------------------------------------------------------------------------- the service
def _stack_env(tmp_path, n_d3=6, same_wavelength=True, extra_far=0):
    specs = {}
    for k in range(n_d3):
        specs[f"ibe/data/spherex/dr1/level2/s{k}/3/f{k}.fits"] = dict(mjd=60800.7 + 20 * k, rotation=0.0, detector=3, band="SPHEREx-D3", seed=k,
                                                                      shift=0.0 if same_wavelength else 0.02 * k)
    for k in range(extra_far):
        specs[f"ibe/data/spherex/dr1/level2/far{k}/3/g{k}.fits"] = dict(mjd=60900.7 + k, rotation=0.0, detector=3, band="SPHEREx-D3", seed=50 + k, shift=0.5)

    class Cut:
        def __init__(self):
            self.files = {u: make_cutout_fits(tmp_path / f"c{i}.fits", *STAR, rotation_deg=s["rotation"], mjd=s["mjd"], detector=s["detector"], n=61,
                                              noise=1.0, noise_seed=s["seed"], wave_offset=s["shift"])
                          for i, (u, s) in enumerate(specs.items())}
            self.known = set(self.files)

        def is_cached(self, url):
            return any(u in url for u in self.known)

        def get(self, url, **kw):
            for u, p in self.files.items():
                if u in url:
                    return p
            raise irsa.IrsaError("not stored", retryable=False)

    cut = Cut()
    files = [_file(u, s) for u, s in specs.items()]
    svc = Services(Registry(tmp_path / "r.sqlite"), cut, tmp_path / "images", discover=lambda ra, dec: files, resolver=lambda name: CENTER)
    return TestClient(create_app(svc)), cut, svc


def _render_all(c, oid):
    for o in c.get(f"/api/v1/objects/{oid}/observations", params={"bandpass": "SPHEREx-D3", "limit": 200}).json()["items"]:
        assert c.get(f"/api/v1/observations/{o['observationId']}/image", params={"fovArcmin": 5}).json()["availability"]["status"] == "available"


def test_a_stack_is_labelled_derived_lists_its_inputs_and_improves_the_noise(tmp_path):
    c, cut, svc = _stack_env(tmp_path)
    oid = _object(c)["objectId"]
    _render_all(c, oid)
    r = c.get(f"/api/v1/objects/{oid}/coadd", params={"bandpass": "SPHEREx-D3", "fovArcmin": 5})
    assert r.status_code == 200
    j = r.json()
    assert j["kind"] == "derived_stack" and j["availability"]["status"] == "available" and j["coaddId"].startswith("coadd_")
    assert j["frameCount"] == 6 and len(j["inputObservationIds"]) == 6
    ids = {o["observationId"] for o in c.get(f"/api/v1/objects/{oid}/observations", params={"bandpass": "SPHEREx-D3", "limit": 200}).json()["items"]}
    assert set(j["inputObservationIds"]) == ids
    assert j["noise"]["measuredGain"] > 1.5 and j["noise"]["idealGain"] > 2.0 and j["noise"]["stack"] < j["noise"]["singleFrame"]
    assert any("derived" in w.lower() and "not a NASA observation" in w for w in j["warnings"])
    assert j["imageUrl"].startswith("/media/images/coadd_") and c.get(j["imageUrl"]).status_code == 200
    assert "matched in wavelength" in j["method"]
    again = c.get(f"/api/v1/objects/{oid}/coadd", params={"bandpass": "SPHEREx-D3", "fovArcmin": 5}).json()
    assert again["coaddId"] == j["coaddId"] and again["generatedAt"] == j["generatedAt"]          # stored, not rebuilt


def test_frames_at_a_different_wavelength_are_left_out_and_say_so(tmp_path):
    c, cut, svc = _stack_env(tmp_path, n_d3=5, extra_far=2)
    oid = _object(c)["objectId"]
    _render_all(c, oid)
    j = c.get(f"/api/v1/objects/{oid}/coadd", params={"fovArcmin": 5}).json()
    assert j["availability"]["status"] == "available" and j["frameCount"] == 5
    assert j["leftOut"]["different wavelength"] == 2 and any("different wavelength" in w for w in j["warnings"])


def test_not_enough_matching_frames_gives_an_honest_unavailable(tmp_path):
    c, cut, svc = _stack_env(tmp_path, n_d3=4, same_wavelength=False)             # each frame 0.02 um from the last: no two match
    oid = _object(c)["objectId"]
    _render_all(c, oid)
    j = c.get(f"/api/v1/objects/{oid}/coadd", params={"fovArcmin": 5}).json()
    assert j["availability"]["status"] == "unavailable" and "needs at least 2" in j["availability"]["statusReason"]
    assert "different wavelength" in j["availability"]["statusReason"]


def test_frames_not_stored_here_are_never_downloaded(tmp_path):
    c, cut, svc = _stack_env(tmp_path)
    oid = _object(c)["objectId"]
    _render_all(c, oid)
    cut.known = set()                                                              # nothing stored any more
    j = c.get(f"/api/v1/objects/{oid}/coadd", params={"fovArcmin": 5}).json()
    assert j["availability"]["status"] == "unavailable" and "not stored on this server" in j["availability"]["statusReason"]


def test_bad_parameters_are_rejected(tmp_path):
    c, *_ = _stack_env(tmp_path)
    oid = _object(c)["objectId"]
    assert c.get(f"/api/v1/objects/{oid}/coadd", params={"bandpass": "SPHEREx-D9"}).status_code == 400
    assert c.get(f"/api/v1/objects/{oid}/coadd", params={"maxFrames": 1}).status_code == 400
    assert c.get(f"/api/v1/objects/{oid}/coadd", params={"fovArcmin": 31}).status_code == 400
    assert c.get("/api/v1/objects/obj_nope/coadd").status_code == 404
