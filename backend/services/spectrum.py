"""Per-position spectrum built from many SPHEREx observations (contract operation 10, SpectralData).

SPHEREx sees one wavelength per object per exposure: the object lands at a different wavelength on each pointing, so a spectrum is
assembled by measuring the same position in many observations and plotting flux against the wavelength at the object's pixel.

Method, per observation: aperture photometry at the object's position (moved along its catalogue proper motion for that date when the
object is one of the curated targets), flux density in mJy, local background ring, no aperture correction. The wavelength comes from
the file's WCS-WAVE table (wavelength at the object's pixel).

Bands: the mission has 102 channels, 17 per detector (D1 to D6). The archive does not state a band number, so it is ESTIMATED: each
detector's wavelength range (from the archive) is split into 17 channels equally spaced in log wavelength. Checked against the data:
the measured per-pixel bandwidth is 2.4 to 2.5 percent of the wavelength in D3, which is what 17 log-spaced channels give (2.4
percent). The estimate is stated in the result's warnings.

One point per band: when several observations fall in one band the one with the best signal-to-noise is shown (not an average, which
would mix dates). Bands of a covered detector with no usable measurement become gap points with a reason. Detectors that no processed
observation belongs to are not listed (the warnings say so).

Time budget: frames whose cutouts are already stored are always used (fast); uncached ones are downloaded only while the budget
lasts, a few per detector, spread over time, so one request never turns into hundreds of archive downloads.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from processing import imaging, irsa, photometry
from processing.fits_io import FitsReadError

from . import targets

CHANNELS_PER_DETECTOR = 17
EXPECTED_BANDS = 6 * CHANNELS_PER_DETECTOR          # 102, from the mission description
DEFAULT_RADIUS_ARCSEC = 12.0
MAX_UNCACHED_PER_DETECTOR = 8
MAX_FRAMES_PER_DETECTOR = 60
DOWNLOAD_BUDGET_S = 25.0
WORKERS = 4
ARCHIVE_FAILURES_TO_GIVE_UP = 3       # consecutive archive errors after which uncached frames are skipped (the archive is down: stop waiting)


class _Archive:
    """Shared across the worker threads of one spectrum: counts consecutive archive failures."""

    def __init__(self):
        self.fails = 0
        self._lock = threading.Lock()

    def failed(self) -> None:
        with self._lock:
            self.fails += 1

    def worked(self) -> None:
        with self._lock:
            self.fails = 0

    @property
    def down(self) -> bool:
        return self.fails >= ARCHIVE_FAILURES_TO_GIVE_UP


def spectrum_id(object_id: str, ra: float | None, dec: float | None, radius: float) -> str:
    key = f"{object_id}|{'obj' if ra is None else f'{ra:.6f},{dec:.6f}'}|{radius:.3f}"
    return "spec_" + hashlib.sha1(key.encode()).hexdigest()[:10]


def channel_edges(lo_um: float, hi_um: float) -> list[float]:
    """The 18 edges of a detector's 17 log-spaced channels."""
    r = (hi_um / lo_um) ** (1.0 / CHANNELS_PER_DETECTOR)
    return [lo_um * r ** k for k in range(CHANNELS_PER_DETECTOR + 1)]


def band_of(detector: int, wavelength_um: float, lo_um: float, hi_um: float) -> tuple[int, float, float]:
    """(1-based band number 1..102, channel lower edge, channel upper edge) for a wavelength inside detector `detector`'s range."""
    edges = channel_edges(lo_um, hi_um)
    k = int(CHANNELS_PER_DETECTOR * math.log(wavelength_um / lo_um) / math.log(hi_um / lo_um))
    k = min(max(k, 0), CHANNELS_PER_DETECTOR - 1)
    return (detector - 1) * CHANNELS_PER_DETECTOR + k + 1, edges[k], edges[k + 1]


def detector_number(bandpass: str | None) -> int | None:
    try:
        return int(bandpass.rsplit("D", 1)[-1]) if bandpass and "D" in bandpass else None
    except ValueError:
        return None


