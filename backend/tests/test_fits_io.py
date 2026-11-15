"""Reading the per-pixel wavelength from a WCS-WAVE table."""
import numpy as np
import pytest

from processing.fits_io import read_cutout
from tests.conftest import CENTER, make_cutout_fits

pytestmark = pytest.mark.synthetic


def test_wavelength_map_follows_the_table_and_header_offsets(tmp_path):
    c = read_cutout(make_cutout_fits(tmp_path / "a.fits", *CENTER))
    assert c.wavelength_um.shape == c.image.shape and c.bandwidth_um.shape == c.image.shape
    p, q = 10, 20                                              # 0-based cutout pixel -> 1-based p+1, q+1 -> detector p+1+999+1, q+1+1499+1
    x_det, y_det = (p + 1) + 1000, (q + 1) + 1500
    assert c.wavelength_um[q, p] == pytest.approx(1.0 + 0.001 * x_det + 0.0005 * y_det, abs=1e-4)
    assert c.bandwidth_um[q, p] == pytest.approx(0.03)
    assert np.all(np.diff(c.wavelength_um[0, :]) > 0)           # wavelength rises along x in this synthetic table
    assert not any("wavelength" in w for w in c.warnings)


def test_missing_wave_table_is_reported_not_guessed(tmp_path):
    c = read_cutout(make_cutout_fits(tmp_path / "b.fits", *CENTER, with_wave=False))
    assert c.wavelength_um is None and c.bandwidth_um is None
    assert any("wavelength at each pixel is unknown" in w for w in c.warnings)
