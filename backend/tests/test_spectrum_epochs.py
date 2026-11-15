"""P2-03: spectrum before versus after a date, wavelength-gated, from stored frames only."""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from processing import irsa
from services.core import Services
from store.registry import Registry
from tests.conftest import CENTER, make_cutout_fits
from tests.test_api import STAR, _file, _object

pytestmark = pytest.mark.synthetic


def _env(tmp_path, frames):
    """frames: list of (mjd, peak, wave_shift_x). All D3, wavelength offset -1.0 so the object sits near 1.77 um when the shift is 0."""
    specs = {f"ibe/data/spherex/dr1/level2/e{i}/3/f{i}.fits": dict(mjd=m, rotation=0.0, detector=3, band="SPHEREx-D3", peak=p, shift=s)
             for i, (m, p, s) in enumerate(frames)}

    class Cut:
        def __init__(self):
            self.files = {u: make_cutout_fits(tmp_path / f"c{i}.fits", *STAR, rotation_deg=0.0, mjd=sp["mjd"], detector=3, flag_value=1 << 21, peak=sp["peak"],
                                              wave_shift_x=sp["shift"], wave_offset=-1.0, noise=0.0)
                          for i, (u, sp) in enumerate(specs.items())}
            self.known = set(self.files)

        def is_cached(self, url):
            return any(u in url for u in self.known)

        def get(self, url, **kw):
            for u, p in self.files.items():
                if u in url and u in self.known:
                    return p
            raise irsa.IrsaError("not stored", retryable=False)

    cut = Cut()
    files = [_file(u, s) for u, s in specs.items()]
    svc = Services(Registry(tmp_path / "r.sqlite"), cut, tmp_path / "images", discover=lambda ra, dec: files, resolver=lambda name: CENTER)
    return TestClient(create_app(svc)), cut, svc


def _cmp(c, oid, **p):
    return c.get(f"/api/v1/objects/{oid}/spectrum-comparison", params=p)


def test_an_unchanged_object_is_consistent_between_the_two_stretches(tmp_path):
    c, *_ = _env(tmp_path, [(60800.7 + 10 * k, 100.0, 0.0) for k in range(8)])
    oid = _object(c)["objectId"]
    r = _cmp(c, oid)
    assert r.status_code == 200
    j = r.json()
    assert j["availability"]["status"] == "available" and j["kind"] == "epoch_comparison"
    s = j["summary"]
    assert s["bandsCompared"] >= 1 and s["bandsDifferingBeyondSigma"] == 0 and "consistent" in s["text"]
    row = next(b for b in j["bands"] if b["comparable"])
    assert abs(row["significanceSigma"]) < 3 and row["ratio"] == pytest.approx(1.0, abs=0.02)
    assert row["early"]["observedAt"] < j["splitAt"] <= row["late"]["observedAt"]
    assert any("repeatability floor" in w for w in j["warnings"]) and any("not proof of variability" in w for w in j["warnings"])


def test_a_brighter_later_epoch_is_flagged_as_a_lead_not_a_finding(tmp_path):
    frames = [(60800.7 + 10 * k, 100.0, 0.0) for k in range(4)] + [(60900.7 + 10 * k, 200.0, 0.0) for k in range(4)]
    c, *_ = _env(tmp_path, frames)
    oid = _object(c)["objectId"]
    j = _cmp(c, oid).json()
    s = j["summary"]
    assert s["bandsDifferingBeyondSigma"] >= 1 and "not a finding" in s["text"] and s["medianRatio"] == pytest.approx(2.0, rel=0.15)
    assert "discover" not in s["text"].lower() and "confirm" not in s["text"].lower()


def test_bands_whose_wavelengths_do_not_match_are_not_compared(tmp_path):
    # early frames sit near 1.77 um; the late frames are shifted on the detector so the object lands at a clearly different wavelength
    frames = [(60800.7 + 10 * k, 100.0, 0.0) for k in range(4)] + [(60900.7 + 10 * k, 100.0, 300.0) for k in range(4)]
    c, *_ = _env(tmp_path, frames)
    oid = _object(c)["objectId"]
    j = _cmp(c, oid).json()
    assert j["availability"]["status"] == "unavailable" and "wavelength-matched" in j["availability"]["statusReason"]
    assert any(not b["comparable"] for b in j["bands"]) and not any(b["comparable"] for b in j["bands"])


def test_frames_not_stored_are_never_downloaded_and_few_frames_say_so(tmp_path):
    c, cut, svc = _env(tmp_path, [(60800.7 + 10 * k, 100.0, 0.0) for k in range(8)])
    oid = _object(c)["objectId"]
    cut.known = set()
    j = _cmp(c, oid).json()
    assert j["availability"]["status"] == "unavailable" and "not stored on this server" in j["availability"]["statusReason"]


def test_a_chosen_split_date_is_honoured_and_bad_input_is_rejected(tmp_path):
    c, *_ = _env(tmp_path, [(60800.7 + 10 * k, 100.0, 0.0) for k in range(8)])
    oid = _object(c)["objectId"]
    j = _cmp(c, oid, split="2025-06-01").json()
    assert j["splitAt"] == "2025-06-01"
    assert _cmp(c, oid, split="not a date").status_code == 400
    assert _cmp(c, oid, apertureRadiusArcsec=500).status_code == 400
    assert _cmp(c, "obj_nope").status_code == 404
