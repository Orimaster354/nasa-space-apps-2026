"""Decide whether two observations may be compared, and describe how (contract ComparisonResult.compatibility).

Rules follow docs/SCIENTIFIC_DATA_PIPELINE.md §5. Thresholds are OUR heuristics, stated here and tested; they are not NASA's.
`notEvaluated` is never turned into `pass`: whatever we cannot verify stays visible to the user.

Pure functions: no I/O, no web layer.
"""
from __future__ import annotations

from dataclasses import dataclass

# Relative difference of the modelled zodiacal-light level above which we warn that the BACKGROUND, not the sources, may differ.
ZODI_WARN_FRACTION = 0.05


@dataclass(frozen=True)
class Side:
    """What we know about one observation going into a comparison."""

    bandpass: str | None
    release: str | None
    quality: str                 # good | degraded | poor | unknown
    zodi_median: float | None    # median of the ZODI plane (MJy/sr), if the file has one
    wavelength_um: float | None = None   # wavelength at the OBJECT's pixel (WCS-WAVE), not the whole band
    bandwidth_um: float | None = None    # bandwidth at that pixel


def comparison_kind(base: Side, target: Side) -> str:
    """"same_band_epochs" (a temporal comparison) or "cross_band" (a spectral comparison, NOT a time change)."""
    return "same_band_epochs" if base.bandpass and base.bandpass == target.bandpass else "cross_band"


def _check(name: str, result: str, detail: str) -> dict:
    return {"name": name, "result": result, "detail": detail}


def _wavelength_within_band(base: Side, target: Side) -> dict:
    """Same bandpass is NOT the same wavelength: each SPHEREx band spans a range and the object lands at a different wavelength
    on each pointing (measured on real data: a star's flux fell 45% across one band as its wavelength moved from 1.64 to 2.40 um).
    We call the wavelengths matched when they differ by no more than one bandwidth. That threshold is our heuristic."""
    head = f"Both use the same bandpass ({base.bandpass})."
    if base.wavelength_um is None or target.wavelength_um is None:
        return _check("wavelength", "notEvaluated", head + " The wavelength at the object was not available for both frames, so whether they match within the band is unknown.")
    bw = max(b for b in (base.bandwidth_um, target.bandwidth_um) if b is not None) if (base.bandwidth_um or target.bandwidth_um) else None
    delta = abs(target.wavelength_um - base.wavelength_um)
    where = f"{base.wavelength_um:.3f} um and {target.wavelength_um:.3f} um"
    if bw is None:
        return _check("wavelength", "notEvaluated", head + f" The object falls at {where}; the bandwidth was not available to judge the difference.")
    if delta <= bw:
        return _check("wavelength", "pass", head + f" The object falls at {where}, within one bandwidth ({bw:.3f} um).")
    return _check("wavelength", "warn", head + f" But the object falls at different wavelengths in the two frames ({where}; bandwidth about {bw:.3f} um). "
                  "A brightness difference may be spectral (the source's flux changes with wavelength), not a change over time.")


def assess(base: Side, target: Side, mode: str) -> dict:
    """Returns {"verdict", "checks", "comparisonKind", "warnings"} for the contract."""
    kind = comparison_kind(base, target)
    checks = [_check("footprint", "pass", "Both observations were found covering this position.")]

    if kind == "same_band_epochs":
        checks.append(_wavelength_within_band(base, target))
    elif mode == "difference":
        checks.append(_check("wavelength", "fail", "The observations use different bandpasses; subtracting them would compare different wavelengths, not change over time."))
    else:
        checks.append(_check("wavelength", "warn", "The observations use different bandpasses. This shows a spectral difference, not a change over time."))

    if base.release and target.release and base.release != target.release:
        checks.append(_check("dataRelease", "warn", f"Different data releases ({base.release} and {target.release})."))
        checks.append(_check("calibration", "warn", "Different releases; flux calibration may not be equivalent."))
    else:
        checks.append(_check("dataRelease", "pass", "Same data release."))
        checks.append(_check("calibration", "notEvaluated", "Calibration equivalence between these observations was not independently verified."))

    checks.append(_check("alignment", "notEvaluated", "Each image was resampled onto a common sky grid using its own WCS; the alignment residual was not measured."))

    worst = _worse(base.quality, target.quality)
    if worst == "good":
        checks.append(_check("quality", "pass", "Both observations have few unusable pixels."))
    elif worst == "unknown":
        checks.append(_check("quality", "notEvaluated", "Pixel quality was not available for at least one observation."))
    else:
        checks.append(_check("quality", "warn", f"At least one observation is {worst} (many pixels are flagged unusable)."))

    zb, zt = base.zodi_median, target.zodi_median
    if zb is None or zt is None or (zb + zt) == 0:
        checks.append(_check("background", "notEvaluated", "The modelled zodiacal-light background was not available for both observations."))
    else:
        frac = abs(zt - zb) / ((abs(zb) + abs(zt)) / 2)
        if frac > ZODI_WARN_FRACTION:
            checks.append(_check("background", "warn", f"The modelled zodiacal-light background differs by {frac * 100:.0f}%; part of any difference may be background, not sources."))
        else:
            checks.append(_check("background", "pass", f"The modelled zodiacal-light background differs by only {frac * 100:.1f}%."))

    results = {c["result"] for c in checks}
    if "fail" in results:
        verdict = "not_comparable"
    elif results == {"pass"}:
        verdict = "comparable"
    else:
        verdict = "comparable_with_caveats"

    warnings = []
    if mode == "difference" and verdict != "not_comparable":
        warnings.append("A difference image shows noise, alignment error, calibration and background differences as well as real change. Treat it as a pointer for follow-up, not a detection.")
    if kind == "same_band_epochs" and any(c["name"] == "wavelength" and c["result"] == "warn" for c in checks):
        warnings.append("The two frames sample different wavelengths at this position: brightness differences are not a clean time change. Positional changes are still visible.")
    if kind == "cross_band":
        warnings.append("These frames are different wavelengths: differences are spectral, not changes over time.")
    return {"verdict": verdict, "checks": checks, "comparisonKind": kind, "warnings": warnings}


_ORDER = {"good": 0, "unknown": 1, "degraded": 2, "poor": 3}


def _worse(a: str, b: str) -> str:
    return a if _ORDER.get(a, 1) >= _ORDER.get(b, 1) else b
