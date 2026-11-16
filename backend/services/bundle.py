"""Demo bundle: everything the curated demo targets need to work with the SPHEREx archive unreachable.

A bundle is an ordinary data directory (registry.sqlite, images/, cutouts/) built once on a machine with network access, then shipped
inside the container as the starting data directory. It contains, for each target:
  * the target and its observation records (so lists and ids resolve),
  * a saved archive listing (so the observation list works while IRSA is down),
  * rendered images for the first N frames of the default band at the default field of view,
  * SLIM copies of those frames' cutouts. A full cutout is about 4 MB, almost all of it a PSF table this app never reads; the slim copy
    keeps only the planes the pipeline reads (IMAGE, FLAGS, VARIANCE, ZODI, WCS-WAVE), about 0.4 MB. With them, other stretches,
    comparisons, difference images, alignment and photometry for those frames also work offline.
Frames outside the bundle still need the archive and fail cleanly (a retryable error) when it is down.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from astropy.io import fits

from processing import imaging

KEEP_EXTENSIONS = ("IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE")


def slim_fits(src: str | Path, dst: str | Path) -> tuple[int, int]:
    """Write a copy of `src` with only the extensions the pipeline reads. Returns (size_before, size_after) in bytes."""
    src, dst = Path(src), Path(dst)
    with fits.open(src, memmap=False) as h:
        out = fits.HDUList([fits.PrimaryHDU(header=h[0].header)])
        for name in KEEP_EXTENSIONS:
            try:
                out.append(h[name].copy())
            except KeyError:
                continue
        if "IMAGE" not in [x.name for x in out]:
            raise ValueError(f"{src.name} has no IMAGE extension")
        tmp = dst.with_name(dst.name + ".part")
        out.writeto(tmp, overwrite=True)
    before = src.stat().st_size
    os.replace(tmp, dst)
    return before, dst.stat().st_size


def slim_directory(cutout_dir: str | Path) -> dict:
    """Slim every cutout in a cache directory in place (same file names, so the cache keys still match)."""
    sizes_before = sizes_after = n = 0
    for f in sorted(Path(cutout_dir).glob("*.fits")):
        b, a = slim_fits(f, f)
        sizes_before, sizes_after, n = sizes_before + b, sizes_after + a, n + 1
    return {"files": n, "bytesBefore": sizes_before, "bytesAfter": sizes_after}


def prune_poor(svc, observation_ids: list[str]) -> list[str]:
    """Delete the stored image and cutout of every frame whose own quality roll-up is 'poor' (over a quarter of its pixels unusable).
    Such a frame helps nobody in a demo and costs space; it is still listed and, if someone opens it, fetched live. Returns the ids dropped."""
    from processing import irsa

    dropped = []
    for oid in observation_ids:
        rec = svc.registry.get_observation(oid) or {}
        if (rec.get("quality") or {}).get("summary") != "poor":
            continue
        for f in svc.images_dir.glob(f"{oid}_*"):
            f.unlink(missing_ok=True)
        grid = svc._grid(rec, None, None, imaging.DEFAULT_FOV_ARCMIN)
        svc.cutouts.path_for(irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)).unlink(missing_ok=True)
        dropped.append(oid)
    return dropped


def warm_spectrum(svc, object_id: str, wait_s: float = 900.0, log=print) -> dict:
    """Build and STORE the object's spectrum, then render the image behind every band, so the Spectrum page works with the archive down.
    A spectrum that is partial or refused is stored too (a refusal is an answer), but only a complete one is served without a rebuild."""
    res = svc.get_spectrum(object_id, wait_s=wait_s)
    status = res["availability"]["status"]
    ids = sorted({p["observationId"] for p in res.get("points", []) if p.get("available") and p.get("observationId")})
    ready = 0
    if status == "available" and ids:
        with ThreadPoolExecutor(max_workers=4) as pool:
            ready = sum(a["status"] == "available" for a in pool.map(lambda oid: svc.get_image(oid, wait_s=300)["availability"], ids))
    log(f"  spectrum: {status}, {len(ids)} band images, {ready} rendered" + ("" if status == "available" else f" ({res['availability'].get('statusReason', '')[:80]})"))
    return {"status": status, "bandImages": len(ids), "rendered": ready}


def build_final_spectrum(svc, object_id: str, passes: int = 3, budget_s: float = 240.0, log=print) -> dict:
    """The spectrum of a target that is being ADDED to the preloaded set: built with a long download budget, repeated (frames fetched in one pass are
    stored for the next) until nothing is missing, and then kept as the final answer, an honest refusal included, so a visitor never triggers a rebuild.
    Also renders the picture behind every measured band. Returns {status, bandImages, rendered, reason}."""
    from . import spectrum as sp
    from .core import now_iso
    obj = svc.get_object(object_id)
    sid = sp.spectrum_id(object_id, None, None, sp.DEFAULT_RADIUS_ARCSEC)
    result = None
    for _ in range(max(1, passes)):
        result = svc._build_spectrum(obj, None, None, sp.DEFAULT_RADIUS_ARCSEC, sid, budget_s=budget_s)
        rec = svc.registry.get_spectrum(sid)
        if rec is not None and not rec.get("partial"):
            break
    rec = svc.registry.get_spectrum(sid)
    if rec is None or rec.get("partial"):                      # what the stored frames allow is the answer (a refusal is stored too)
        svc.registry.put_spectrum(sid, {"result": result, "partial": False, "savedAt": now_iso()})
    status = result["availability"]["status"]
    ids = sorted({p["observationId"] for p in result.get("points", []) if p.get("available") and p.get("observationId")})
    ready = 0
    if status == "available" and ids:
        with ThreadPoolExecutor(max_workers=4) as pool:
            ready = sum(a["status"] == "available" for a in pool.map(lambda oid: svc.get_image(oid, wait_s=300)["availability"], ids))
    reason = result["availability"].get("statusReason", "")
    log(f"  spectrum: {status}, {len(ids)} band images, {ready} rendered" + ("" if status == "available" else f" ({reason[:80]})"))
    return {"status": status, "bandImages": len(ids), "rendered": ready, "reason": reason}


def warm_target(svc, query: str, band: str = "SPHEREx-D3", frames: int = 50, workers: int = 4, rounds: int = 4,
                pause_s: float = 5.0, log=print, skip_poor: bool = False, spare: float = 0.25) -> dict:
    """Resolve a target, list its observations in `band`, and render the first `frames` images, retrying failures a few rounds.

    With `skip_poor`, `spare` (a fraction) extra frames are rendered so that dropping the poor ones still leaves about `frames` usable frames."""
    obj = svc.resolve(q=query)
    page = svc.list_observations(obj["objectId"], bandpass=band, limit=200)
    items = page["items"][: (min(200, int(frames * (1 + spare) + 0.999)) if skip_poor else frames)]
    pending = [o["observationId"] for o in items]
    log(f"{obj['name']}: {page['total']} {band} observations; warming {len(pending)} frames")
    ok = 0
    for rnd in range(1, rounds + 1):
        if not pending:
            break
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda oid: (oid, svc.get_image(oid, wait_s=300)["availability"]), pending))
        pending = [oid for oid, a in results if a["status"] != "available"]
        ok = len(items) - len(pending)
        log(f"  round {rnd}: {ok}/{len(items)} frames ready" + (f"; retrying {len(pending)}" if pending and rnd < rounds else ""))
        if pending and rnd < rounds:
            time.sleep(pause_s)
    poor: list[str] = []
    if skip_poor:
        poor = prune_poor(svc, [o["observationId"] for o in items if o["observationId"] not in pending])
        if poor:
            log(f"  dropped {len(poor)} poor-quality frame(s)")
    return {"target": obj["name"], "objectId": obj["objectId"], "observations": page["total"], "frames": len(items) - len(poor),
            "ready": ok - len(poor), "missing": pending, "poor": poor}
