"""Adding a searched target to the preloaded set (feature: "searches that work become preloads").

When a visitor searches for something that is not preloaded and the archive answers with a complete observation list, a background worker gives
that target the same treatment the shipped targets got, so the next visitor finds it instantly and offline:
  1. the first ~50 pictures in the default band, poor-quality frames dropped (bundle.warm_target);
  2. the spectrum with the picture behind every band, or a stored refusal with its reason (bundle.warm_spectrum);
  3. a motion-candidate search when SIMBAD supplied a proper motion (services/pm_lookup.py), otherwise the reason there is none;
  4. its cutouts slimmed to what the app reads (about a tenth of the size), the observation list saved.
Then it is marked (`promotedAt`), listed in the Library as "added from a search", and logged by name (`PRELOAD_ADDED name=...`).

It is OFF unless SPX_AUTO_PRELOAD is set, runs one target at a time, and is bounded: a daily cap, a cap on stored data, a minimum number of
observations, and a one-hour wait before a failed target is tried again. Cloud Run's disk is temporary, so additions last until the next
redeploy or restart; the log line is how the popular ones get baked into the next shipped bundle.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import bundle, library, targets

log = logging.getLogger("spx.promotion")

RETRY_AFTER_FAILURE_S = 3600.0


class Promoter:
    def __init__(self, svc, *, min_d3: int = 8, per_day: int = 12, max_data_mb: float = 1500.0, frames: int = 50, quiet: bool = True):
        self.svc, self.min_d3, self.per_day, self.max_data_mb, self.frames = svc, min_d3, per_day, max_data_mb, frames
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="promote")
        self._lock = threading.Lock()
        self._active: set[str] = set()                           # queued or running
        self._failed_at: dict[str, float] = {}
        self._started: dict[str, float] = {}
        self._futures: dict[str, object] = {}

    # ---- decide -------------------------------------------------------------------------------------------------
    def _data_mb(self) -> float:
        return (library._dir_bytes(self.svc.images_dir) + library._dir_bytes(getattr(self.svc.cutouts, "dir", None))) / 1e6

    def _recent_count(self) -> int:
        cut = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        return sum(1 for o in self.svc.registry.all_objects() if (o.get("promotedAt") or "") >= cut)

    def consider(self, object_id: str, d3_count: int, complete: bool) -> bool:
        """Called after a successful observation listing. Returns True when a job was queued."""
        if not complete or d3_count < self.min_d3:
            return False
        obj = self.svc.registry.get_object(object_id) or {}
        if not obj or obj.get("promotedAt") or targets.find(obj.get("name")) in targets.TARGETS:
            return False
        with self._lock:
            if object_id in self._active or time.monotonic() - self._failed_at.get(object_id, -1e12) < RETRY_AFTER_FAILURE_S:
                return False
            if self._recent_count() >= self.per_day:
                log.info("PRELOAD_SKIPPED name=%s reason=daily cap of %d reached", obj.get("name"), self.per_day)
                return False
            if self._data_mb() >= self.max_data_mb:
                log.info("PRELOAD_SKIPPED name=%s reason=stored data is above %.0f MB", obj.get("name"), self.max_data_mb)
                return False
            self._active.add(object_id)
        self._futures[object_id] = self._pool.submit(self._job, object_id)
        return True

    def wait(self, object_id: str, timeout: float = 120.0) -> None:
        """Wait for a queued job (tests and scripts)."""
        f = self._futures.get(object_id)
        if f is not None:
            f.result(timeout=timeout)

    # ---- the job ------------------------------------------------------------------------------------------------
    def _job(self, object_id: str) -> None:
        svc = self.svc
        obj = svc.registry.get_object(object_id)
        name = obj["name"]
        started = time.time()
        try:
            c = obj["coordinates"]
            query = f"{c['raDeg']} {c['decDeg']}" if obj["resolver"]["name"] == "user-supplied coordinates" else name
            report = bundle.warm_target(svc, query, frames=self.frames, workers=4, rounds=3, pause_s=2.0, log=lambda *a: None, skip_poor=True)
            if report["missing"]:
                raise RuntimeError(f"{len(report['missing'])} pictures could not be downloaded")
            c0 = obj["coordinates"]
            if svc.registry.get_discovery(f"{round(c0['raDeg'], 4):.4f},{round(c0['decDeg'], 4):.4f}") is None:
                raise RuntimeError("the archive's observation list was incomplete, so it could not be saved")
            spec = bundle.build_final_spectrum(svc, object_id, log=lambda *a: None)
            run = svc.compute_candidates(object_id)
            self._slim_new_cutouts(started)
            body = svc.registry.get_object(object_id)
            note = run.get("note") or ""
            promotion = {"ok": True, "frames": report["ready"], "spectrum": spec["status"], "spectrumBands": spec["bandImages"], "spectrumReason": spec["reason"][:200],
                         "candidates": len(run.get("candidateIds", [])), "candidatesNote": note[:300]}
            svc.registry.put_object(object_id, {**body, "promotedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), "promotion": promotion})
            library.invalidate(svc)
            log.info("PRELOAD_ADDED name=%s objectId=%s frames=%d spectrum=%s bands=%d candidates=%d spectrumNote=%s candidatesNote=%s", name, object_id, report["ready"],
                     spec["status"], spec["bandImages"], promotion["candidates"], spec["reason"][:100], note[:100])
        except Exception as exc:
            with self._lock:
                self._failed_at[object_id] = time.monotonic()
            log.warning("PRELOAD_FAILED name=%s objectId=%s reason=%s", name, object_id, str(exc)[:200])
            body = svc.registry.get_object(object_id) or obj
            svc.registry.put_object(object_id, {**body, "promotion": {"ok": False, "reason": str(exc)[:200]}})
        finally:
            with self._lock:
                self._active.discard(object_id)

    def _slim_new_cutouts(self, since: float) -> None:
        d = getattr(self.svc.cutouts, "dir", None)
        if d is None:
            return
        for f in Path(d).glob("*.fits"):
            try:
                st = f.stat()
                if st.st_mtime >= since - 1 and st.st_size > 1_500_000:
                    bundle.slim_fits(f, f)
            except Exception as exc:                          # a file that cannot be slimmed is simply left as it is
                log.info("could not slim %s: %s", f.name, exc)
