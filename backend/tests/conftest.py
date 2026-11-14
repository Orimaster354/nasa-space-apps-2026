"""Shared synthetic data: a SPHEREx-like cutout MEF (IMAGE, FLAGS, VARIANCE, ZODI) with a Gaussian star at a known sky position."""
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

CENTER = (133.795129, -7.245147)       # WISE 0855, used only as a convenient sky position


def make_cutout_fits(path: Path, star_ra: float, star_dec: float, rotation_deg: float = 0.0, n: int = 29,
                     scale_arcsec: float = 6.2, peak: float = 100.0, mjd: float = 60800.7, detector: int = 3,
                     flag_value: int = 0, center=CENTER) -> Path:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(center)
    w.wcs.crpix = [(n + 1) / 2, (n + 1) / 2]
    s = scale_arcsec / 3600.0
    t = np.deg2rad(rotation_deg)
    w.wcs.cd = [[-s * np.cos(t), s * np.sin(t)], [s * np.sin(t), s * np.cos(t)]]
    w.wcs.cunit = ["deg", "deg"]

    x0, y0 = w.wcs_world2pix([[star_ra, star_dec]], 0)[0]
    yy, xx = np.mgrid[0:n, 0:n]
    image = (peak * np.exp(-((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * 1.2 ** 2)) + 1.0).astype(np.float32)
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
    fits.HDUList(hdus).writeto(path, overwrite=True)
    return path
