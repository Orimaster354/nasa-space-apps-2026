import pytest

from processing.compare import Side, assess

pytestmark = pytest.mark.synthetic


def S(band="SPHEREx-D3", rel="dr1", q="good", z=1.0, wl=2.0, bw=0.05):
    return Side(band, rel, q, z, wl, bw)


def results(r):
    return {c["name"]: c["result"] for c in r["checks"]}


def test_same_band_same_release_is_comparable_with_caveats_never_plain_comparable():
    r = assess(S(), S(), "blink")
    assert r["comparisonKind"] == "same_band_epochs" and r["verdict"] == "comparable_with_caveats"
    c = results(r)
    assert c["wavelength"] == "pass" and c["calibration"] == "notEvaluated" and c["alignment"] == "notEvaluated"   # never claimed as pass


def test_cross_band_difference_is_not_comparable():
    r = assess(S("SPHEREx-D3"), S("SPHEREx-D6"), "difference")
    assert r["verdict"] == "not_comparable" and results(r)["wavelength"] == "fail" and r["comparisonKind"] == "cross_band"


def test_cross_band_blink_is_allowed_but_labelled_spectral():
    r = assess(S("SPHEREx-D3"), S("SPHEREx-D6"), "blink")
    assert r["verdict"] == "comparable_with_caveats" and results(r)["wavelength"] == "warn"
    assert any("spectral" in w for w in r["warnings"])


def test_different_releases_warn_about_calibration():
    r = assess(S(rel="dr1"), S(rel="qr3"), "overlay")
    assert results(r)["dataRelease"] == "warn" and results(r)["calibration"] == "warn"


def test_quality_levels():
    assert results(assess(S(q="good"), S(q="good"), "blink"))["quality"] == "pass"
    assert results(assess(S(q="good"), S(q="degraded"), "blink"))["quality"] == "warn"
    assert results(assess(S(q="poor"), S(q="good"), "blink"))["quality"] == "warn"
    assert results(assess(S(q="unknown"), S(q="good"), "blink"))["quality"] == "notEvaluated"


def test_zodi_background_check():
    assert results(assess(S(z=1.00), S(z=1.02), "difference"))["background"] == "pass"
    r = assess(S(z=1.0), S(z=1.5), "difference")
    assert results(r)["background"] == "warn" and "background" in [c["detail"] for c in r["checks"] if c["name"] == "background"][0]
    assert results(assess(S(z=None), S(z=1.0), "difference"))["background"] == "notEvaluated"


def test_difference_always_carries_the_false_positive_warning():
    r = assess(S(), S(), "difference")
    assert any("not a detection" in w for w in r["warnings"])
    assert not any("not a detection" in w for w in assess(S(), S(), "blink")["warnings"])


def test_footprint_check_present_and_missing_bandpass_is_cross_band():
    r = assess(S(band=None), S(band=None), "blink")
    assert r["comparisonKind"] == "cross_band" and results(r)["footprint"] == "pass"


def test_same_band_but_different_wavelength_at_the_object_warns():
    r = assess(S(wl=1.70), S(wl=2.30), "blink")
    c = results(r)
    assert c["wavelength"] == "warn" and r["verdict"] == "comparable_with_caveats" and r["comparisonKind"] == "same_band_epochs"
    assert any("different wavelengths" in w for w in r["warnings"])
    detail = next(k["detail"] for k in r["checks"] if k["name"] == "wavelength")
    assert "1.700 um" in detail and "2.300 um" in detail and "spectral" in detail


def test_difference_at_unmatched_wavelengths_warns_not_refuses():
    """Positional change is still visible, so the difference is allowed, but it is clearly caveated."""
    r = assess(S(wl=1.70), S(wl=2.30), "difference")
    assert r["verdict"] == "comparable_with_caveats" and results(r)["wavelength"] == "warn"


def test_wavelengths_within_one_bandwidth_pass():
    r = assess(S(wl=2.00), S(wl=2.04), "blink")
    assert results(r)["wavelength"] == "pass"


def test_unknown_wavelength_is_not_evaluated_not_pass():
    r = assess(S(wl=None, bw=None), S(), "blink")
    assert results(r)["wavelength"] == "notEvaluated"
    assert assess(S(bw=None), S(bw=None), "blink")["checks"][1]["result"] == "notEvaluated"


def test_alignment_check_uses_the_measured_residual_and_never_invents_one():
    from processing.alignment import AlignmentEstimate
    ok = AlignmentEstimate(True, 9, 0.3, 0.2, 0.36, 0.8, 0.33)
    bad = AlignmentEstimate(True, 9, 4.0, 1.0, 4.1, 0.8, 0.33)
    none = AlignmentEstimate(False, 1, reason="Only 1 sources matched between the frames (need 3).")
    check = lambda est: next(c for c in assess(S(), S(), "difference", est)["checks"] if c["name"] == "alignment")
    assert check(ok)["result"] == "pass" and "9 bright stars" in check(ok)["detail"] and "0.4 arcsec" in check(ok)["detail"]
    assert check(bad)["result"] == "warn" and "bright/dark pairs" in check(bad)["detail"]
    assert check(none)["result"] == "notEvaluated" and "could not be measured" in check(none)["detail"]
    assert check(None)["result"] == "notEvaluated"
