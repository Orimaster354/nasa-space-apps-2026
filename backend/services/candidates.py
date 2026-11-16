"""Candidates: sources worth a closer look because they MOVE or CHANGE in brightness across SPHEREx observations (contract operation 19).

A candidate is a ranking for human follow-up. It is never a detection, never validated science, and never described as a probability.

Strategy (P1-02): CATALOGUE-SEEDED. For a curated target we know where it should be at each date (catalogue position plus proper
motion). We find the source near that place in each frame, track it relative to the surrounding stars (processing/astrometry.py), and
test two things:
  * MOTION: a straight-line displacement over time, validated against stated thresholds (otherwise labelled "unvalidated displacement");
  * BRIGHTNESS: flux differences between frames that sit at the SAME wavelength (frames at different wavelengths are never compared).
A target where neither stands out gets NO candidate, and the page says so: absence is an honest result.
Objects that are not in the curated table have no seed, so they get no candidates (automated search of the whole field is future work).

Ranking (P1-08): score = min(1, max(motion_term, brightness_term) + bonus) x quality_factor, rounded to 3 places; see SCORE_BASIS.
It is a heuristic for ordering, not a probability, and nothing here is called probability or confidence.

The catalogue proper motion is shown as `properMotion` (provenance "catalog"); what WE measure is `motion` and its method says so.
They are never mixed (P1-06).
"""
from __future__ import annotations

import hashlib
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from processing import astrometry, imaging, irsa, photometry
from processing.fits_io import FitsReadError, read_cutout

from processing import compare

from . import spectrum as spectrum_mod
from . import targets

VERSION = "motion-brightness-v1"
FRAME_LIMIT = 60                       # frames used per target
MAX_TRACK_POINTS = 12                  # points returned in a candidate's track
MIN_PAIR_DAYS = 60.0                   # brightness pairs need frames at least this far apart in time
BRIGHT_MEDIAN_Z = 3.0                  # brightness counts as changing when the median |z| of matched pairs exceeds this ...
BRIGHT_MAX_Z = 5.0                     # ... or the largest one exceeds this, with at least MIN_PAIRS pairs
MIN_PAIRS = 3
MOTION_FOR_FULL_SCORE = 10.0           # motion significance (times its uncertainty) that earns the full motion term
BRIGHT_FOR_FULL_SCORE = 8.0
UNVALIDATED_PENALTY = 0.5
MIN_UNVALIDATED_SIGNIFICANCE = 3.0     # below this an unvalidated displacement is not worth listing at all
DOWNLOAD_BUDGET_S = 25.0
APERTURE_ARCSEC = 12.0
CENTROID_SIGMA_ARCSEC = 0.9            # single-frame position uncertainty: median field-star scatter measured on 40 real Epsilon Indi B frames

SCORE_BASIS = (
    "Heuristic ranking, not a probability. score = min(1, max(motion term, brightness term) + 0.1 if both stand out) x quality factor. "
    f"Motion term: how many times larger the measured motion is than its uncertainty, divided by {MOTION_FOR_FULL_SCORE:g} (halved when the motion "
    f"did not pass validation). Brightness term: the largest flux difference between same-wavelength frames, in units of its uncertainty, divided "
    f"by {BRIGHT_FOR_FULL_SCORE:g}. Quality factor: the share of frames that could be used, between 0.5 and 1."
)

LIMITATIONS = [
    "Positions are measured relative to surrounding stars in SPHEREx's 6 arcsecond pixels over a baseline of months; this is a consistency "
    "check, not an independent proper-motion measurement.",
    "The uncertainties assume independent errors from frame to frame and may be optimistic.",
    "Brightness has no aperture correction and neighbouring sources may contribute to the measured flux.",
    "A candidate is a lead for follow-up, not an established finding.",
]


@dataclass
class FrameData:
    key: str
    year: float
    mjd: float
    arr: np.ndarray
    flux: float | None = None
    sigma: float | None = None
    wl: float | None = None
    bw: float | None = None
    flags: list | None = None


