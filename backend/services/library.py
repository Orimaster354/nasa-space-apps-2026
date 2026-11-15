"""The 'library': which targets this server already holds, so they work with the NASA archive unreachable.

Frontend-only operation (not in the contract). Everything here is read from what is actually stored (registry rows and files on disk),
never from a hard-coded list, so a bigger bundle or a target someone searched for shows up by itself.

"Stored frames" means observations whose picture is already rendered on this server: they open instantly and offline.
"""
from __future__ import annotations

import os
import threading
import time

from . import spectrum as spectrum_mod
from . import targets

_TTL_S = 30.0
_lock = threading.Lock()
_cache: dict[int, tuple[float, dict]] = {}


def _dir_bytes(path) -> int:
    total = 0
    if path is None:
        return 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.is_file(follow_symlinks=False):
                    total += e.stat().st_size
    except OSError:
        pass
    return total


def build(svc, fresh: bool = False) -> dict:
    key = id(svc)
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < _TTL_S:
            return hit[1]
    out = _compute(svc)
    with _lock:
        _cache[key] = (time.monotonic(), out)
    return out


def _compute(svc) -> dict:
    # observation ids that have a rendered picture on disk ("obs_<hex>_g_<grid>_<stretch>...png")
    rendered: set[str] = set()
    try:
        with os.scandir(svc.images_dir) as it:
            for e in it:
                n = e.name
                if n.startswith("obs_") and n.endswith(".png"):
                    rendered.add("_".join(n.split("_", 2)[:2]))
    except OSError:
        pass

    objects = {o["objectId"]: o for o in svc.registry.all_objects()}
    per: dict[str, dict] = {}
    for oid, rec in svc.registry.all_observations():
        ob = rec.get("observation") or {}
        for obj_id in ob.get("objectIds", []):
            p = per.setdefault(obj_id, {"listed": 0, "stored": 0, "byBand": {}})
            p["listed"] += 1
            if oid in rendered:
                p["stored"] += 1
                band = ob.get("bandpass") or "unknown"
                p["byBand"][band] = p["byBand"].get(band, 0) + 1

    curated = [t.name.lower() for t in targets.TARGETS]
    items = []
    for obj_id, p in per.items():
        o = objects.get(obj_id)
        if o is None or p["stored"] == 0:
            continue
        sp = svc.registry.get_spectrum(spectrum_mod.spectrum_id(obj_id, None, None, spectrum_mod.DEFAULT_RADIUS_ARCSEC))
        spectrum = None
        if sp:
            r = sp["result"]
            ok = [q for q in r.get("points", []) if q.get("available")]
            spectrum = {"status": r["availability"]["status"], "bands": len(ok),
                        **({"reason": r["availability"].get("statusReason")} if r["availability"]["status"] != "available" else {})}
            if ok:
                spectrum["fromUm"], spectrum["toUm"] = min(q["wavelengthUm"] for q in ok), max(q["wavelengthUm"] for q in ok)
        run = svc.registry.get_candidate_run(obj_id)
        cands = None if run is None else {"count": len(run.get("candidateIds", [])), "note": run.get("note")}
        is_curated = o["name"].lower() in curated
        items.append({
            "objectId": obj_id, "name": o["name"], "objectType": o.get("objectType"), "coordinates": o["coordinates"], "curated": is_curated,
            "observationsListed": p["listed"], "storedFrames": p["stored"], "storedByBand": dict(sorted(p["byBand"].items())),
            "spectrum": spectrum, "candidates": cands,
            "worksOffline": True,
            "properMotion": ({"value": o["properMotion"]["value"], "unit": o["properMotion"]["unit"]} if o.get("properMotion") else None),
        })
    order = {n: i for i, n in enumerate(curated)}
    items.sort(key=lambda i: (order.get(i["name"].lower(), 999), -i["storedFrames"], i["name"]))
    return {
        "items": items,
        "totals": {"targets": len(items), "storedFrames": sum(i["storedFrames"] for i in items),
                   "storedMegabytes": round((_dir_bytes(svc.images_dir) + _dir_bytes(getattr(svc.cutouts, "dir", None))) / 1e6, 1)},
        "note": ("These targets and their pictures are stored on this server, so they open instantly and keep working when NASA's archive is "
                 "unreachable. Any other target needs the archive."),
    }
