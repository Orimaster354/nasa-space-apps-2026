"""Read a SPHEREx cutout MEF defensively. Structure verified on a real cutout (backend/scripts/PROBE_FINDINGS.md):
HDUs IMAGE, FLAGS, VARIANCE, ZODI (+ EPSF, WCS-WAVE we do not need yet). IMAGE is MJy/sr and NOT zodi-subtracted.
Observation-time keywords were found in the VARIANCE/ZODI headers, not in IMAGE. Missing pieces are reported, never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.interpolate import RegularGridInterpolator


class FitsReadError(Exception):
    pass


@dataclass
class Cutout:
    image: np.ndarray                       # MJy/sr, as delivered
    wcs: WCS                                # celestial WCS of IMAGE (TAN-SIP in the real data)
    unit: str | None
    flags: np.ndarray | None = None
    variance: np.ndarray | None = None      # (MJy/sr)^2
    zodi: np.ndarray | None = None
    detector: str | None = None
    obsid: str | None = None
    mjd_obs: float | None = None            # from VARIANCE/ZODI header when present
    wavelength_um: np.ndarray | None = None # per-pixel wavelength, from the WCS-WAVE table (approximate, see _wave_maps)
    bandwidth_um: np.ndarray | None = None  # per-pixel bandwidth
    warnings: list[str] = field(default_factory=list)


def _hdu(h: fits.HDUList, name: str):
    try:
        return h[name]
    except KeyError:
        return None


def _wave_maps(h: fits.HDUList, header, shape: tuple[int, int]):
    """Per-pixel wavelength and bandwidth (micrometres) from the WCS-WAVE table, or (None, None, warning).

    Structure verified on a real cutout: WCS-WAVE has one row with X (detector pixel nodes), Y (nodes) and VALUES of shape
    (len(Y), len(X), 2) = [wavelength, bandwidth] at each node. IMAGE header keys CRPIX{1,2}W, CDELT{1,2}W, CRVAL{1,2}W map a
    1-based cutout pixel p to a position in the node coordinates: u = (p - CRPIX) * CDELT + CRVAL. The header calls this
    "Approximated per-pixel wavelength and bandwidth", so values are interpolated (bilinear), not exact per pixel.
    """
    hdu = _hdu(h, "WCS-WAVE")
    if hdu is None or hdu.data is None or len(hdu.data) == 0:
        return None, None, "no WCS-WAVE table, so the wavelength at each pixel is unknown"
    try:
        xs = np.asarray(hdu.data["X"][0], dtype=float)
        ys = np.asarray(hdu.data["Y"][0], dtype=float)
        vals = np.asarray(hdu.data["VALUES"][0], dtype=float)
        if vals.shape != (len(ys), len(xs), 2) or np.any(np.diff(xs) <= 0) or np.any(np.diff(ys) <= 0):
            raise ValueError("unexpected WCS-WAVE table layout")
        cr1, cr2 = float(header["CRPIX1W"]), float(header["CRPIX2W"])
        cd1, cd2 = float(header.get("CDELT1W", 1.0)), float(header.get("CDELT2W", 1.0))
        cv1, cv2 = float(header.get("CRVAL1W", 1.0)), float(header.get("CRVAL2W", 1.0))
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        return None, None, f"the WCS-WAVE table could not be interpreted ({exc}), so the wavelength at each pixel is unknown"
    ny, nx = shape
    u = (np.arange(1, nx + 1, dtype=float) - cr1) * cd1 + cv1
    v = (np.arange(1, ny + 1, dtype=float) - cr2) * cd2 + cv2
    vv, uu = np.meshgrid(v, u, indexing="ij")
    out = RegularGridInterpolator((ys, xs), vals, bounds_error=False, fill_value=np.nan)(np.stack([vv, uu], axis=-1))
    return out[..., 0].astype(np.float32), out[..., 1].astype(np.float32), None


def read_cutout(path: str | Path) -> Cutout:
    try:
        h = fits.open(path, memmap=False)
    except Exception as exc:
        raise FitsReadError(f"cannot open FITS file: {exc}") from exc
    with h:
        im = _hdu(h, "IMAGE")
        if im is None or im.data is None or im.data.ndim != 2:
            raise FitsReadError("file has no 2D IMAGE extension")
        warnings: list[str] = []
        wcs = WCS(im.header)
        if not wcs.has_celestial:
            raise FitsReadError("IMAGE extension has no celestial WCS")

        def plane(name: str):
            hdu = _hdu(h, name)
            if hdu is None or hdu.data is None or hdu.data.shape != im.data.shape:
                warnings.append(f"{name} extension missing or a different shape")
                return None
            return np.array(hdu.data)

        mjd = None
        for name in ("VARIANCE", "ZODI", "IMAGE"):
            hdu = _hdu(h, name)
            if hdu is not None and hdu.header.get("MJD-OBS") is not None:
                mjd = float(hdu.header["MJD-OBS"])
                break
        if mjd is None:
            warnings.append("no MJD-OBS keyword found in the file")

        wave, bw, wave_warning = _wave_maps(h, im.header, im.data.shape)
        if wave_warning:
            warnings.append(wave_warning)
        det = im.header.get("DETECTOR")
        return Cutout(
            image=np.array(im.data, dtype=np.float32),
            wcs=wcs,
            unit=im.header.get("BUNIT"),
            flags=plane("FLAGS"),
            variance=plane("VARIANCE"),
            zodi=plane("ZODI"),
            detector=None if det is None else str(det),
            obsid=im.header.get("OBSID"),
            mjd_obs=mjd,
            wavelength_um=wave,
            bandwidth_um=bw,
            warnings=warnings,
        )
