"""Common pixel grid, reprojection and display rendering.

Contract (v0.2.0, "Common pixel grid"): every image requested with the same field-of-view parameters is resampled onto one
north-up TAN grid centred on (centerRa, centerDec) with a fixed pixel scale, and carries the same `gridId`.
Raster pixel (x, y) has its origin top-left (contract §5), so FITS row order is flipped when rendering.

Rendering is for VIEWING only. Measurements must never be taken from the PNG.
"""
from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass

import numpy as np
from astropy.wcs import WCS
from PIL import Image
from reproject import reproject_interp

from . import quality
from .fits_io import Cutout

PIXEL_SCALE_ARCSEC = 2.0            # output grid scale (the archive's native scale is about 6.2 arcsec/pixel)
MAX_FOV_ARCMIN = 30.0               # IRSA cutouts are limited to 1 degree; 30 arcmin plus margin stays under it
MARGIN = 1.5                        # request a larger cutout than the grid so rotated pointings still cover the corners

STRETCHES = ("linear", "log", "sqrt", "asinh")


@dataclass(frozen=True)
class GridSpec:
    center_ra: float
    center_dec: float
    fov_arcmin: float
    scale_arcsec: float = PIXEL_SCALE_ARCSEC

    def __post_init__(self):
        if not (0 < self.fov_arcmin <= MAX_FOV_ARCMIN):
            raise ValueError(f"fovArcmin must be in (0, {MAX_FOV_ARCMIN}]")

    @property
    def npix(self) -> int:
        return max(8, int(round(self.fov_arcmin * 60.0 / self.scale_arcsec)))

    @property
    def grid_id(self) -> str:
        key = f"{self.center_ra:.6f},{self.center_dec:.6f},{self.fov_arcmin:.4f},{self.scale_arcsec:.4f}"
        return "g_" + hashlib.sha1(key.encode()).hexdigest()[:10]

    @property
    def cutout_size_deg(self) -> float:
        return min(1.0, self.fov_arcmin / 60.0 * MARGIN)

    def wcs(self) -> WCS:
        n = self.npix
        w = WCS(naxis=2)
        w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
        w.wcs.crval = [self.center_ra, self.center_dec]
        w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
        s = self.scale_arcsec / 3600.0
        w.wcs.cdelt = [-s, s]
        w.wcs.cunit = ["deg", "deg"]
        return w

    def raster_to_sky(self, x: float, y: float) -> tuple[float, float]:
        """Raster pixel (origin top-left, 0-based) -> (RA, Dec) in degrees."""
        y_fits = (self.npix - 1) - y
        ra, dec = self.wcs().wcs_pix2world([[x, y_fits]], 0)[0]
        return float(ra % 360.0), float(dec)

    def sky_to_raster(self, ra: float, dec: float) -> tuple[float, float]:
        x, y_fits = self.wcs().wcs_world2pix([[ra, dec]], 0)[0]
        return float(x), float((self.npix - 1) - y_fits)

    def wcs_summary(self) -> dict:
        """Contract WcsSummary for the served raster. Exact TAN, so `approximate` is false."""
        n = self.npix
        s = self.scale_arcsec / 3600.0
        return {
            "ctype1": "RA---TAN", "ctype2": "DEC--TAN",
            "crval": [self.center_ra, self.center_dec],
            "crpix": [(n + 1) / 2.0, (n + 1) / 2.0],
            "cd": [[-s, 0.0], [0.0, s]],
            "pixelScaleArcsec": self.scale_arcsec,
            "approximate": False,
        }


def to_grid(cutout: Cutout, grid: GridSpec) -> np.ndarray:
    """Resample the cutout IMAGE onto the grid (FITS orientation, y up). Unusable pixels become NaN first."""
    data = cutout.image.astype(np.float64)
    if cutout.flags is not None:
        data = np.where(quality.unusable(cutout.flags), np.nan, data)
    n = grid.npix
    out, _ = reproject_interp((data, cutout.wcs), grid.wcs(), shape_out=(n, n))
    return out


def render_png(arr: np.ndarray, stretch: str = "asinh", low_pct: float = 1.0, high_pct: float = 99.5) -> tuple[bytes, dict]:
    """8-bit grayscale PNG (origin top-left) and the stretch description required by the contract."""
    if stretch not in STRETCHES:
        raise ValueError(f"stretch must be one of {STRETCHES}")
    if not (0 <= low_pct < high_pct <= 100):
        raise ValueError("percentiles must satisfy 0 <= low < high <= 100")
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        lo = hi = 0.0
        norm = np.zeros(arr.shape)
    else:
        lo, hi = (float(v) for v in np.percentile(finite, [low_pct, high_pct]))
        if hi <= lo:
            hi = lo + 1e-12
        norm = np.clip((np.nan_to_num(arr, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
        if stretch == "asinh":
            norm = np.arcsinh(10 * norm) / math.asinh(10)
        elif stretch == "sqrt":
            norm = np.sqrt(norm)
        elif stretch == "log":
            norm = np.log10(1 + 999 * norm) / 3.0
    img = Image.fromarray((np.flipud(norm) * 255).astype(np.uint8), mode="L")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), {"mode": stretch, "lowValue": lo, "highValue": hi, "unit": "MJy/sr"}
