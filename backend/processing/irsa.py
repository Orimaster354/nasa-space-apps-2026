"""Thin IRSA client: discover SPHEREx files covering a position, and download cutouts with a disk cache.

Verified against the real archive (backend/scripts/PROBE_FINDINGS.md):
  * discovery: IRSA SIA2 (https://irsa.ipac.caltech.edu/SIA?COLLECTION=...&POS=circle+ra+dec+r). For WISE 0855 it returned the
    same 258 files as a TAP join in about 1.3 s, where the TAP join took about 310 s and sometimes returned HTTP 502.
    TAP is therefore NOT used for discovery.
  * cutout:    https://irsa.ipac.caltech.edu/<path>?center=<ra>,<dec>d&size=<degrees>

No dependency on the web layer, so it is testable on its own. Collections are configuration, so a new data release is a
config change, not a code change. Network behaviour (timeouts, retries, de-duplication) is configurable because IRSA
documents no rate limits.
"""
from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from .archive_status import archive

log = logging.getLogger("spx.irsa")

IRSA_BASE = "https://irsa.ipac.caltech.edu/"
SIA_URL = IRSA_BASE + "SIA"
# Collections named in IRSA's SPHEREx data-access page (read 2026-11-14). QR2 is superseded and not searchable via SIA2.
DEFAULT_COLLECTIONS = ("spherex_dr1", "spherex_dr1_deep", "spherex_qr3", "spherex_qr3_deep")
SEARCH_RADIUS_DEG = 0.01


