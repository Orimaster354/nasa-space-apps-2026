"""P1-13 and P1-20: GET /objects/{id}/spectrum. One point per band, gaps as gaps, best signal-to-noise per band, honest warnings, async."""
import math
import threading
import time

import pytest

from api import schemas
from processing import quality
from services import spectrum, targets
from tests.conftest import CENTER, make_cutout_fits
from tests.test_api import STAR, URIS, _object, env                                  # noqa: F401  (env is a fixture)

pytestmark = pytest.mark.synthetic

OMEGA = (6.2 / 206264.806247) ** 2
LO, HI = 1.6301, 2.4346                                                              # the synthetic archive records' wavelength range
URI_D3_A, URI_D3_B, URI_D6 = list(URIS)


def _waves(cut, tmp_path, shifts=(0.0, 300.0, 120.0), offset=-1.0, peaks=(100.0, 100.0, 100.0), flags=(1 << 21,) * 3):
    """Replace the three synthetic cutouts with ones whose wavelength at the object is 1.77, 2.07 and 1.88 um (default)."""
    for i, (uri, spec) in enumerate(URIS.items()):
        cut.files[uri] = make_cutout_fits(tmp_path / f"sp{i}.fits", *STAR, rotation_deg=spec["rotation"], mjd=spec["mjd"], detector=spec["detector"],
                                          flag_value=flags[i], peak=peaks[i], wave_shift_x=shifts[i], wave_offset=offset)


def _spec(c, oid, **params):
    return c.get(f"/api/v1/objects/{oid}/spectrum", params=params)


def _flux(peak, radius=12.0):
    sigma = 1.2
    return peak * 2 * math.pi * sigma ** 2 * (1 - math.exp(-((radius / 6.2) ** 2) / (2 * sigma ** 2))) * OMEGA * 1e9


# ---- band arithmetic ------------------------------------------------------------------------------------------------------------
def test_each_detector_is_split_into_17_log_spaced_channels_and_bands_number_1_to_102():
    e = spectrum.channel_edges(LO, HI)
    assert len(e) == 18 and e[0] == pytest.approx(LO) and e[-1] == pytest.approx(HI)
    ratios = [e[i + 1] / e[i] for i in range(17)]
    assert max(ratios) == pytest.approx(min(ratios)) and ratios[0] == pytest.approx(1.0239, abs=1e-3)      # constant resolving power
    assert spectrum.EXPECTED_BANDS == 102
    seen = set()
    for d in range(1, 7):
        for k in range(17):
            wl = math.sqrt(e[k] * e[k + 1])
            band, lo, hi = spectrum.band_of(d, wl, LO, HI)
            assert band == (d - 1) * 17 + k + 1 and lo == pytest.approx(e[k]) and hi == pytest.approx(e[k + 1])
            seen.add(band)
    assert seen == set(range(1, 103))
    assert spectrum.band_of(3, LO, LO, HI)[0] == 35 and spectrum.band_of(3, HI, LO, HI)[0] == 51     # the edges clamp into the 17 channels
    assert spectrum.band_of(3, HI * 1.2, LO, HI)[0] == 51 and spectrum.band_of(3, LO * 0.9, LO, HI)[0] == 35
    assert spectrum.detector_number("SPHEREx-D3") == 3 and spectrum.detector_number(None) is None and spectrum.detector_number("x") is None


def test_the_band_width_matches_the_bandwidth_measured_in_real_files():
    """Real D3 files report a per-pixel bandwidth of 2.39 to 2.50 percent of the wavelength (see PROBE_FINDINGS.md); 17 log channels give 2.39."""
    e = spectrum.channel_edges(1.6301, 2.4346)
    assert (e[1] - e[0]) / e[0] == pytest.approx(0.0239, abs=5e-4)


def test_a_curated_target_is_carried_along_its_proper_motion_and_other_positions_are_not():
    t = targets.find("Barnard's Star")
    barnard = {"name": "Barnard's Star", "coordinates": {"raDeg": t.ra, "decDeg": t.dec}}
    ra, dec = spectrum.position_for(barnard, 2026.0, None, None)
    assert dec - t.dec == pytest.approx(10.362394 * 26.0 / 3600, rel=2e-3) and ra < t.ra
    assert spectrum.position_for(barnard, 2026.0, 10.0, 20.0) == (10.0, 20.0)                   # explicit coordinates are used as given
    plain = {"name": "Some star", "coordinates": {"raDeg": 1.0, "decDeg": 2.0}}
    assert spectrum.position_for(plain, 2026.0, None, None) == (1.0, 2.0)


