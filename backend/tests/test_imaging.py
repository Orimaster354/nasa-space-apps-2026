import io

import numpy as np
import pytest
from PIL import Image

from processing import fits_io, imaging
from tests.conftest import CENTER, make_cutout_fits

pytestmark = pytest.mark.synthetic

STAR = (CENTER[0] + 0.0008, CENTER[1] + 0.0005)      # about 3 and 2 arcsec from the centre


def _star_sky_from_raster(arr_fits, grid):
    """Flux-weighted centroid of the star (pixels above half the peak), converted to sky via the raster mapping."""
    a = np.nan_to_num(arr_fits, nan=0.0)
    a = a - np.median(a)
    mask = a > 0.5 * a.max()
    yy, xx = np.nonzero(mask)
    w = a[mask]
    x, y_fits = (xx * w).sum() / w.sum(), (yy * w).sum() / w.sum()
    return grid.raster_to_sky(x, (grid.npix - 1) - y_fits)


def test_read_cutout_extracts_everything(tmp_path):
    c = fits_io.read_cutout(make_cutout_fits(tmp_path / "a.fits", *STAR, detector=3, mjd=60800.7))
    assert c.unit == "MJy / sr" and c.detector == "3" and c.obsid == "TEST_OBS_0001"
    assert c.mjd_obs == pytest.approx(60800.7) and c.flags is not None and not c.warnings


def test_missing_extensions_become_warnings_not_crashes(tmp_path):
    from astropy.io import fits
    p = make_cutout_fits(tmp_path / "b.fits", *STAR)
    with fits.open(p) as h:
        fits.HDUList([h[0], h["IMAGE"]]).writeto(tmp_path / "c.fits")
    c = fits_io.read_cutout(tmp_path / "c.fits")
    assert c.flags is None and c.mjd_obs is None and len(c.warnings) >= 3


def test_not_a_fits_file_raises(tmp_path):
    (tmp_path / "x.fits").write_bytes(b"nope")
    with pytest.raises(fits_io.FitsReadError):
        fits_io.read_cutout(tmp_path / "x.fits")


@pytest.mark.parametrize("rotation", [0.0, 35.0, 170.0])
def test_star_lands_at_same_sky_position_on_the_common_grid_for_any_pointing_rotation(tmp_path, rotation):
    grid = imaging.GridSpec(*CENTER, fov_arcmin=2.0)
    c = fits_io.read_cutout(make_cutout_fits(tmp_path / "r.fits", *STAR, rotation_deg=rotation))
    out = imaging.to_grid(c, grid)
    ra, dec = _star_sky_from_raster(out, grid)
    err_arcsec = np.hypot((ra - STAR[0]) * np.cos(np.deg2rad(STAR[1])), dec - STAR[1]) * 3600
    assert err_arcsec < 1.0                      # centroid within 1 arcsec (half an output pixel)


def test_two_pointings_share_one_grid_so_blinking_needs_no_alignment(tmp_path):
    grid = imaging.GridSpec(*CENTER, fov_arcmin=2.0)
    a = imaging.to_grid(fits_io.read_cutout(make_cutout_fits(tmp_path / "a.fits", *STAR, rotation_deg=0)), grid)
    b = imaging.to_grid(fits_io.read_cutout(make_cutout_fits(tmp_path / "b.fits", *STAR, rotation_deg=90)), grid)
    assert a.shape == b.shape == (grid.npix, grid.npix)
    assert np.hypot(*(np.subtract(np.unravel_index(np.nanargmax(a), a.shape), np.unravel_index(np.nanargmax(b), b.shape)))) <= 2


def test_grid_id_is_deterministic_and_parameter_sensitive():
    g1, g2 = imaging.GridSpec(10, 10, 3.0), imaging.GridSpec(10, 10, 3.0)
    assert g1.grid_id == g2.grid_id != imaging.GridSpec(10, 10, 4.0).grid_id


def test_raster_sky_round_trip_and_orientation():
    g = imaging.GridSpec(*CENTER, fov_arcmin=3.0)
    x, y = 17.3, 41.9
    ra, dec = g.raster_to_sky(x, y)
    x2, y2 = g.sky_to_raster(ra, dec)
    assert (x2, y2) == pytest.approx((x, y), abs=1e-6)
    # raster y grows downward, so smaller y means higher declination; larger x means smaller RA (east to the left)
    assert g.raster_to_sky(g.npix / 2, 0)[1] > g.raster_to_sky(g.npix / 2, g.npix - 1)[1]
    assert g.raster_to_sky(0, g.npix / 2)[0] > g.raster_to_sky(g.npix - 1, g.npix / 2)[0]


def test_fov_limits():
    with pytest.raises(ValueError):
        imaging.GridSpec(10, 10, 0)
    with pytest.raises(ValueError):
        imaging.GridSpec(10, 10, imaging.MAX_FOV_ARCMIN + 1)


def test_unusable_pixels_do_not_contribute(tmp_path):
    c = fits_io.read_cutout(make_cutout_fits(tmp_path / "f.fits", *STAR, flag_value=1 << 6))   # every pixel dead
    assert np.isnan(imaging.to_grid(c, imaging.GridSpec(*CENTER, 2.0))).all()


def test_png_render_is_valid_and_top_is_north():
    g = imaging.GridSpec(*CENTER, fov_arcmin=2.0)
    arr = np.zeros((g.npix, g.npix))
    arr[-5:, g.npix // 2] = 100.0        # FITS rows near the top of the array = north
    png, st = imaging.render_png(arr, "asinh", 1, 99.9)
    img = np.array(Image.open(io.BytesIO(png)))
    assert img.shape == (g.npix, g.npix) and img[:5, g.npix // 2].max() > img[-5:, g.npix // 2].max()
    assert st["mode"] == "asinh" and st["highValue"] >= st["lowValue"]
    with pytest.raises(ValueError):
        imaging.render_png(arr, "bogus")