def _compass(west: float, north: float) -> str:
    ns = "north" if north > 0 else "south"
    ew = "west" if west > 0 else "east"
    major_ns = abs(north) >= 2 * abs(west)
    major_ew = abs(west) >= 2 * abs(north)
    return ns if major_ns else ew if major_ew else f"{ns}-{ew}"


def brightness_pairs(points: list[dict]) -> dict:
    """Flux differences between frames at the same wavelength (within compare.WAVELENGTH_MATCH_FRACTION of a bandwidth), at least MIN_PAIR_DAYS apart."""
    zs, members = [], []
    for i, a in enumerate(points):
        for j, b in enumerate(points[i + 1:], start=i + 1):
            bw = max(a["bw"] or 0.0, b["bw"] or 0.0)
            if not bw or abs(b["wl"] - a["wl"]) > compare.WAVELENGTH_MATCH_FRACTION * bw or abs(b["mjd"] - a["mjd"]) < MIN_PAIR_DAYS:
                continue
            sigma = math.sqrt(a["sigma"] ** 2 + b["sigma"] ** 2 + (photometry.SYSTEMATIC_FRACTION * a["flux"]) ** 2 + (photometry.SYSTEMATIC_FRACTION * b["flux"]) ** 2)
            zs.append((b["flux"] - a["flux"]) / sigma)
            members.append((i, j))
    if not zs:
        return {"n": 0, "max_abs_z": 0.0, "median_abs_z": 0.0, "significant": False}
    az = np.abs(zs)
    sig = len(zs) >= MIN_PAIRS and (float(np.median(az)) >= BRIGHT_MEDIAN_Z or _corroborated(az, members))
    return {"n": len(zs), "max_abs_z": float(az.max()), "median_abs_z": float(np.median(az)), "rms_z": float(np.sqrt(np.mean(np.square(zs)))), "significant": bool(sig)}


def _corroborated(az: np.ndarray, members: list[tuple[int, int]]) -> bool:
    """A large difference only counts when it survives dropping the ONE frame involved in the most large differences. A single bad frame (a cosmic-ray
    hit, a neighbouring star in the aperture, a flagged patch) sits in many pairs at once and would otherwise fake a brightness change on its own; real
    variability shows up in pairs that do not share a frame. Found on real data: LHS 1140 had one 23-sigma 'change' made of a few bad frames."""
    if float(az.max()) < BRIGHT_MAX_Z:
        return False
    counts: dict[int, int] = {}
    for k, z in enumerate(az):
        if z >= BRIGHT_MAX_Z:
            for f in members[k]:
                counts[f] = counts.get(f, 0) + 1
    worst = max(counts, key=lambda f: counts[f])
    rest = [float(az[k]) for k, m in enumerate(members) if worst not in m]
    return len(rest) >= MIN_PAIRS and max(rest) >= BRIGHT_MAX_Z


def score(motion_z: float | None, validated: bool, bright: dict, usable: float) -> float:
    m = min(1.0, (motion_z or 0.0) / MOTION_FOR_FULL_SCORE) * (1.0 if validated else UNVALIDATED_PENALTY) if motion_z else 0.0
    b = min(1.0, bright["max_abs_z"] / BRIGHT_FOR_FULL_SCORE) if bright["significant"] else 0.0
    both = 0.1 if (m > 0 and validated and b > 0) else 0.0
    q = min(1.0, max(0.5, usable))
    return round(min(1.0, max(m, b) + both) * q, 3)


def _load(svc, rec: dict, grid: imaging.GridSpec, archive, deadline: float):
    url = irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)
    if not svc.cutouts.is_cached(url):
        if time.monotonic() >= deadline:
            return None, "budget"
        if archive.down:
            return None, "archive"
    try:
        cut = read_cutout(svc.cutouts.get(url, retries=1, timeout_s=15.0))
    except irsa.IrsaError:
        archive.failed()
        return None, "archive"
    except FitsReadError:
        return None, "unreadable"
    archive.worked()
    return cut, None


