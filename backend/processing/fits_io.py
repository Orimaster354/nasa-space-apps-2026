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
    warnings: list[str] = field(default_factory=list)


def _hdu(h: fits.HDUList, name: str):
    try:
        return h[name]
    except KeyError:
        return None


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
            warnings=warnings,
        )
