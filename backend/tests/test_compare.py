import pytest

from processing.compare import Side, assess

pytestmark = pytest.mark.synthetic


def S(band="SPHEREx-D3", rel="dr1", q="good", z=1.0):
    return Side(band, rel, q, z)


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