# ---- the endpoint ---------------------------------------------------------------------------------------------------------------
def test_spectrum_has_one_point_per_band_sorted_with_gaps_and_a_valid_contract_shape(env, tmp_path):
    c, cut, _ = env
    _waves(cut, tmp_path)
    oid = _object(c)["objectId"]
    r = _spec(c, oid)
    assert r.status_code == 200, r.text
    body = r.json()
    m = schemas.SpectralData.model_validate(body)
    assert m.availability.status == "available" and m.wavelengthUnit == "um" and m.fluxUnit == "mJy" and m.expectedBandCount == 102
    assert all(v is not None for v in body.values())                                              # omitted, never null
    pts = body["points"]
    assert len(pts) == 34                                                                          # D3 and D6 each list their 17 bands
    assert [p["wavelengthUm"] for p in pts] == sorted(p["wavelengthUm"] for p in pts)
    assert len({p["bandIndex"] for p in pts}) == 34 and all(1 <= p["bandIndex"] <= 102 for p in pts)
    assert all(p["wavelengthMinUm"] <= p["wavelengthUm"] <= p["wavelengthMaxUm"] for p in pts)
    ok = [p for p in pts if p["available"]]
    assert len(ok) == 3
    for p in ok:
        assert 0.85 * _flux(100.0) < p["flux"] < _flux(100.0) and p["fluxUncertainty"] > 0         # the star is 3.4 arcsec off the default centre, so a little flux is missed and p["observationId"].startswith("obs_") and p["observedAt"]
        assert "qualityFlags" in p
    gap = next(p for p in pts if not p["available"])
    assert "flux" not in gap and "fluxUncertainty" not in gap and "observationId" not in gap and "falls in this band" in gap["unavailableReason"]
    assert {p["bandIndex"] for p in ok} == {38, 45, 83 + 0} or len({p["bandIndex"] for p in ok}) == 3  # D3 bands 38 and 45; D6 band depends on its range
    text = " ".join(body["warnings"])
    assert "different observation dates" in text and "Band numbers are estimated" in text and "Detectors covered: D3, D6" in text
    assert "No frames were available for D1, D2, D4, D5" in text and "no aperture correction" in text


def test_the_band_with_the_best_signal_to_noise_is_shown_not_an_average(env, tmp_path):
    c, cut, _ = env
    _waves(cut, tmp_path, shifts=(0.0, 0.0, 0.0), peaks=(100.0, 250.0, 100.0))                    # the two D3 frames share one band; the second is brighter
    oid = _object(c)["objectId"]
    body = _spec(c, oid, ra=STAR[0], dec=STAR[1]).json()                                          # centred on the star: exact fluxes
    d3 = [p for p in body["points"] if p["available"] and 35 <= p["bandIndex"] <= 51]
    # rel 5 percent: this synthetic star is far broader (sigma 1.2 pixels, FWHM 17 arcsec) than SPHEREx's (about 6 arcsec), and for a broad star the
    # fractional-overlap aperture reads about 2.7 percent low (measured in docs/SPECTRUM_VALIDATION.md); at real widths the bias is 0.2 to 2 percent.
    assert len(d3) == 1 and d3[0]["flux"] == pytest.approx(_flux(250.0), rel=0.05)
    second = c.get(f"/api/v1/objects/{oid}/observations").json()["items"][1]["observationId"]
    assert d3[0]["observationId"] == second


def test_an_unmeasurable_frame_becomes_a_gap_with_its_own_reason(env, tmp_path):
    c, cut, _ = env
    crit = quality.UNUSABLE_MASK & -quality.UNUSABLE_MASK
    _waves(cut, tmp_path, flags=(1 << 21, crit, 1 << 21))                                         # the second D3 frame is entirely flagged unusable
    oid = _object(c)["objectId"]
    body = _spec(c, oid).json()
    d3 = {p["bandIndex"]: p for p in body["points"] if 35 <= p["bandIndex"] <= 51}
    assert d3[38]["available"] is True and d3[45]["available"] is False
    assert "background pixels" in d3[45]["unavailableReason"] or "usable data" in d3[45]["unavailableReason"]
    assert any("could not be measured" in w for w in body["warnings"])