@dataclass
class FrameResult:
    obs: dict                        # the stored observation record
    detector: int
    lo_um: float                     # the detector's whole wavelength range
    hi_um: float
    wavelength_um: float | None = None
    bandwidth_um: float | None = None
    flux_mjy: float | None = None
    sigma_mjy: float | None = None
    snr: float | None = None
    flags: list | None = None
    basis: str | None = None
    reason: str | None = None        # why no flux (unmeasurable), when wavelength is still known
    skipped: str | None = None       # not processed at all (budget, archive error)


def position_for(obj: dict, year: float, ra: float | None, dec: float | None) -> tuple[float, float]:
    """Explicit coordinates are used as given; a curated target is carried to the frame's date along its catalogue proper motion."""
    if ra is not None:
        return ra, dec
    t = targets.find(obj.get("name"))
    if t is not None:
        return targets.position_at(t, year)
    c = obj["coordinates"]
    return c["raDeg"], c["decDeg"]


def _plan(svc, obj_id: str, files) -> dict[int, list[dict]]:
    """Group the object's stored observation records by detector, and choose which to process."""
    from .core import observation_id_for
    by_det: dict[int, list[dict]] = defaultdict(list)
    recs_by_id = svc.registry.get_observations([observation_id_for(f.uri) for f in files])
    for f in files:
        rec = recs_by_id.get(observation_id_for(f.uri))
        if not rec:
            continue
        d = detector_number(rec["observation"].get("bandpass"))
        wr = rec["observation"].get("wavelengthRangeUm")
        if d and wr and 1 <= d <= 6:
            by_det[d].append(rec)
    chosen: dict[int, list[dict]] = {}
    for d, recs in by_det.items():
        recs.sort(key=lambda r: r["observation"].get("observedAt", ""))
        grid_cache = {}

        def cached(rec):
            o = svc.registry.get_object(obj_id)
            c = o.get("viewCenter") or o["coordinates"]
            g = grid_cache.setdefault("g", imaging.GridSpec(c["raDeg"], c["decDeg"], imaging.DEFAULT_FOV_ARCMIN))
            return svc.cutouts.is_cached(irsa.cutout_url(rec["file"]["uri"], g.center_ra, g.center_dec, g.cutout_size_deg))

        hot = [r for r in recs if cached(r)][:MAX_FRAMES_PER_DETECTOR]
        cold = [r for r in recs if r not in hot]
        if cold and len(hot) < MAX_FRAMES_PER_DETECTOR:
            n = min(MAX_UNCACHED_PER_DETECTOR, MAX_FRAMES_PER_DETECTOR - len(hot))
            step = max(1, len(cold) // n)
            cold = cold[::step][:n]
        else:
            cold = []
        chosen[d] = hot + cold
    return chosen


def _is_cached(svc, obj: dict, rec: dict) -> bool:
    o = svc.registry.get_object(obj["objectId"])
    c = o.get("viewCenter") or o["coordinates"]
    g = imaging.GridSpec(c["raDeg"], c["decDeg"], imaging.DEFAULT_FOV_ARCMIN)
    return svc.cutouts.is_cached(irsa.cutout_url(rec["file"]["uri"], g.center_ra, g.center_dec, g.cutout_size_deg))


def _measure(svc, rec: dict, d: int, obj: dict, ra, dec, radius: float, deadline: float, archive: _Archive) -> FrameResult:
    obs = rec["observation"]
    wr = obs["wavelengthRangeUm"]
    fr = FrameResult(rec, d, wr["min"], wr["max"])
    year = 2000.0 + ((_mjd(obs) or 60800.0) - 51544.5) / 365.25
    pra, pdec = position_for(obj, year, ra, dec)
    try:
        o = svc.registry.get_object(obj["objectId"])
        c = o.get("viewCenter") or o["coordinates"]
        g = imaging.GridSpec(c["raDeg"], c["decDeg"], imaging.DEFAULT_FOV_ARCMIN)
        url = irsa.cutout_url(rec["file"]["uri"], g.center_ra, g.center_dec, g.cutout_size_deg)
        if not svc.cutouts.is_cached(url):
            if time.monotonic() >= deadline:
                fr.skipped = "not processed: the time budget for archive downloads ran out"
                return fr
            if archive.down:
                fr.skipped = "not processed: the SPHEREx archive is not responding"
                return fr
        cutout = svc._cutout_for_measurement(rec, pra, pdec, 3 * radius, quick=True)
        x, y = photometry.sky_to_pixel(cutout, pra, pdec)
        fr.wavelength_um, fr.bandwidth_um = photometry.wave_at_pixel(cutout, x, y)
        res = photometry.aperture_flux(cutout, pra, pdec, radius_arcsec=radius, annulus_arcsec=(2 * radius, 3 * radius))
    except photometry.Unmeasurable as exc:
        fr.reason = str(exc)
        return fr
    except (irsa.IrsaError, FitsReadError) as exc:
        if isinstance(exc, irsa.IrsaError):
            archive.failed()
        fr.skipped = f"not processed: {exc}"
        return fr
    archive.worked()
    fr.flux_mjy, fr.sigma_mjy, fr.snr, fr.flags, fr.basis = res.flux_mjy, res.uncertainty_mjy, res.snr, res.quality_flags, res.uncertainty_basis
    fr.wavelength_um = res.wavelength_um if res.wavelength_um is not None else fr.wavelength_um
    return fr


def _mjd(obs: dict) -> float | None:
    from .core import iso_to_mjd
    return iso_to_mjd(obs["observedAt"]) if obs.get("observedAt") else None


def _sig(v: float, digits: int) -> float:
    return float(f"{v:.{digits}g}")


def build(svc, obj: dict, ra: float | None, dec: float | None, radius: float, sid: str, now_iso) -> tuple[dict, bool]:
    """Returns (SpectralData, partial). `partial` is True when some frames were skipped (time budget or archive trouble)."""
    files, archive_note = svc._discover_cached(obj["coordinates"]["raDeg"], obj["coordinates"]["decDeg"])
    svc.list_observations(obj["objectId"], limit=1)                  # makes sure every observation record is stored
    plan = _plan(svc, obj["objectId"], files)
    deadline = time.monotonic() + DOWNLOAD_BUDGET_S
    jobs = [(rec, d) for d, recs in plan.items() for rec in recs]
    hot = [j for j in jobs if _is_cached(svc, obj, j[0])]
    cold = [j for j in jobs if j not in hot]
    archive = _Archive()
    run = lambda j: _measure(svc, j[0], j[1], obj, ra, dec, radius, deadline, archive)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        frames = list(pool.map(run, hot))                                # stored frames first: fast, and a failing archive cannot hold them up
        if cold:
            probe = run(cold[0])                                         # one download on its own: if the archive is down, learn that once, not four times
            frames.append(probe)
            if probe.skipped and "archive" in probe.skipped.lower() and "could not be reached" in probe.skipped:
                archive.fails = ARCHIVE_FAILURES_TO_GIVE_UP
            frames += list(pool.map(run, cold[1:]))

    position = {"raDeg": ra, "decDeg": dec} if ra is not None else (
        {"raDeg": targets.view_center(t)[0], "decDeg": targets.view_center(t)[1]} if (t := targets.find(obj.get("name"))) else obj["coordinates"])
    base = {"spectrumId": sid, "objectId": obj["objectId"], "position": position, "apertureRadiusArcsec": radius,
            "wavelengthUnit": "um", "fluxUnit": photometry.FLUX_UNIT, "expectedBandCount": EXPECTED_BANDS, "generatedAt": now_iso()}
    method = (f"Aperture photometry on each SPHEREx observation: {radius:g} arcsec radius, local background ring, flux density in {photometry.FLUX_UNIT}, "
              "no aperture correction, at the wavelength where the object falls on the detector. One point per band: the measurement with the best signal-to-noise")
    if not frames:
        return ({**base, "availability": {"status": "unavailable", "statusReason": "No SPHEREx observations with a known wavelength range cover this position."},
                 "extractionMethod": method, "points": [], "warnings": [archive_note] if archive_note else []}, False)

    points, used_dets = [], sorted(plan)
    measured = [f for f in frames if f.flux_mjy is not None]
    for d in used_dets:
        fr_d = [f for f in frames if f.detector == d]
        lo, hi = fr_d[0].lo_um, fr_d[0].hi_um
        edges = channel_edges(lo, hi)
        best: dict[int, FrameResult] = {}
        reasons: dict[int, str] = {}
        for f in fr_d:
            if f.wavelength_um is None or not (lo <= f.wavelength_um <= hi):
                continue
            band, _, _ = band_of(d, f.wavelength_um, lo, hi)
            if f.flux_mjy is not None:
                if band not in best or abs(f.snr) > abs(best[band].snr):
                    best[band] = f
            elif f.reason:
                reasons.setdefault(band, f.reason)
        for k in range(CHANNELS_PER_DETECTOR):
            band = (d - 1) * CHANNELS_PER_DETECTOR + k + 1
            e0, e1 = edges[k], edges[k + 1]
            f = best.get(band)
            if f is not None:
                obs = f.obs["observation"]
                pt = {"wavelengthUm": round(f.wavelength_um, 4), "wavelengthMinUm": round(e0, 4), "wavelengthMaxUm": round(e1, 4), "bandIndex": band,
                      "available": True, "flux": _sig(f.flux_mjy, 5), "fluxUncertainty": _sig(f.sigma_mjy, 3), "qualityFlags": f.flags or [],
                      "observationId": obs["observationId"]}
                if obs.get("observedAt"):
                    pt["observedAt"] = obs["observedAt"]
            else:
                why = reasons.get(band) or "No processed observation of this position falls in this band (the number of frames processed is limited)."
                pt = {"wavelengthUm": round(math.sqrt(e0 * e1), 4), "wavelengthMinUm": round(e0, 4), "wavelengthMaxUm": round(e1, 4), "bandIndex": band,
                      "available": False, "unavailableReason": why, "qualityFlags": []}
            points.append(pt)
    points.sort(key=lambda p: p["wavelengthUm"])

    got = [p for p in points if p["available"]]
    dates = sorted(p["observedAt"] for p in got if p.get("observedAt"))
    warnings = []
    if not got:
        return ({**base, "availability": {"status": "unavailable", "statusReason": "None of the processed observations could be measured at this position "
                                          "(for example the aperture is masked, as for very bright stars)."},
                 "extractionMethod": method, "points": points, "warnings": ["Every point is a gap; see each point's reason."]}, bool(any(f.skipped for f in frames)))
    if dates:
        warnings.append(f"Points come from different observation dates ({dates[0][:10]} to {dates[-1][:10]}); source variability is not corrected.")
    warnings.append(f"Band numbers are estimated: each detector's range is split into {CHANNELS_PER_DETECTOR} channels equally spaced in log wavelength "
                    "(this matches the measured bandwidth); the archive does not give band numbers.")
    missing = [f"D{d}" for d in range(1, 7) if d not in plan]
    warnings.append(f"Detectors covered: {', '.join(f'D{d}' for d in used_dets)} ({len(got)} of {len(used_dets) * CHANNELS_PER_DETECTOR} bands in them have a "
                    f"measurement; {EXPECTED_BANDS} bands exist in all)." + (f" No frames were available for {', '.join(missing)}." if missing else ""))
    skipped = [f for f in frames if f.skipped]
    unmeas = [f for f in frames if f.reason]
    by_budget = [f for f in skipped if "time budget" in f.skipped]                                   # classified first: its text also says "archive"
    by_archive = [f for f in skipped if f not in by_budget and "archive" in f.skipped.lower()]
    other = len(skipped) - len(by_archive) - len(by_budget)
    why = [t for t in ((f"{len(by_archive)} because the SPHEREx archive did not respond" if by_archive else ""),
                       (f"{len(by_budget)} because the time budget for archive downloads ran out" if by_budget else ""),
                       (f"{other} for other reasons" if other else "")) if t]
    warnings.append(f"{len(measured)} of {len(frames)} processed observations gave a measurement"
                    + (f"; {len(unmeas)} could not be measured (masked or off the image)" if unmeas else "")
                    + (f"; {len(skipped)} were not processed: {', '.join(why)}" if skipped else "") + ".")
    if ra is None and targets.find(obj.get("name")) is not None:
        warnings.append("The position was carried along the object's catalogue proper motion to each observation's date.")
    warnings.append("The flux scale is approximate: no aperture correction is applied and the telescope's blur changes with wavelength, so the spectrum's "
                    "overall shape is only as good as that assumption.")
    if archive_note:
        warnings.append(archive_note)
    basis = next((f.basis for f in measured if f.basis), None)
    return ({**base, "availability": {"status": "available"}, "extractionMethod": method, **({"uncertaintyBasis": basis} if basis else {}),
             "points": points, "warnings": warnings}, bool(skipped))
