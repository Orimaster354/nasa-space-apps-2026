"""Shared synthetic data: a SPHEREx-like cutout MEF (IMAGE, FLAGS, VARIANCE, ZODI) with a Gaussian star at a known sky position."""
import os
from pathlib import Path

# Hermetic tests: never read real AI keys from backend/.env and never reach a live provider. Environment variables take priority
# over the .env file, and this runs before app.config is imported by any test module.
for _name in ("PROVIDER", "API_KEY", "MODEL", "BASE_URL"):
    for _i in (1, 2, 3):
        os.environ[f"SPX_LLM_{_name}_{_i}"] = ""

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

CENTER = (133.795129, -7.245147)       # near WISE 0855's challenge coordinate; used only as a convenient sky position for synthetic tests


def make_cutout_fits(path: Path, star_ra: float, star_dec: float, rotation_deg: float = 0.0, n: int = 29,
                     scale_arcsec: float = 6.2, peak: float = 100.0, mjd: float = 60800.7, detector: int = 3,
                     flag_value: int = 0, center=CENTER, with_wave: bool = True, wave_shift_x: float = 0.0, wave_offset: float = 0.0,
                     stars=None, noise: float = 0.0, noise_seed: int = 0) -> Path:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(center)
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    s = scale_arcsec / 3600.0
    t = np.deg2rad(rotation_deg)
    w.wcs.cd = [[-s * np.cos(t), s * np.sin(t)], [s * np.sin(t), s * np.cos(t)]]
    w.wcs.cunit = ["deg", "deg"]

    yy, xx = np.mgrid[0:n, 0:n]
    image = np.full((n, n), 1.0)
    for ra_s, dec_s, pk in (stars if stars is not None else [(star_ra, star_dec, peak)]):
        x0, y0 = w.wcs_world2pix([[ra_s, dec_s]], 0)[0]
        image = image + pk * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * 1.2 ** 2))
    if noise:
        image = image + np.random.default_rng(noise_seed).normal(0, noise, image.shape)
    image = image.astype(np.float32)
    flags = np.full((n, n), flag_value, dtype=np.int32)
    variance = np.full((n, n), 0.25, dtype=np.float32)
    zodi = np.full((n, n), 0.5, dtype=np.float32)

    hdr = w.to_header()
    prim = fits.PrimaryHDU()
    prim.header["VERSION"] = "test"
    im = fits.ImageHDU(image, header=hdr, name="IMAGE")
    im.header["BUNIT"] = "MJy / sr"
    im.header["DETECTOR"] = detector
    im.header["OBSID"] = "TEST_OBS_0001"
    hdus = [prim, im, fits.ImageHDU(flags, header=hdr, name="FLAGS"),
            fits.ImageHDU(variance, header=hdr, name="VARIANCE"), fits.ImageHDU(zodi, header=hdr, name="ZODI")]
    hdus[3].header["MJD-OBS"] = mjd
    if with_wave:
        # Synthetic WCS-WAVE: wavelength = 1 + 0.001*x_det + 0.0005*y_det (um), bandwidth 0.03; cutout pixel p -> detector p + offset.
        xs, ys = np.array([1, 500, 1000, 1500, 2040]), np.array([1, 700, 1400, 2040])
        vals = np.zeros((4, 5, 2), dtype=np.float32)
        vals[..., 0] = 1.0 + 0.001 * xs[None, :] + 0.0005 * ys[:, None] + wave_offset
        vals[..., 1] = 0.03
        tab = fits.BinTableHDU.from_columns([
            fits.Column("X", "5J", array=xs[None, :].astype(np.int32)), fits.Column("Y", "4J", array=ys[None, :].astype(np.int32)),
            fits.Column("VALUES", "40E", dim="(2,5,4)", array=vals[None])], name="WCS-WAVE")
        for k, v in {"CRPIX1W": -999.0 - wave_shift_x, "CRPIX2W": -1499.0, "CDELT1W": 1, "CDELT2W": 1, "CRVAL1W": 1, "CRVAL2W": 1}.items():
            im.header[k] = v
        hdus.append(tab)
    fits.HDUList(hdus).writeto(path, overwrite=True)
    return path