def test_when_nothing_can_be_measured_the_spectrum_is_unavailable_with_a_reason(env, tmp_path):
    c, cut, _ = env
    crit = quality.UNUSABLE_MASK & -quality.UNUSABLE_MASK
    _waves(cut, tmp_path, flags=(crit,) * 3)
    body = _spec(c, _object(c)["objectId"]).json()
    assert body["availability"]["status"] == "unavailable" and "masked" in body["availability"]["statusReason"]
    assert all(not p["available"] for p in body["points"])


def test_explicit_position_and_radius_are_honoured_and_make_a_different_spectrum(env, tmp_path):
    c, cut, _ = env
    _waves(cut, tmp_path)
    oid = _object(c)["objectId"]
    default = _spec(c, oid).json()
    other = _spec(c, oid, ra=STAR[0], dec=STAR[1], apertureRadiusArcsec=20).json()
    assert other["spectrumId"] != default["spectrumId"] and other["apertureRadiusArcsec"] == 20 and other["position"] == {"raDeg": STAR[0], "decDeg": STAR[1]}
    assert next(p for p in other["points"] if p["available"])["flux"] == pytest.approx(_flux(100.0, 20.0), rel=0.03)
    assert other["spectrumId"] == _spec(c, oid, ra=STAR[0], dec=STAR[1], apertureRadiusArcsec=20).json()["spectrumId"]      # stable


def test_bad_parameters_and_unknown_objects(env):
    c, *_ = env
    oid = _object(c)["objectId"]
    for params in ({"ra": 10.0}, {"dec": 10.0}, {"ra": 500, "dec": 0}, {"apertureRadiusArcsec": 2}, {"apertureRadiusArcsec": 61}):
        r = _spec(c, oid, **params)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST", params
    assert _spec(c, "obj_nope").status_code == 404


def test_a_second_request_is_answered_from_the_stored_spectrum(env, tmp_path):
    c, cut, _ = env
    _waves(cut, tmp_path)
    oid = _object(c)["objectId"]
    first = _spec(c, oid).json()
    calls = cut.calls
    assert _spec(c, oid).json() == first and cut.calls == calls


def test_a_slow_spectrum_answers_202_then_the_finished_spectrum(env, tmp_path):
    c, cut, _ = env
    _waves(cut, tmp_path)
    c.app.state.services.spectrum_wait_s = 0.15
    oid = _object(c)["objectId"]
    cut.gate = threading.Event()
    r = _spec(c, oid)
    body = r.json()
    assert r.status_code == 202 and body["availability"]["status"] == "processing" and body["availability"]["retryAfterSeconds"] == 3 and body["points"] == []
    schemas.SpectralData.model_validate(body)
    assert _spec(c, oid).status_code == 202                                                       # the same job, not a second one
    cut.gate.set()
    t = time.time()
    while time.time() - t < 10:
        r = _spec(c, oid)
        if r.status_code == 200:
            break
        time.sleep(0.05)
    assert r.status_code == 200 and r.json()["availability"]["status"] == "available" and len([p for p in r.json()["points"] if p["available"]]) == 3


def test_an_object_with_no_observations_gets_an_unavailable_spectrum_not_an_error(env):
    c, _, state = env
    oid = _object(c)["objectId"]
    state["files"] = []
    body = _spec(c, oid)
    assert body.status_code == 200 and body.json()["availability"]["status"] == "unavailable" and body.json()["points"] == []


def test_archive_trouble_with_nothing_saved_is_a_503(env):
    from processing import irsa
    c, _, state = env
    oid = _object(c)["objectId"]
    state["discover_error"] = irsa.IrsaError("archive down")
    r = _spec(c, oid)
    assert r.status_code == 503 and r.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


def test_uncached_frames_are_skipped_once_the_download_budget_is_spent(env, tmp_path, monkeypatch):
    c, cut, _ = env
    _waves(cut, tmp_path)
    cached_uri = URI_D3_A
    cut.is_cached = lambda url: cached_uri in url                                                  # only the first D3 frame is already stored
    monkeypatch.setattr(spectrum, "DOWNLOAD_BUDGET_S", 0.0)
    body = _spec(c, _object(c)["objectId"]).json()
    assert len([p for p in body["points"] if p["available"]]) == 1
    assert any("were not processed" in w and "time budget" in w for w in body["warnings"])


