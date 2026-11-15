"""Aperture photometry checked against injected truth: known flux, calibrated uncertainty, masking, edges, non-detections."""
import math

import numpy as np
import pytest
from astropy.wcs import WCS

from processing import photometry, quality
from processing.fits_io import Cutout
from processing.photometry import Unmeasurable
from tests.conftest import CENTER

pytestmark = pytest.mark.synthetic

SCALE = 6.2                      # arcsec / pixel
N = 41
OMEGA = (SCALE / 206264.806247) ** 2
CRIT_BIT = quality.UNUSABLE_MASK & -quality.UNUSABLE_MASK


def _wcs(n=N):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(CENTER)
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    s = SCALE / 3600.0
    w.wcs.cdelt = [-s, s]
    w.wcs.cunit = ["deg", "deg"]
    return w


def _cutout(total_counts=0.0, sigma_px=1.2, bkg=3.0, noise=0.0, var=0.25, seed=0, n=N, flags=None, with_var=True):
    """total_counts = sum over pixels of the injected star, in MJy/sr; the true flux in mJy is total_counts * OMEGA * 1e9."""
    yy, xx = np.mgrid[0:n, 0:n]
    c = (n - 1) / 2
    g = np.exp(-((xx - c) ** 2 + (yy - c) ** 2) / (2 * sigma_px ** 2))
    img = bkg + total_counts * g / g.sum()
    if noise:
        img = img + np.random.default_rng(seed).normal(0, noise, img.shape)
    return Cutout(image=img.astype(np.float32), wcs=_wcs(n), unit="MJy / sr",
                  flags=np.zeros((n, n), dtype=np.int32) if flags is None else flags,
                  variance=np.full((n, n), var, dtype=np.float32) if with_var else None)


def test_recovers_injected_flux():
    truth_mjy = 900.0 * OMEGA * 1e9
    r = photometry.aperture_flux(_cutout(total_counts=900.0), *CENTER, radius_arcsec=30, annulus_arcsec=(45, 70))
    assert r.flux_mjy == pytest.approx(truth_mjy, rel=0.01)
    assert r.background_mjy_sr == pytest.approx(3.0, abs=1e-3)
    assert r.uncertainty_mjy > 0 and r.snr > 3
    assert r.usable_fraction == pytest.approx(1.0)
    assert "solid angle" in r.method                                    # unit conversion is described
    assert "no aperture correction" in r.method


def test_small_aperture_loses_flux_and_we_say_so():
    full = photometry.aperture_flux(_cutout(total_counts=900.0), *CENTER, radius_arcsec=30, annulus_arcsec=(45, 70)).flux_mjy
    small = photometry.aperture_flux(_cutout(total_counts=900.0), *CENTER, radius_arcsec=12, annulus_arcsec=(24, 36)).flux_mjy
    expected = 1 - math.exp(-((12 / SCALE) ** 2) / (2 * 1.2 ** 2))        # enclosed fraction of a Gaussian
    assert small / full == pytest.approx(expected, rel=0.03)


def test_background_is_removed_whatever_its_level():
    a = photometry.aperture_flux(_cutout(total_counts=900.0, bkg=3.0), *CENTER, radius_arcsec=30, annulus_arcsec=(45, 70)).flux_mjy
    b = photometry.aperture_flux(_cutout(total_counts=900.0, bkg=250.0), *CENTER, radius_arcsec=30, annulus_arcsec=(45, 70)).flux_mjy
    assert a == pytest.approx(b, rel=1e-3)


def test_uncertainty_matches_real_scatter():
    """Pure noise (sigma 0.5 => variance 0.25): the reported uncertainty must match the spread of the measured fluxes."""
    fluxes, sigmas = [], []
    for seed in range(300):
        r = photometry.aperture_flux(_cutout(noise=0.5, var=0.25, seed=seed), *CENTER)
        fluxes.append(r.flux_mjy)
        sigmas.append(r.uncertainty_mjy)
    fluxes, sigmas = np.array(fluxes), np.array(sigmas)
    assert np.std(fluxes) == pytest.approx(np.mean(sigmas), rel=0.15)
    assert abs(np.mean(fluxes)) < 3 * np.std(fluxes) / math.sqrt(len(fluxes))        # unbiased
    within = np.mean(np.abs(fluxes) < sigmas)
    assert 0.55 < within < 0.80                                                         # about 68% within 1 sigma


def test_underestimated_archive_variance_is_rescaled():
    """The archive claims variance 0.01 but the real noise is 0.5: the annulus scatter must win."""
    fluxes, sigmas, rescales = [], [], []
    for seed in range(200):
        r = photometry.aperture_flux(_cutout(noise=0.5, var=0.01, seed=seed), *CENTER)
        fluxes.append(r.flux_mjy)
        sigmas.append(r.uncertainty_mjy)
        rescales.append(r.noise_rescale)
    assert np.mean(rescales) > 10
    assert np.std(fluxes) == pytest.approx(np.mean(sigmas), rel=0.2)
    assert any("scaled up" in w for w in photometry.aperture_flux(_cutout(noise=0.5, var=0.01, seed=1), *CENTER).warnings)


