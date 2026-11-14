import numpy as np
import pytest

from processing import quality

pytestmark = pytest.mark.synthetic


def test_decode_values_seen_in_real_cutout():
    # Values observed in a real SPHEREx cutout (scripts/PROBE_FINDINGS.md).
    assert [d.name for d in quality.decode_value(524288)] == ["OUTLIER"]
    assert [d.name for d in quality.decode_value(2097152)] == ["SOURCE"]
    assert {d.name for d in quality.decode_value(2097221)} == {"SOURCE", "TRANSIENT", "SUR_ERROR", "NONFUNC"}


def test_unknown_bit_is_kept_as_info():
    (d,) = quality.decode_value(1 << 40)
    assert d.name == "BIT40" and d.severity == "info"


def test_source_flag_is_not_treated_as_bad_data():
    flags = np.full((10, 10), 1 << 21)           # every pixel marks a known source
    assert not quality.unusable(flags).any()
    assert quality.quality_summary(flags) == "good"


def test_nonfunctional_pixels_make_quality_poor():
    flags = np.zeros((10, 10), dtype=np.int32)
    flags[:5, :] = 1 << 6                        # half the pixels dead
    assert quality.unusable(flags).sum() == 50
    assert quality.quality_summary(flags) == "poor"


def test_summarize_reports_counts_and_contract_fields():
    flags = np.zeros((4, 4), dtype=np.int32)
    flags[0, 0] = 1 << 21
    (f,) = quality.summarize(flags)
    assert f["code"] == "SOURCE" and f["origin"] == "source" and f["severity"] == "info"
    assert "1 of 16 pixels" in f["description"]


def test_empty_region_is_unknown():
    assert quality.quality_summary(np.zeros((3, 3)), region=np.zeros((3, 3), dtype=bool)) == "unknown"