def _frame(svc, rec: dict, grid, target: targets.Target, archive, deadline: float):
    cut, why = _load(svc, rec, grid, archive, deadline)
    if cut is None or cut.mjd_obs is None:
        return None, why or "no observation time"
    year = 2000.0 + (cut.mjd_obs - 51544.5) / 365.25
    fd = FrameData(rec["observation"]["observationId"], year, cut.mjd_obs, imaging.to_grid(cut, grid))
    ra, dec = targets.position_at(target, year)
    try:
        res = photometry.aperture_flux(cut, ra, dec, radius_arcsec=APERTURE_ARCSEC, annulus_arcsec=(2 * APERTURE_ARCSEC, 3 * APERTURE_ARCSEC))
        if res.wavelength_um is not None and res.bandwidth_um:
            fd.flux, fd.sigma, fd.wl, fd.bw, fd.flags = res.flux_mjy, res.uncertainty_mjy, res.wavelength_um, res.bandwidth_um, res.quality_flags
    except photometry.Unmeasurable:
        pass
    return fd, None


def _frames_for(svc, obj: dict, target: targets.Target, grid) -> tuple[list[FrameData], dict]:
    from .core import observation_id_for
    files, _ = svc._discover_cached(obj["coordinates"]["raDeg"], obj["coordinates"]["decDeg"])
    svc.list_observations(obj["objectId"], limit=1)
    recs_by_id = svc.registry.get_observations([observation_id_for(f.uri) for f in files])
    recs = [r for r in recs_by_id.values() if r["observation"].get("bandpass") == "SPHEREx-D3" and r["observation"].get("observedAt")]
    recs.sort(key=lambda r: r["observation"]["observedAt"])
    hot = [r for r in recs if svc.cutouts.is_cached(irsa.cutout_url(r["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg))][:FRAME_LIMIT]
    cold = [r for r in recs if r not in hot]
    if cold and len(hot) < FRAME_LIMIT:
        step = max(1, len(cold) // 24)
        cold = cold[::step][:min(24, FRAME_LIMIT - len(hot))]
    else:
        cold = []
    if getattr(svc, "candidate_cached_only", False):
        cold = []
    archive = spectrum_mod._Archive()
    deadline = time.monotonic() + DOWNLOAD_BUDGET_S
    run = lambda r: _frame(svc, r, grid, target, archive, deadline)
    skipped = {"archive": 0, "budget": 0, "other": 0}
    out: list[FrameData] = []

    def take(results):
        for fd, why in results:
            if fd is not None:
                out.append(fd)
            else:
                skipped["archive" if why == "archive" else "budget" if why == "budget" else "other"] += 1

    with ThreadPoolExecutor(max_workers=spectrum_mod.WORKERS) as pool:
        take(list(pool.map(run, hot)))
        if cold:
            first = run(cold[0])
            take([first])
            if first[1] == "archive":
                archive.fails = spectrum_mod.ARCHIVE_FAILURES_TO_GIVE_UP
            take(list(pool.map(run, cold[1:])))
    out.sort(key=lambda f: f.mjd)
    return out, {"candidates_considered": len(hot) + len(cold), "skipped": skipped}


def measure_motion(frames: list[FrameData], expected, scale: float):
    rows = astrometry.track([(f.key, f.year, f.arr) for f in frames], expected, scale)
    fit = astrometry.fit_motion(rows)
    return rows, fit


def _motion_measurement(mid: str, obj: dict, fit, why: list[str], rows, grid, target, now_iso) -> dict:
    ra, dec = targets.view_center(target)
    validated = not why
    method = (f"Derived from observations: the target's position relative to {fit.mean_stars:.0f} surrounding stars on average, in {fit.n} frames over "
              f"{fit.span_years:.2f} years, straight-line fit per axis. West {fit.west_rate:+.2f} +- {fit.west_err:.2f}, north {fit.north_rate:+.2f} "
              f"+- {fit.north_err:.2f} arcsec per year; scatter around the line {fit.rms_arcsec:.2f} arcsec.")
    warnings = ([] if validated else ["Unvalidated displacement, not a measured motion: " + "; ".join(why) + "."])
    return {"measurementId": mid, "kind": "derived_motion", "value": fit.total, "unit": "arcsec/yr", "uncertainty": fit.total_err,
            "uncertaintyBasis": "Scatter of the target's displacement around the fitted line, assuming independent errors between frames.",
            "signalToNoise": fit.significance, "observationIds": [r.key for r in rows],
            "region": {"center": {"raDeg": ra, "decDeg": dec}, "radiusArcsec": astrometry.TARGET_MATCH_ARCSEC}, "method": method,
            "qualityFlags": [], "warnings": warnings, "createdAt": now_iso()}


def build(svc, obj: dict, now_iso) -> tuple[list[dict], dict]:
    """Returns (candidates, run_info). run_info has `note` (why there are none, or None) and `partial` (frames skipped for archive reasons)."""
    target = targets.find(obj.get("name"))
    if target is None:
        why = ((svc.registry.get_object(obj["objectId"]) or {}).get("pmLookup") or {}).get("reason")
        return [], {"note": ("Candidates are seeded from a known proper motion, and none could be obtained for this object: " + why) if why else
                    "Candidates are seeded from a known proper motion, and this object is not one of the catalogue targets, so none were generated.", "partial": False}
    rec_obj = svc.registry.get_object(obj["objectId"])
    c = rec_obj.get("viewCenter") or rec_obj["coordinates"]
    grid = imaging.GridSpec(c["raDeg"], c["decDeg"], imaging.DEFAULT_FOV_ARCMIN)
    scale = grid.scale_arcsec
    frames, info = _frames_for(svc, obj, target, grid)
    partial = info["skipped"]["archive"] > 0 or info["skipped"]["budget"] > 0
    if len(frames) < astrometry.MIN_FRAMES:
        return [], {"note": f"Only {len(frames)} usable frames of this target could be loaded, too few to look for motion or change.", "partial": partial}

    wcs = grid.wcs()

    def expected(year):
        ra, dec = targets.position_at(target, year)
        x, y = wcs.wcs_world2pix([[ra, dec]], 0)[0]
        return float(x), float(y)

    rows, fit = measure_motion(frames, expected, scale)
    why = astrometry.validate(fit)
    motion_ok = fit is not None and not why
    motion_listed = fit is not None and (motion_ok or fit.significance >= MIN_UNVALIDATED_SIGNIFICANCE)
    pts = [{"key": f.key, "mjd": f.mjd, "flux": f.flux, "sigma": f.sigma, "wl": f.wl, "bw": f.bw} for f in frames if f.flux is not None]
    bright = brightness_pairs(pts)
    usable = len(rows) / max(1, info["candidates_considered"])

    cat = obj.get("properMotion")
    agree_note = None
    warnings = []
    if motion_listed and cat:
        cw, cn = -cat["pmRaMasPerYr"] / 1000.0, cat["pmDecMasPerYr"] / 1000.0
        zw, zn = abs(fit.west_rate - cw) / max(fit.west_err, 1e-9), abs(fit.north_rate - cn) / max(fit.north_err, 1e-9)
        agree_note = (f"The catalogue gives {cat['value']:.2f} {cat['unit']} ({cat['source']}); the measured total differs from it by "
                      f"{abs(fit.total - cat['value']):.2f} arcsec/yr, and the west and north components differ by {zw:.1f} and {zn:.1f} times their uncertainties.")
        if max(zw, zn) > 2.0:
            warnings.append("The measured components differ from the catalogue by more than 2 times their uncertainties, so the uncertainties may be optimistic.")
    if motion_listed and not motion_ok:
        warnings.append("Unvalidated displacement, not a measured motion: " + "; ".join(why) + ".")
    if bright["n"] >= MIN_PAIRS and not bright["significant"]:
        warnings.append(f"Brightness was compared between {bright['n']} same-wavelength frame pairs and shows no significant change.")
    elif 0 < bright["n"] < MIN_PAIRS:
        warnings.append(f"Only {bright['n']} same-wavelength frame pair(s) exist, too few to judge whether the brightness changes.")
    if info["skipped"]["archive"] or info["skipped"]["budget"]:
        warnings.append(f"{info['skipped']['archive'] + info['skipped']['budget']} frames could not be loaded (archive not responding or time budget).")

    if not (motion_listed or bright["significant"]):
        why_none = ("Nothing stands out for this target: " + ("no significant motion was found " if fit is not None else "motion could not be fitted ")
                    + (f"({fit.total:.2f} +- {fit.total_err:.2f} arcsec/yr from {fit.n} frames) " if fit is not None else "")
                    + ("and its same-wavelength brightness is steady." if bright["n"] >= MIN_PAIRS and not bright["significant"]
                       else f"and there are too few same-wavelength frame pairs ({bright['n']}) to judge its brightness." if bright["n"]
                       else "and brightness could not be compared at matching wavelengths."))
        return [], {"note": why_none, "partial": partial}

    h = hashlib.sha1(f"{obj['objectId']}|{VERSION}|{target.name}".encode()).hexdigest()
    cid, label = "cand_" + h[:10], f"SX-{int(h[:6], 16) % 10000:04d}"
    score_value = score(fit.significance if motion_listed else None, motion_ok, bright, usable)
    kind = "both" if (motion_listed and bright["significant"]) else "positional_change" if motion_listed else "brightness_change"

    evidence, refs = [], []
    if motion_listed:
        mid = "meas_" + hashlib.sha1(f"{cid}|derived_motion".encode()).hexdigest()[:10]
        svc.registry.put_measurement(mid, _motion_measurement(mid, obj, fit, why, rows, grid, target, now_iso))
        refs.append({"targetType": "measurement", "targetId": mid})
        evidence.append(f"Moves {fit.total:.2f} +- {fit.total_err:.2f} arcsec per year relative to the surrounding stars, towards the {_compass(fit.west_rate, fit.north_rate)} "
                        f"({fit.n} frames over {fit.span_years:.2f} years; {fit.significance:.0f} times its uncertainty).")
        if agree_note:
            evidence.append(agree_note)
    if bright["significant"]:
        evidence.append(f"Brightness differs between same-wavelength frames: {bright['n']} pairs, largest difference {bright['max_abs_z']:.1f} times its uncertainty.")
    elif bright["n"] >= MIN_PAIRS:
        evidence.append(f"Brightness is steady between {bright['n']} same-wavelength frame pairs (largest difference {bright['max_abs_z']:.1f} times its uncertainty).")

    chosen = rows if len(rows) <= MAX_TRACK_POINTS else [rows[i] for i in np.linspace(0, len(rows) - 1, MAX_TRACK_POINTS).round().astype(int)]
    track = []
    for r in chosen:
        ra, dec = wcs.wcs_pix2world([[r.raw_x, r.raw_y]], 0)[0]
        track.append({"observationId": r.key, "sky": {"raDeg": float(ra % 360.0), "decDeg": float(dec)},
                      "pixel": {"x": float(r.raw_x), "y": float((grid.npix - 1) - r.raw_y)}})        # raster origin is top-left (contract)

    if kind == "positional_change":
        desc = (f"{obj['name']} appears to move across the sky relative to the stars around it, at about {fit.total:.1f} arcseconds per year, "
                f"towards the {_compass(fit.west_rate, fit.north_rate)}." + ("" if motion_ok else " This has not passed the checks needed to call it a measured motion."))
    elif kind == "brightness_change":
        desc = f"{obj['name']} differs in brightness between observations taken at the same wavelength."
    else:
        desc = f"{obj['name']} appears to move relative to the stars around it and also differs in brightness between same-wavelength observations."
    cand = {"candidateId": cid, "label": label, "objectId": obj["objectId"], "observationIds": [t["observationId"] for t in track], "kind": kind,
            "description": desc, "evidence": evidence, "evidenceRefs": refs, "track": track,
            **({"motion": {"value": round(fit.total, 3), "unit": "arcsec/yr", "uncertainty": round(fit.total_err, 3),
                           "method": ("Derived from observations (relative to surrounding stars, straight-line fit)" if motion_ok
                                      else "Unvalidated displacement (relative to surrounding stars); not a measured motion")}} if motion_listed else {}),
            **({"properMotion": cat} if cat else {}), "score": score_value, "scoreBasis": SCORE_BASIS, "isProbability": False,
            "qualityFlags": [], "warnings": warnings, "limitations": LIMITATIONS, "status": "candidate", "createdAt": now_iso()}
    if not refs:
        cand.pop("evidenceRefs")
    return [cand], {"note": None, "partial": partial}