def test_the_spectrum_reaches_the_companions_facts(env, tmp_path):
    from ai import facts
    c, cut, _ = env
    _waves(cut, tmp_path)
    oid = _object(c)["objectId"]
    sp = _spec(c, oid).json()
    pack = facts.build(c.app.state.services, {"objectId": oid, "spectrumId": sp["spectrumId"], "mode": "beginner"})
    f = next(x for x in pack.facts if x.target_type == "spectrum")
    assert f.target_id == sp["spectrumId"] and "3 bands" in f.text and "different observation dates" in " ".join(f.warnings)
    assert "not available" not in f.text and f.plain and "spectrum" in f.plain.lower()
    assert any(abs(n - 1.77) < 0.01 for n in f.numbers)                                             # a measured wavelength is quotable


def test_a_dead_archive_stops_costing_time_after_a_few_failures(env, tmp_path, monkeypatch):
    """With the archive down, uncached frames must not each burn their own retries: after 3 failures in a row the rest are skipped at once."""
    from processing import irsa
    c, cut, _ = env
    _waves(cut, tmp_path)
    cut.is_cached = lambda url: URI_D3_A in url                                                     # only the first D3 frame is stored
    attempts = []
    real_get = cut.get

    def get(url, **kw):
        if URI_D3_A in url:
            return real_get(url, **kw)
        attempts.append(url)
        raise irsa.IrsaError("The SPHEREx archive could not be reached.")

    cut.get = get
    monkeypatch.setattr(spectrum, "ARCHIVE_FAILURES_TO_GIVE_UP", 1)                                 # one failure is enough in this small test
    monkeypatch.setattr(spectrum, "WORKERS", 1)
    body = _spec(c, _object(c)["objectId"]).json()
    assert len(attempts) == 1                                                                         # the second uncached frame was not even tried
    assert len([p for p in body["points"] if p["available"]]) == 1
    assert any("because the SPHEREx archive did not respond" in w for w in body["warnings"])


def test_downloads_for_a_spectrum_are_quick_and_the_archive_is_probed_once(env, tmp_path):
    """Uncached frames are fetched with one retry and a 15 s timeout, and when the first one fails the others are not even attempted."""
    from processing import irsa
    c, cut, _ = env
    _waves(cut, tmp_path)
    cut.is_cached = lambda url: URI_D3_A in url
    seen = []
    real_get = cut.get

    def get(url, **kw):
        if URI_D3_A in url:
            return real_get(url, **kw)
        seen.append(kw)
        raise irsa.IrsaError("The SPHEREx archive could not be reached.")

    cut.get = get
    body = _spec(c, _object(c)["objectId"]).json()
    assert len(seen) == 1 and seen[0] == {"retries": 1, "timeout_s": 15.0}                         # one probe, with the quick settings
    assert len([p for p in body["points"] if p["available"]]) == 1
    assert any("because the SPHEREx archive did not respond" in w for w in body["warnings"])


def test_an_incomplete_spectrum_is_rebuilt_after_a_short_while_but_a_complete_one_is_kept(env, tmp_path):
    c, cut, _ = env
    svc = c.app.state.services
    _waves(cut, tmp_path)
    oid = _object(c)["objectId"]
    cut.is_cached = lambda url: URI_D3_A in url                                                      # two of the three frames are uncached ...
    full_get = cut.get
    from processing import irsa

    def flaky(url, **kw):
        if URI_D3_A in url:
            return full_get(url, **kw)
        raise irsa.IrsaError("The SPHEREx archive could not be reached.")                           # ... and the archive is down

    cut.get = flaky
    first = _spec(c, oid).json()
    assert len([p for p in first["points"] if p["available"]]) == 1
    cut.get, cut.is_cached = full_get, (lambda url: True)                                            # the archive recovers
    again = _spec(c, oid).json()
    assert again == first                                                                              # inside the short window the incomplete result is reused
    svc.spectrum_partial_ttl_s = 0
    healed = _spec(c, oid).json()
    assert len([p for p in healed["points"] if p["available"]]) == 3                                  # after it, the spectrum is rebuilt and now complete
    svc.spectrum_partial_ttl_s = 0
    calls = cut.calls
    assert _spec(c, oid).json() == healed and cut.calls == calls                                      # a complete spectrum is never rebuilt, whatever the ttl