def test_no_archive_variance_still_gives_uncertainty_and_says_so():
    r = photometry.aperture_flux(_cutout(noise=0.5, seed=3, with_var=False), *CENTER)
    assert r.uncertainty_mjy > 0
    assert any("uncertainty plane was not available" in w for w in r.warnings)
    assert "background ring" in r.uncertainty_basis


def test_non_detection_reports_low_significance_not_zero_flux_claim():
    r = photometry.aperture_flux(_cutout(noise=0.5, seed=5), *CENTER)
    assert abs(r.snr) < 3
    assert any("no source is significantly detected" in w for w in r.warnings)
    assert r.uncertainty_mjy > 0


def test_fully_masked_aperture_is_unmeasurable_not_zero():
    flags = np.zeros((N, N), dtype=np.int32)
    flags[18:23, 18:23] = CRIT_BIT                # covers the whole 12 arcsec aperture, leaves the ring usable
    with pytest.raises(Unmeasurable, match="usable data"):
        photometry.aperture_flux(_cutout(total_counts=900.0, flags=flags), *CENTER)


def test_partly_masked_aperture_warns_and_is_scaled():
    flags = np.zeros((N, N), dtype=np.int32)
    flags[20, 23] = CRIT_BIT                      # one pixel in the wing of the aperture
    clean = photometry.aperture_flux(_cutout(total_counts=900.0), *CENTER, radius_arcsec=30, annulus_arcsec=(45, 70))
    part = photometry.aperture_flux(_cutout(total_counts=900.0, flags=flags), *CENTER, radius_arcsec=30, annulus_arcsec=(45, 70))
    assert part.usable_fraction < 1.0
    assert any("masked or off the image" in w for w in part.warnings)
    assert part.flux_mjy == pytest.approx(clean.flux_mjy, rel=0.05)
    assert any(f["code"] for f in part.quality_flags)                 # the flag is reported for the region


def test_masked_background_ring_is_unmeasurable():
    flags = np.full((N, N), CRIT_BIT, dtype=np.int32)
    flags[18:23, 18:23] = 0                       # only the centre is usable; the ring is not
    with pytest.raises(Unmeasurable, match="background pixels"):
        photometry.aperture_flux(_cutout(total_counts=900.0, flags=flags), *CENTER)


def test_position_off_the_image_is_unmeasurable():
    with pytest.raises(Unmeasurable, match="outside the image"):
        photometry.aperture_flux(_cutout(total_counts=900.0), CENTER[0] + 1.0, CENTER[1])


def test_near_the_edge_is_flagged_not_hidden():
    w = _wcs()
    ra, dec = w.wcs_pix2world([[1.0, 20.0]], 0)[0]                    # 1 pixel from the left edge: part of the aperture is off-image
    r = photometry.aperture_flux(_cutout(total_counts=0.0, noise=0.5, seed=2), float(ra), float(dec))
    assert r.usable_fraction < 1.0
    assert any("masked or off the image" in x for x in r.warnings)


def test_corner_position_has_too_little_usable_data():
    w = _wcs()
    ra, dec = w.wcs_pix2world([[-1.2, -1.2]], 0)[0]                   # centre just outside the corner
    with pytest.raises(Unmeasurable):
        photometry.aperture_flux(_cutout(total_counts=900.0), float(ra), float(dec))


def test_wcs_non_convergence_far_from_the_image_is_unmeasurable(monkeypatch):
    """Real SPHEREx cutouts use TAN-SIP; astropy's inverse can raise NoConvergence far from the image. That must not become a crash."""
    from astropy.wcs import NoConvergence
    c = _cutout()

    def boom(*a, **k):
        raise NoConvergence("diverging")
    monkeypatch.setattr(c.wcs, "all_world2pix", boom)
    with pytest.raises(Unmeasurable, match="too far"):
        photometry.aperture_flux(c, *CENTER)
    assert photometry.fits_in_image(c, *CENTER, outer_arcsec=36) is False


def test_fits_in_image():
    c = _cutout()
    assert photometry.fits_in_image(c, *CENTER, outer_arcsec=36)
    assert not photometry.fits_in_image(c, *CENTER, outer_arcsec=200)


def test_bad_geometry_is_rejected():
    with pytest.raises(ValueError):
        photometry.aperture_flux(_cutout(), *CENTER, radius_arcsec=30, annulus_arcsec=(20, 36))


def test_wrong_unit_is_refused():
    c = _cutout()
    c.unit = "DN"
    with pytest.raises(Unmeasurable, match="not MJy/sr"):
        photometry.aperture_flux(c, *CENTER)


def test_pixel_solid_angle_matches_scale():
    c = _cutout()
    assert photometry.pixel_scale_arcsec(c) == pytest.approx(SCALE, rel=1e-4)
    assert photometry.pixel_solid_angle_sr(c) == pytest.approx(OMEGA, rel=1e-4)