class IrsaError(Exception):
    """Archive could not be reached or returned something unusable. `retryable` mirrors the contract's ApiError."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ArchiveFile:
    """One SPHEREx spectral-image file whose footprint contains the searched position. Meanings are the archive's (ObsCore);
    wavelengths are converted from metres to micrometres, nothing else is altered."""

    uri: str                       # path under irsa.ipac.caltech.edu (used to build cutout URLs)
    collection: str | None         # e.g. spherex_dr1
    obs_id: str | None
    bandpass: str | None           # e.g. SPHEREx-D3
    center_ra: float | None        # centre of the full detector image
    center_dec: float | None
    footprint: tuple[tuple[float, float], ...]   # polygon corners (ra, dec) from s_region; empty when absent
    wave_lo_um: float | None
    wave_hi_um: float | None
    res_power: float | None
    mjd_start: float | None
    mjd_end: float | None
    exposure_s: float | None
    pixel_arcsec: float | None
    release_date: str | None       # archive `obs_release_date`
    target_name: str | None        # survey field name, not an astronomical target
    est_size_bytes: int | None

    @property
    def release(self) -> str | None:
        """Data release label taken from the collection name ("spherex_dr1" -> "dr1")."""
        if self.collection:
            return self.collection.removeprefix("spherex_")
        m = re.search(r"spherex/([^/]+)/", self.uri)
        return m.group(1) if m else None


def _clean(v):
    if v is None:
        return None
    if getattr(v, "mask", False) is True:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    return None if s in ("", "--", "nan", "None") else v


def _num(v, scale: float = 1.0):
    v = _clean(v)
    return None if v is None else float(v) * scale


def _str(v):
    v = _clean(v)
    return None if v is None else str(v).strip()


_POLY_RE = re.compile(r"POLYGON\s+\S+\s+(.*)", re.IGNORECASE)


def parse_polygon(s_region) -> tuple[tuple[float, float], ...]:
    """'POLYGON ICRS ra dec ra dec ...' -> ((ra, dec), ...). Anything unparseable -> empty tuple."""
    s = _str(s_region)
    m = _POLY_RE.match(s) if s else None
    if not m:
        return ()
    try:
        vals = [float(x) for x in m.group(1).split()]
    except ValueError:
        return ()
    if len(vals) < 6 or len(vals) % 2:
        return ()
    return tuple((vals[i], vals[i + 1]) for i in range(0, len(vals), 2))


def validate_position(ra: float, dec: float) -> tuple[float, float]:
    ra, dec = float(ra), float(dec)
    if not (math.isfinite(ra) and math.isfinite(dec)) or not (0 <= ra < 360) or not (-90 <= dec <= 90):
        raise ValueError(f"invalid position ra={ra}, dec={dec}")
    return ra, dec


def sia_url(collection: str, ra: float, dec: float, radius_deg: float = SEARCH_RADIUS_DEG) -> str:
    ra, dec = validate_position(ra, dec)       # numbers only: nothing user-supplied is interpolated as text
    if not re.fullmatch(r"[A-Za-z0-9_]+", collection):
        raise ValueError("invalid collection name")
    return f"{SIA_URL}?COLLECTION={collection}&POS=circle+{ra!r}+{dec!r}+{radius_deg!r}&RESPONSEFORMAT=votable"


SIA_ATTEMPTS = 2
# On 2026-11-15, after DR1 came out, IRSA's search answered correctly but took about 22 s, which a 20 s limit counted as a failure. The limit is now
# 45 s (override with SPX_SIA_TIMEOUT_S). A timeout is NOT retried (a second wait of the same length would only double the cost); a quick 502 still is.
SIA_TIMEOUT_S = float(os.environ.get("SPX_SIA_TIMEOUT_S", "45"))
SIA_BACKOFF_S = 1.0


def _default_fetch(collection: str, ra: float, dec: float):
    """One SIA2 query -> astropy Table. Raises IrsaError on any failure."""
    from astropy.io.votable import parse_single_table

    url = sia_url(collection, ra, dec)
    r, last = None, None
    for attempt in range(SIA_ATTEMPTS):                     # IRSA sometimes answers 502/503 for a moment (seen on 2026-11-15)
        if attempt:
            time.sleep(SIA_BACKOFF_S * attempt)
        try:
            r = httpx.get(url, timeout=SIA_TIMEOUT_S, follow_redirects=True)
        except httpx.TimeoutException as exc:
            last, r = IrsaError(f"The SPHEREx archive did not answer within {SIA_TIMEOUT_S:g} s."), None
            last.__cause__ = exc
            break                                           # waiting again as long would only double the cost
        except httpx.HTTPError as exc:
            last, r = IrsaError("The SPHEREx archive could not be reached."), None
            last.__cause__ = exc
            continue
        if r.status_code >= 500 or r.status_code == 429:
            last = IrsaError(f"The SPHEREx archive answered {r.status_code}.")
            continue
        last = None
        break
    archive.note(last is None)                               # feeds the app's status badge
    if last is not None:
        raise last
    if r.status_code != 200:
        raise IrsaError(f"The SPHEREx archive rejected the query ({r.status_code}).", retryable=False)
    try:
        return parse_single_table(io.BytesIO(r.content)).to_table(use_names_over_ids=True)   # IRSA gives fields generic IDs (col_0...)
    except Exception as exc:
        raise IrsaError("The archive returned an unreadable response.") from exc


def _to_file(r) -> ArchiveFile | None:
    url = _str(r["access_url"])
    if not url:
        return None
    uri = url.removeprefix(IRSA_BASE)
    return ArchiveFile(
        uri=uri, collection=_str(r["obs_collection"]), obs_id=_str(r["obs_id"]), bandpass=_str(r["energy_bandpassname"]),
        center_ra=_num(r["s_ra"]), center_dec=_num(r["s_dec"]), footprint=parse_polygon(r["s_region"]),
        wave_lo_um=_num(r["em_min"], 1e6), wave_hi_um=_num(r["em_max"], 1e6), res_power=_num(r["em_res_power"]),
        mjd_start=_num(r["t_min"]), mjd_end=_num(r["t_max"]), exposure_s=_num(r["t_exptime"]), pixel_arcsec=_num(r["s_pixel_scale"]),
        release_date=_str(r["obs_release_date"]), target_name=_str(r["target_name"]),
        est_size_bytes=(None if _num(r["access_estsize"]) is None else int(_num(r["access_estsize"]))),
    )


class Discovery(list):
    """List of ArchiveFile plus `incomplete`: names of collections that did not answer (so the list may be missing files)."""

    incomplete: list[str] = []


def discover(ra: float, dec: float, collections: tuple[str, ...] = DEFAULT_COLLECTIONS,
             fetch: Callable[[str, float, float], object] = _default_fetch) -> Discovery:
    """Science spectral-image files whose footprint contains the position, oldest first. Empty list when there is no coverage.

    Collections are queried concurrently. If some fail but others answer, the answers are returned and the failure is
    logged and named in `.incomplete`. If every collection fails, OR some fail and nothing was found, IrsaError is raised: we must
    never say "no coverage" when part of the archive did not answer.
    """
    validate_position(ra, dec)

    def one(coll: str):
        try:
            return coll, fetch(coll, ra, dec), None
        except IrsaError as exc:
            return coll, None, exc
        except Exception as exc:
            return coll, None, IrsaError(f"unexpected archive error: {exc}")

    with ThreadPoolExecutor(max_workers=len(collections) or 1) as pool:
        results = list(pool.map(one, collections))
    errors = [(c, e) for c, t, e in results if e is not None]
    if errors and len(errors) == len(results):
        log.warning("all SIA2 collections failed: %s", [(c, str(e)) for c, e in errors])
        raise IrsaError(str(errors[0][1]), retryable=any(e.retryable for _, e in errors))
    for c, e in errors:
        log.warning("SIA2 collection %s failed: %s", c, e)

    files = Discovery()
    files.incomplete = [c for c, _ in errors]
    seen: set[str] = set()
    for _, table, _ in results:
        if table is None:
            continue
        for r in table:
            f = _to_file(r)
            if f and f.uri not in seen:
                seen.add(f.uri)
                files.append(f)
    if errors and not files:
        raise IrsaError("Part of the SPHEREx archive is not answering right now, so coverage for this position cannot be confirmed. Try again shortly.",
                        retryable=any(e.retryable for _, e in errors))
    files.sort(key=lambda f: (f.mjd_start is None, f.mjd_start or 0.0))
    return files


def cutout_url(uri: str, ra: float, dec: float, size_deg: float) -> str:
    ra, dec = validate_position(ra, dec)
    if not (0 < size_deg <= 1.0):
        raise ValueError("size_deg must be in (0, 1]")
    return f"{IRSA_BASE}{uri.lstrip('/')}?center={ra!r},{dec!r}d&size={size_deg!r}"


class CutoutCache:
    """Disk cache of cutout FITS files keyed by URL. Safe for concurrent callers: one download per key at a time."""

    def __init__(self, directory: str | Path, client: httpx.Client | None = None, retries: int = 3,
                 backoff_s: float = 1.0, timeout_s: float = 120.0, max_concurrent: int = 4):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=timeout_s, follow_redirects=True)
        self.retries, self.backoff_s = retries, backoff_s
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_concurrent)   # polite global cap on IRSA downloads

    def path_for(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode()).hexdigest() + ".fits")

    def is_cached(self, url: str) -> bool:
        return self.path_for(url).exists()

    def get(self, url: str, *, retries: int | None = None, timeout_s: float | None = None) -> Path:
        """Path of the cached cutout, downloading it if needed. `retries` and `timeout_s` override the defaults for this call only
        (used when many frames are fetched in one request and a slow archive must not stall everything)."""
        path = self.path_for(url)
        if path.exists():
            return path
        with self._guard:
            lock = self._locks.setdefault(url, threading.Lock())
        with lock:                                   # concurrent requests for one key share a single download
            if path.exists():
                return path
            with self._slots:
                self._download(url, path, retries, timeout_s)
        return path

    def _download(self, url: str, path: Path, retries: int | None = None, timeout_s: float | None = None) -> None:
        last: Exception | None = None
        n = self.retries if retries is None else retries
        for attempt in range(n + 1):
            try:
                resp = self.client.get(url, **({} if timeout_s is None else {"timeout": timeout_s}))
                if resp.status_code == 404:
                    raise IrsaError("The requested SPHEREx data is not available.", retryable=False)
                if resp.status_code >= 500 or resp.status_code == 429:
                    raise IrsaError(f"The SPHEREx archive answered {resp.status_code}.")
                resp.raise_for_status()
                if not resp.content.startswith(b"SIMPLE"):
                    raise IrsaError("The archive did not return a FITS file.")
                tmp = path.with_suffix(".part")
                tmp.write_bytes(resp.content)
                tmp.replace(path)                     # atomic: readers never see a half-written file
                return
            except IrsaError as exc:
                last = exc
                if not exc.retryable:
                    raise
            except httpx.HTTPError as exc:
                last = exc
            if attempt < n:
                time.sleep(self.backoff_s * (2 ** attempt))
        raise IrsaError("The SPHEREx archive could not be reached.") from last
