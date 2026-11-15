"""Aperture photometry on a native-pixel SPHEREx cutout. Deterministic NumPy/Astropy code; no LLM and no rendered image is involved.

Method (also returned to clients in `method` and `uncertaintyBasis`):
  * The sky position is converted to a pixel position with the cutout's own WCS.
  * Flux is the sum of background-subtracted surface brightness (MJy/sr) over a circular aperture, using fractional pixel overlap,
    multiplied by the pixel solid angle (sr), and reported in mJy.
  * The background level is the sigma-clipped median of usable pixels in an annulus. The IMAGE plane is not zodiacal-light
    subtracted (verified on a real cutout), so a LOCAL background is essential.
  * Pixels flagged as unusable (quality.UNUSABLE_MASK), non-finite pixels, and pixels off the image are excluded. If some aperture
    pixels are excluded the flux is scaled up by (total / usable) aperture weight and this is stated as a warning. Too few usable
    pixels means "cannot measure", never a zero.
  * Uncertainty = per-pixel noise summed in quadrature over the aperture, plus the error of the background level. Per-pixel noise
    comes from the archive VARIANCE plane, rescaled upward when the scatter measured in the background annulus is larger than
    the archive variance predicts. Pixel noise is assumed independent.

Not done (stated, not hidden): no aperture correction (energy outside the aperture is not added back), so values are only
comparable between measurements taken with the SAME aperture and band; no colour correction; no PSF fitting.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from astropy.stats import sigma_clipped_stats
from astropy.wcs import NoConvergence
from astropy.wcs.utils import proj_plane_pixel_area, proj_plane_pixel_scales

from . import quality
from .fits_io import Cutout

DEFAULT_RADIUS_ARCSEC = 12.0          # about 2 native pixels
DEFAULT_ANNULUS_ARCSEC = (24.0, 36.0)
MIN_USABLE_APERTURE_FRACTION = 0.7    # our heuristic: below this the flux is too extrapolated to report
MIN_ANNULUS_PIXELS = 12               # our heuristic: fewer pixels cannot give a stable background
_SUBSAMPLE = 7                        # sub-pixels per axis for fractional aperture overlap
_MEDIAN_ERR_FACTOR = 1.2533           # standard error of a median relative to the mean, for Gaussian noise
_RESCALE_WARN = 1.5                   # warn when annulus scatter exceeds the archive sigma by this factor
# Frame-to-frame repeatability floor for comparing the brightness of two frames. Measured on Epsilon Indi B (44 real frames): frames at the
# SAME wavelength agree to 0 to 3 percent where the spectrum is flat, but three frames at 1.724 um (formal errors about 1 percent) spread by
# 8 percent where it is steep, so the formal uncertainty alone makes real-world scatter look like change. 3 percent per frame is added in
# quadrature for brightness comparisons; it is our heuristic, not a calibrated value.
SYSTEMATIC_FRACTION = 0.03
LOW_SNR = 3.0                         # wording threshold only; not a detection claim
MJY_TO_MJY_SR = 1.0e9                 # 1 MJy = 1e9 mJy
FLUX_UNIT = "mJy"


class Unmeasurable(Exception):
    """The region cannot be measured (off the image, masked, or no usable background). The message is user-safe."""


@dataclass
class PhotometryResult:
    flux_mjy: float
    uncertainty_mjy: float
    snr: float
    background_mjy_sr: float
    pixel: tuple[float, float]
    radius_arcsec: float
    annulus_arcsec: tuple[float, float]
    n_aperture_pixels: float
    n_annulus_pixels: int
    usable_fraction: float
    noise_rescale: float
    method: str
    uncertainty_basis: str
    quality_flags: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    wavelength_um: float | None = None     # at the aperture centre, from the WCS-WAVE table
    bandwidth_um: float | None = None


def wave_at_pixel(cutout: Cutout, x: float, y: float) -> tuple[float | None, float | None]:
    if cutout.wavelength_um is None or cutout.bandwidth_um is None:
        return None, None
    ny, nx = cutout.wavelength_um.shape
    i, j = int(round(y)), int(round(x))
    if not (0 <= i < ny and 0 <= j < nx):
        return None, None
    wl, bw = float(cutout.wavelength_um[i, j]), float(cutout.bandwidth_um[i, j])
    return (wl if math.isfinite(wl) else None), (bw if math.isfinite(bw) else None)


def pixel_scale_arcsec(cutout: Cutout) -> float:
    return float(np.mean(proj_plane_pixel_scales(cutout.wcs)) * 3600.0)


def pixel_solid_angle_sr(cutout: Cutout) -> float:
    return float(proj_plane_pixel_area(cutout.wcs) * (math.pi / 180.0) ** 2)


def sky_to_pixel(cutout: Cutout, ra: float, dec: float) -> tuple[float, float]:
    try:
        x, y = cutout.wcs.all_world2pix([[ra, dec]], 0)[0]
    except NoConvergence as exc:                 # SIP inverse diverges far from the image; found on real cutouts
        raise Unmeasurable("That sky position is too far from this image to be placed on it.") from exc
    if not (np.isfinite(x) and np.isfinite(y)):
        raise Unmeasurable("That sky position cannot be placed on this image.")
    return float(x), float(y)


def fits_in_image(cutout: Cutout, ra: float, dec: float, outer_arcsec: float) -> bool:
    """True when the whole outer annulus circle lies inside the cutout array."""
    try:
        x, y = sky_to_pixel(cutout, ra, dec)
    except Unmeasurable:
        return False
    r = outer_arcsec / pixel_scale_arcsec(cutout)
    ny, nx = cutout.image.shape
    return x - r >= -0.5 and y - r >= -0.5 and x + r <= nx - 0.5 and y + r <= ny - 0.5


def _aperture_weights(shape: tuple[int, int], x0: float, y0: float, r: float) -> np.ndarray:
    """Fractional overlap (0..1) of each pixel with a circle, by sub-pixel sampling. Pixels off the array are simply absent."""
    ny, nx = shape
    w = np.zeros(shape, dtype=np.float64)
    xlo, xhi = max(0, int(math.floor(x0 - r - 1))), min(nx - 1, int(math.ceil(x0 + r + 1)))
    ylo, yhi = max(0, int(math.floor(y0 - r - 1))), min(ny - 1, int(math.ceil(y0 + r + 1)))
    if xlo > xhi or ylo > yhi:
        return w
    offs = (np.arange(_SUBSAMPLE) + 0.5) / _SUBSAMPLE - 0.5
    for yy in range(ylo, yhi + 1):
        for xx in range(xlo, xhi + 1):
            sx, sy = xx + offs[None, :], yy + offs[:, None]
            w[yy, xx] = np.mean((sx - x0) ** 2 + (sy - y0) ** 2 <= r * r)
    return w


def aperture_flux(cutout: Cutout, ra: float, dec: float, radius_arcsec: float = DEFAULT_RADIUS_ARCSEC,
                  annulus_arcsec: tuple[float, float] = DEFAULT_ANNULUS_ARCSEC) -> PhotometryResult:
    if not (radius_arcsec > 0 and 0 < annulus_arcsec[0] < annulus_arcsec[1] and annulus_arcsec[0] > radius_arcsec):
        raise ValueError("Need 0 < aperture radius < annulus inner radius < annulus outer radius.")
    if cutout.unit and "MJy" not in cutout.unit.replace(" ", ""):
        raise Unmeasurable(f"The image unit '{cutout.unit}' is not MJy/sr, so it cannot be converted.")

    scale = pixel_scale_arcsec(cutout)
    omega = pixel_solid_angle_sr(cutout)
    x0, y0 = sky_to_pixel(cutout, ra, dec)
    r_ap, r_in, r_out = radius_arcsec / scale, annulus_arcsec[0] / scale, annulus_arcsec[1] / scale
    ny, nx = cutout.image.shape
    if not (-0.5 <= x0 <= nx - 0.5 and -0.5 <= y0 <= ny - 0.5):
        raise Unmeasurable("That position is outside the image coverage.")

    img = cutout.image.astype(np.float64)
    good = np.isfinite(img)
    if cutout.flags is not None:
        good &= ~quality.unusable(cutout.flags)
    var = None
    if cutout.variance is not None:
        var = cutout.variance.astype(np.float64)
        good &= np.isfinite(var) & (var > 0)

    warnings: list[str] = []
    yy, xx = np.mgrid[0:ny, 0:nx]
    rr = np.hypot(xx - x0, yy - y0)

    # Background from the annulus.
    ann = good & (rr >= r_in) & (rr <= r_out)
    n_ann = int(ann.sum())
    if n_ann < MIN_ANNULUS_PIXELS:
        raise Unmeasurable(f"Only {n_ann} usable background pixels around that position (need {MIN_ANNULUS_PIXELS}); "
                           "it is too close to the image edge or too heavily masked to measure.")
    _, bkg, sig_b = sigma_clipped_stats(img[ann], sigma=3.0, maxiters=5)
    bkg, sig_b = float(bkg), float(sig_b)

    # Aperture.
    w_all = _aperture_weights(img.shape, x0, y0, r_ap)          # weights on the array
    m = int(math.ceil(2 * r_ap)) + 6                             # full-circle weight on a padded virtual array, same sampling,
    total_w = float(_aperture_weights((m, m), m / 2.0, m / 2.0, r_ap).sum())   # so off-image or masked parts count as missing
    w = w_all * good
    usable_fraction = float(w.sum() / total_w)
    if usable_fraction < MIN_USABLE_APERTURE_FRACTION:
        raise Unmeasurable(f"Only {usable_fraction:.0%} of the aperture has usable data (masked, flagged or off the image); "
                           f"at least {MIN_USABLE_APERTURE_FRACTION:.0%} is needed to measure it.")
    if usable_fraction < 0.999:
        warnings.append(f"{1 - usable_fraction:.0%} of the aperture was masked or off the image; the flux was scaled up to "
                        "compensate, which assumes the missing pixels look like the rest.")
    scale_up = total_w / float(w.sum())

    # Per-pixel noise: archive variance, rescaled to match the scatter seen in the annulus.
    rescale = 1.0
    if var is not None:
        med_var = float(np.median(var[ann]))
        rescale = max(1.0, (sig_b * sig_b) / med_var) if med_var > 0 else 1.0
        pix_var = var * rescale
        if rescale > _RESCALE_WARN ** 2:
            warnings.append(f"The scatter in the background ring is {math.sqrt(rescale):.1f} times the archive's own pixel "
                            "uncertainty (a nearby source, background structure or correlated noise); the uncertainty was "
                            "scaled up to match.")
        basis = ("Archive VARIANCE plane summed in quadrature over the aperture"
                 + (f" (scaled by {math.sqrt(rescale):.2f} to match the background-ring scatter)" if rescale > 1.0 else "")
                 + ", plus the error of the background level from the ring. Pixel noise assumed independent.")
    else:
        pix_var = np.full(img.shape, sig_b * sig_b)
        warnings.append("The archive uncertainty plane was not available; the uncertainty uses only the scatter in the background ring.")
        basis = ("Pixel scatter measured in the background ring, summed in quadrature over the aperture, plus the error of "
                 "the background level. Pixel noise assumed independent.")

    excess = img - bkg
    flux_sr = float((w * np.where(good, excess, 0.0)).sum()) * scale_up           # MJy/sr summed over pixels
    var_ap = float((w * w * np.where(good, pix_var, 0.0)).sum()) * scale_up ** 2
    var_bkg = (total_w ** 2) * (_MEDIAN_ERR_FACTOR ** 2) * sig_b * sig_b / n_ann
    sigma_sr = math.sqrt(var_ap + var_bkg)

    flux_mjy = flux_sr * omega * MJY_TO_MJY_SR
    sigma_mjy = sigma_sr * omega * MJY_TO_MJY_SR
    snr = flux_mjy / sigma_mjy if sigma_mjy > 0 else float("nan")
    if not math.isfinite(snr) or sigma_mjy <= 0:
        raise Unmeasurable("The uncertainty could not be determined for this position.")
    if abs(snr) < LOW_SNR:
        warnings.append(f"The flux is {snr:.1f} times its uncertainty, so no source is significantly detected at this position "
                        f"(the {LOW_SNR:g} threshold is a convention, not a detection claim).")

    method = (f"Circular aperture, radius {radius_arcsec:g} arcsec ({r_ap:.1f} native pixels, fractional pixel overlap); "
              f"background = sigma-clipped median of {n_ann} usable pixels in the {annulus_arcsec[0]:g}-{annulus_arcsec[1]:g} arcsec ring; "
              f"flux density = sum of background-subtracted surface brightness x pixel solid angle ({omega:.3e} sr); "
              "no aperture correction, no colour correction; measured on the native-pixel image, not the displayed raster.")
    wl, bw = wave_at_pixel(cutout, x0, y0)
    if wl is not None:
        method += (f" Wavelength at the aperture centre: {wl:.3f} um" + (f" (bandwidth about {bw:.3f} um)" if bw is not None else "")
                   + "; the value is only comparable with measurements at a similar wavelength.")
    else:
        warnings.append("The wavelength at this position was not available, so this flux cannot be matched to another frame by wavelength.")
    region = (w_all > 0)
    qflags = quality.summarize(cutout.flags, region) if cutout.flags is not None else []
    return PhotometryResult(
        flux_mjy=flux_mjy, uncertainty_mjy=sigma_mjy, snr=float(snr), background_mjy_sr=bkg, pixel=(x0, y0),
        radius_arcsec=radius_arcsec, annulus_arcsec=tuple(annulus_arcsec), n_aperture_pixels=float(w.sum()), n_annulus_pixels=n_ann,
        usable_fraction=usable_fraction, noise_rescale=rescale, method=method, uncertainty_basis=basis,
        quality_flags=qflags, warnings=warnings, wavelength_um=wl, bandwidth_um=bw)
