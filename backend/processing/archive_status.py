"""Is NASA's IRSA archive answering right now? Powers the small status badge in the app.

Two sources, newest wins:
  * passive: every real archive search records whether it worked (free, no extra traffic);
  * active: when the last word is older than `ttl_s`, ONE background probe asks the archive a tiny question. The status request itself
    never waits for it, so the badge can never slow the page down.
The probe is off unless switched on (SPX_ARCHIVE_PROBE=true), so tests and local runs never touch the network by accident.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Callable

import httpx

PROBE_URL = ("https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_dr1&POS=circle+162.0+-53.0+0.001&RESPONSEFORMAT=votable")


def default_probe(timeout_s: float = 6.0) -> bool:
    """True when the archive's search service answers properly (any answer below 500 that is not 'too many requests')."""
    try:
        r = httpx.get(PROBE_URL, timeout=timeout_s, follow_redirects=True)
    except httpx.HTTPError:
        return False
    return r.status_code < 500 and r.status_code != 429


class ArchiveStatus:
    def __init__(self, probe: Callable[[], bool] | None = None, ttl_s: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self._probe, self._ttl, self._clock = probe, ttl_s, clock
        self._lock = threading.Lock()
        self._ok: bool | None = None
        self._at: float | None = None          # monotonic time of the last word
        self._wall: str | None = None
        self._probing = False

    def note(self, ok: bool) -> None:
        """Record the outcome of a real archive request."""
        with self._lock:
            self._ok, self._at = ok, self._clock()
            self._wall = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def enable_probe(self, probe: Callable[[], bool] | None = None) -> None:
        self._probe = probe or default_probe

    def _run_probe(self) -> None:
        try:
            ok = bool(self._probe())
        except Exception:
            ok = False
        self.note(ok)
        with self._lock:
            self._probing = False

    def snapshot(self) -> dict:
        """The current status; starts a background refresh when it is stale. Never blocks."""
        with self._lock:
            stale = self._at is None or self._clock() - self._at >= self._ttl
            start = stale and self._probe is not None and not self._probing
            if start:
                self._probing = True
            ok, wall = self._ok, self._wall
        if start:
            threading.Thread(target=self._run_probe, name="archive-probe", daemon=True).start()
        if ok is None:
            return {"status": "unknown", "checkedAt": None,
                    "message": "Not checked yet."}
        if ok:
            return {"status": "up", "checkedAt": wall, "message": "The NASA SPHEREx archive (IRSA) is answering."}
        return {"status": "down", "checkedAt": wall,
                "message": "The NASA SPHEREx archive (IRSA) is not answering right now. Saved targets still work; other searches may fail until it is back."}


archive = ArchiveStatus()