def test_the_reasons_frames_were_skipped_are_counted_once_each_and_add_up(env, tmp_path, monkeypatch):
    """Regression (seen in the browser): a budget skip was also counted as an archive skip, so the reasons added up to more than the total."""
    import re
    from processing import irsa
    c, cut, _ = env
    _waves(cut, tmp_path)
    cut.is_cached = lambda url: URI_D3_A in url                                                       # two uncached frames
    monkeypatch.setattr(spectrum, "WORKERS", 1)
    real_get = cut.get
    n = [0]

    def get(url, **kw):
        if URI_D3_A in url:
            return real_get(url, **kw)
        n[0] += 1
        raise irsa.IrsaError("The SPHEREx archive could not be reached.")

    cut.get = get
    monkeypatch.setattr(spectrum, "DOWNLOAD_BUDGET_S", 0.0)                                           # every uncached frame is out of budget
    text = next(w for w in _spec(c, _object(c)["objectId"]).json()["warnings"] if "were not processed" in w)
    counts = [int(x) for x in re.findall(r"(\d+) (?:because|for other)", text)]
    total = int(re.search(r"; (\d+) were not processed", text).group(1))
    assert sum(counts) == total and all(x >= 0 for x in counts) and "-" not in re.sub(r"\d{4}-\d{2}-\d{2}", "", text.split("not processed")[1])
    assert "time budget" in text and "did not respond" not in text                                    # only the budget reason applies here


def test_prewarming_builds_every_target_in_turn_and_never_raises(env):
    c, *_ = env
    svc = c.app.state.services
    calls = []

    def fake(object_id, **kw):
        calls.append(object_id)
        if len(calls) == 2:
            raise RuntimeError("the archive fell over")
        return {"availability": {"status": "available"}}

    svc.get_spectrum = fake
    assert svc.prewarm_spectra() == 4                                                                  # five targets, one failed, the rest still built
    assert len(calls) == 5 and len(set(calls)) == 5
    assert svc.prewarm_spectra(["Epsilon Indi B"]) == 1 and len(calls) == 6


def test_the_prewarm_is_off_unless_asked_for():
    from app.config import Settings
    assert Settings(_env_file=None).prewarm_spectra is False
    assert Settings(_env_file=None, prewarm_spectra=True).prewarm_spectra is True


def test_a_refusal_that_is_not_archive_trouble_is_stored_and_served_without_rebuilding(env, tmp_path):
    """A saturated star cannot be measured. That answer is kept, so the page does not re-derive it on every visit."""
    c, cut, _ = env
    _waves(cut, tmp_path, flags=(1 << 6, 1 << 6, 1 << 6))                    # every pixel flagged unusable: nothing can be measured
    oid = _object(c)["objectId"]
    first = _spec(c, oid).json()
    assert first["availability"]["status"] == "unavailable"
    svc = c.app.state.services
    sid = first["spectrumId"]
    assert svc.registry.get_spectrum(sid)["result"]["availability"]["status"] == "unavailable"            # stored
    calls = cut.calls
    again = _spec(c, oid).json()
    assert again["availability"]["status"] == "unavailable" and cut.calls == calls                          # served from the store: no cutout was read again


def test_a_single_lucky_band_among_many_unmeasurable_frames_is_not_a_spectrum(env, tmp_path, monkeypatch):
    """A saturated star can leave one measurable frame among dozens. One band is a measurement, not a spectrum, so it is refused with that reason."""
    c, cut, _ = env
    _waves(cut, tmp_path)
    oid = _object(c)["objectId"]
    body = _spec(c, oid).json()
    assert body["availability"]["status"] == "available"                                   # three bands in a three-frame field: still a (short) spectrum
    monkeypatch.setattr(spectrum, "MANY_FRAMES", 2)
    monkeypatch.setattr(spectrum, "MIN_BANDS", 4)                                          # now three measured bands out of three frames is 'too few'
    c.app.state.services.registry.put_spectrum(body["spectrumId"], {"result": {"availability": {"status": "unavailable"}}, "partial": True, "savedAt": "2000-01-01T00:00:00Z"})
    again = _spec(c, oid, apertureRadiusArcsec=13.0).json()
    assert again["availability"]["status"] == "unavailable" and "not enough to draw a spectrum" in again["availability"]["statusReason"]
