"""Use-case orchestration: objects, observations, images. Depends on `processing` and `store`, never on the web layer.

Deviations worth knowing (documented, not hidden):
  * `Observation.center` and `footprint` describe the FULL detector image from the archive (about 3.5 degrees across), not the
    small cutout we serve around the object. The served image has its own grid (see `ObservationImage.wcs`).
  * `Observation.wavelengthRangeUm` is the archive's plane-level band range (a whole detector band), not the wavelength at the
    object's pixel.
  * `Observation.dataRelease` is taken from the archive file path (e.g. "dr1"); the archive's own `datarelease` column is kept in
    `sourceMetadata`.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from processing import alignment, coadd, compare, imaging, irsa, photometry, quality
from processing.fits_io import FitsReadError, read_cutout
from store.registry import Registry

from . import candidates, guided, spectrum, targets
from .investigations import InvestigationStore

log = logging.getLogger("spx.core")

_MJD_EPOCH = datetime(1858, 11, 17, tzinfo=timezone.utc)


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


class DataUnavailable(Exception):
    """The request is valid but the data cannot support it (for example an unmeasurable region). HTTP 422."""


class Upstream(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def mjd_to_iso(mjd: float | None) -> str | None:
    if mjd is None:
        return None
    return (_MJD_EPOCH + timedelta(days=mjd)).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_to_mjd(s: str) -> float:
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequest(f"'{s}' is not an ISO date") from exc
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (d - _MJD_EPOCH).total_seconds() / 86400.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def object_id_for(ra: float, dec: float) -> str:
    return "obj_" + hashlib.sha1(f"{ra:.4f},{dec:.4f}".encode()).hexdigest()[:10]


def observation_id_for(uri: str) -> str:
    return "obs_" + hashlib.sha1(uri.encode()).hexdigest()[:12]


def release_from_uri(uri: str) -> str | None:
    m = re.search(r"spherex/([^/]+)/", uri)
    return m.group(1) if m else None


def _astropy_resolver(name: str) -> tuple[float, float]:
    from astropy.coordinates import SkyCoord
    from astropy.coordinates.name_resolve import NameResolveError

    try:
        c = SkyCoord.from_name(name)
    except NameResolveError as exc:
        text = str(exc)
        if "Unable to find" in text or "not found" in text.lower():
            raise NotFound(f"No object found for '{name}'.") from exc
        raise Upstream("The name resolver could not be reached right now.") from exc
    return float(c.ra.deg), float(c.dec.deg)


_COORD_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[, ]\s*([+-]?\d+(?:\.\d+)?)\s*$")


class Services:
    def __init__(self, registry: Registry, cutouts: irsa.CutoutCache, images_dir: str | Path,
                 discover: Callable[[float, float], list[irsa.ArchiveFile]] = irsa.discover,
                 resolver: Callable[[str], tuple[float, float]] = _astropy_resolver,
                 discovery_ttl_s: float = 600.0, image_wait_s: float = 20.0, render_workers: int = 6,
                 snapshot_max_age_s: float = 3 * 86400.0):
        self.registry, self.cutouts = registry, cutouts
        self.investigations = InvestigationStore(registry)
        self.images_dir = Path(images_dir)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._discover, self._resolver = discover, resolver
        self._ttl = discovery_ttl_s
        # Images render in a small worker pool. A request waits up to `image_wait_s` for its image; if it is not ready by then the
        # response is 'processing' (the frontend polls) and the job keeps running. Identical requests share one job.
        self.image_wait_s = image_wait_s
        self._pool = ThreadPoolExecutor(max_workers=render_workers, thread_name_prefix="render")
        self._jobs: dict[str, Future] = {}
        self._spectrum_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="spectrum")
        self._spectrum_jobs: dict[str, Future] = {}
        self._candidate_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="candidates")
        self._candidate_jobs: dict[str, Future] = {}
        self.candidate_cached_only = False                # True: the candidate search uses only stored frames (building the demo bundle, no downloads)
        self.candidate_partial_ttl_s = 600.0              # a candidate search that missed frames (archive trouble) is redone after this long
        self.spectrum_partial_ttl_s = 600.0               # an incomplete spectrum is reused for this long, then rebuilt
        self.spectrum_wait_s = 25.0                       # a request waits this long for a spectrum before answering 202 processing
        self.snapshot_max_age_s = snapshot_max_age_s     # a saved archive listing this young is served at once (see _discover_cached)
        self._refreshing: set[str] = set()
        self._disc_cache: dict[tuple[float, float], tuple[float, list[irsa.ArchiveFile]]] = {}
        self._lock = threading.Lock()

    # ---- objects -------------------------------------------------------------------------------------------------
    def _make_object(self, name: str, ra: float, dec: float, resolver_name: str, identifier: str | None) -> dict:
        try:
            ra, dec = irsa.validate_position(ra, dec)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        oid = object_id_for(ra, dec)
        existing = self.registry.get_object(oid)
        if existing and resolver_name == "user-supplied coordinates":
            return existing                       # never replace a resolved name with a bare coordinate label
        obj = {"objectId": oid, "name": name, "aliases": [], "coordinates": {"raDeg": ra, "decDeg": dec},
               "resolver": {"name": resolver_name, **({"identifier": identifier} if identifier else {})}}
        if existing and existing["name"] != name and existing["resolver"]["name"] != "user-supplied coordinates":
            obj["aliases"] = sorted(set(existing.get("aliases", [])) | {existing["name"]})
        self.registry.put_object(oid, obj)
        return obj

    def resolve(self, q: str | None = None, ra: float | None = None, dec: float | None = None) -> dict:
        if ra is not None and dec is not None:
            return self._make_object(f"RA {ra:.4f} Dec {dec:.4f}", ra, dec, "user-supplied coordinates", None)
        if not q or not q.strip():
            raise BadRequest("Provide a name (q) or coordinates (ra and dec).")
        m = _COORD_RE.match(q)
        if m:
            return self.resolve(ra=float(m.group(1)), dec=float(m.group(2)))
        t = targets.find(q)
        if t is not None:
            return self._make_curated(t)                  # works offline and for spellings the name resolver does not know
        r_ra, r_dec = self._resolver(q.strip())
        return self._make_object(q.strip(), r_ra, r_dec, "CDS Sesame via astropy", q.strip())

    def _make_curated(self, t: targets.Target) -> dict:
        oid = object_id_for(t.ra, t.dec)
        vra, vdec = targets.view_center(t)
        obj = {"objectId": oid, "name": t.name, "aliases": sorted({a for a in t.aliases if a != t.name}),
               "coordinates": {"raDeg": t.ra, "decDeg": t.dec}, "objectType": t.object_type,
               "properMotion": targets.proper_motion_model(t),
               "resolver": {"name": "Curated demo target", "identifier": t.catalog_id}}
        # `viewCenter` is internal (not in the contract model): images are centred where the object is expected during the survey.
        self.registry.put_object(oid, {**obj, "viewCenter": {"raDeg": vra, "decDeg": vdec}})
        return obj

    def get_object(self, object_id: str) -> dict:
        obj = self.registry.get_object(object_id)
        if obj is None:
            raise NotFound("Unknown object.")
        return obj

    # ---- observations --------------------------------------------------------------------------------------------
    @staticmethod
    def _age_s(saved_at: str | None) -> float:
        try:
            return (datetime.now(timezone.utc) - datetime.fromisoformat(saved_at.replace("Z", "+00:00"))).total_seconds()
        except (AttributeError, ValueError):
            return float("inf")

    @staticmethod
    def _files_from_snapshot(snap: dict) -> list[irsa.ArchiveFile]:
        return [irsa.ArchiveFile(**{**d, "footprint": tuple(tuple(p) for p in d["footprint"]) if d.get("footprint") else d.get("footprint")})
                for d in snap["files"]]

    def _live_discover(self, ra: float, dec: float, key: tuple[float, float], skey: str) -> list[irsa.ArchiveFile]:
        """Ask the archive; on a complete answer save it as the snapshot and cache it in memory. May raise IrsaError."""
        files = self._discover(ra, dec)
        if getattr(files, "incomplete", []):
            return files                                   # a partial listing is returned to the caller but never saved or cached
        self.registry.put_discovery(skey, {"savedAt": now_iso(), "files": [asdict(f) for f in files]})
        with self._lock:
            self._disc_cache[key] = (time.monotonic(), list(files))
        return files

    def _refresh_in_background(self, ra: float, dec: float, key: tuple[float, float], skey: str) -> None:
        with self._lock:
            if skey in self._refreshing:
                return
            self._refreshing.add(skey)

        def run():
            try:
                self._live_discover(ra, dec, key, skey)
            except Exception as exc:                       # the saved listing keeps serving; nothing to tell the user
                log.info("background refresh of %s failed: %s", skey, exc)
            finally:
                with self._lock:
                    self._refreshing.discard(skey)

        threading.Thread(target=run, name="discovery-refresh", daemon=True).start()

    def _discover_cached(self, ra: float, dec: float) -> tuple[list[irsa.ArchiveFile], str | None]:
        """Archive listing for a position, plus an optional note for the user.

        1. A listing fetched in the last few minutes is reused.
        2. Otherwise, a saved snapshot younger than `snapshot_max_age_s` is returned AT ONCE and refreshed in the background, so a slow or
           broken archive never makes the page wait (the demo bundle ships such snapshots).
        3. Otherwise the archive is asked; if it fails, an older snapshot is used (and the user is told) or the error is raised.
        A listing that is missing a collection is never cached or saved, and carries a note.
        """
        key = (round(ra, 4), round(dec, 4))
        skey = f"{key[0]:.4f},{key[1]:.4f}"
        with self._lock:
            hit = self._disc_cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1], None
        snap = self.registry.get_discovery(skey)
        if snap and self._age_s(snap.get("savedAt")) < self.snapshot_max_age_s:
            self._refresh_in_background(ra, dec, key, skey)
            return self._files_from_snapshot(snap), None
        try:
            files = self._live_discover(ra, dec, key, skey)
        except irsa.IrsaError as exc:
            if snap:
                log.warning("archive unavailable (%s); serving the saved listing from %s", exc, snap.get("savedAt"))
                return (self._files_from_snapshot(snap),
                        f"The SPHEREx archive is not responding right now, so this is the last saved listing ({snap.get('savedAt', 'earlier')}). It may be out of date.")
            raise Upstream(str(exc), exc.retryable) from exc
        incomplete = list(getattr(files, "incomplete", []))
        if incomplete:
            return list(files), (f"Some archive collections did not respond ({', '.join(incomplete)}), so this list may be incomplete. "
                                 "Reload in a moment to check again.")
        return list(files), None

    def _observation_dict(self, f: irsa.ArchiveFile, obj: dict, quality_info: dict | None) -> dict:
        det = f.bandpass.rsplit("D", 1)[-1] if f.bandpass and "D" in f.bandpass else None
        file_stem = f.uri.rsplit("/", 1)[-1].removesuffix(".fits")
        meta = {k: v for k, v in {
            "uri": f.uri, "obsId": f.obs_id, "collection": f.collection, "archiveReleaseDate": f.release_date,
            "surveyField": f.target_name, "exposureSeconds": f.exposure_s, "pixelScaleArcsec": f.pixel_arcsec,
            "resolvingPower": f.res_power, "mjdStart": f.mjd_start, "mjdEnd": f.mjd_end, "estimatedSizeBytes": f.est_size_bytes,
        }.items() if v is not None}
        center = ({"raDeg": f.center_ra, "decDeg": f.center_dec} if f.center_ra is not None and f.center_dec is not None
                  else obj["coordinates"])
        out = {
            "observationId": observation_id_for(f.uri), "objectIds": [obj["objectId"]],
            "center": center, "image": {"status": "available"},
            "qualityFlags": (quality_info or {}).get("flags", []), "qualitySummary": (quality_info or {}).get("summary", "unknown"),
            "sourceMetadata": meta,
        }
        for k, v in {"irsaObservationId": file_stem, "dataRelease": f.release, "observedAt": mjd_to_iso(f.mjd_start),
                     "detector": det, "bandpass": f.bandpass}.items():
            if v is not None:
                out[k] = v
        if f.footprint:
            out["footprint"] = [{"raDeg": ra, "decDeg": dec} for ra, dec in f.footprint]
        if f.wave_lo_um is not None and f.wave_hi_um is not None:
            out["wavelengthRangeUm"] = {"min": f.wave_lo_um, "max": f.wave_hi_um}
        return out

    def list_observations(self, object_id: str, *, date_from: str | None = None, date_to: str | None = None,
                          data_release: str | None = None, bandpass: str | None = None,
                          min_wl: float | None = None, max_wl: float | None = None, limit: int = 50, offset: int = 0, sort: str = "observedAt") -> dict:
        if not (1 <= limit <= 200) or offset < 0:
            raise BadRequest("limit must be 1-200 and offset >= 0.")
        if sort not in ("observedAt", "-observedAt"):
            raise BadRequest("sort must be observedAt or -observedAt.")
        obj = self.get_object(object_id)
        c = obj["coordinates"]
        files, archive_note = self._discover_cached(c["raDeg"], c["decDeg"])

        lo = iso_to_mjd(date_from) if date_from else None
        hi = iso_to_mjd(date_to) if date_to else None
        keep = []
        for f in files:
            if lo is not None and (f.mjd_start is None or f.mjd_start < lo):
                continue
            if hi is not None and (f.mjd_start is None or f.mjd_start > hi):
                continue
            if data_release and f.release != data_release:
                continue
            if bandpass and f.bandpass != bandpass:
                continue
            if min_wl is not None and (f.wave_hi_um is None or f.wave_hi_um < min_wl):
                continue
            if max_wl is not None and (f.wave_lo_um is None or f.wave_lo_um > max_wl):
                continue
            keep.append(f)
        keep.sort(key=lambda f: (f.mjd_start is None, f.mjd_start or 0.0), reverse=(sort == "-observedAt"))

        items = []
        priors = self.registry.get_observations([observation_id_for(f.uri) for f in keep])        # one read for the whole listing
        stored = []
        for f in keep:
            oid = observation_id_for(f.uri)
            prior = priors.get(oid)
            observation = self._observation_dict(f, obj, (prior or {}).get("quality"))
            self._with_wave(observation, prior or {})
            object_ids = sorted(set((prior or {}).get("observation", {}).get("objectIds", [])) | {object_id})
            observation["objectIds"] = object_ids
            stored.append((oid, {"observation": observation, "file": asdict(f), "quality": (prior or {}).get("quality"),
                                 **({"wavelengthAtObject": prior["wavelengthAtObject"]} if prior and prior.get("wavelengthAtObject") else {})}))
            items.append(observation)
        self.registry.put_observations(stored)                                                       # and one write

        note = None
        if not files:
            note = "No SPHEREx observations were found covering this position."
        elif not keep:
            note = "Observations exist here, but none match the filters."
        page = items[offset:offset + limit]
        out = {"items": page, "total": len(items), "limit": limit, "offset": offset}
        if page and (offset > 0 or offset + len(page) < len(items)):          # the caller sees only part of the list: say so
            note = (f"Showing observations {offset + 1}-{offset + len(page)} of {len(items)} matching this view "
                    f"({'oldest' if sort == 'observedAt' else 'newest'} first).")
        note = " ".join(n for n in (note, archive_note) if n) or None
        if note:
            out["coverageNote"] = note
        return out

    @staticmethod
    def _with_wave(obs: dict, rec: dict) -> None:
        w = rec.get("wavelengthAtObject")
        if w:
            obs["wavelengthAtObjectUm"] = w["um"]
            if w.get("bandwidthUm") is not None:
                obs["bandwidthAtObjectUm"] = w["bandwidthUm"]

    def get_observation(self, observation_id: str) -> dict:
        rec = self.registry.get_observation(observation_id)
        if rec is None:
            raise NotFound("Unknown observation.")
        obs = dict(rec["observation"])
        self._with_wave(obs, rec)
        if rec.get("quality"):
            obs["qualityFlags"], obs["qualitySummary"] = rec["quality"]["flags"], rec["quality"]["summary"]
        return obs

    # ---- images --------------------------------------------------------------------------------------------------
    def _grid(self, rec: dict, center_ra, center_dec, fov_arcmin) -> imaging.GridSpec:
        if (center_ra is None) != (center_dec is None):
            raise BadRequest("Provide both centerRa and centerDec, or neither.")
        if center_ra is None:
            obj = self.registry.get_object(rec["observation"]["objectIds"][0])
            if obj is None:
                raise NotFound("The object for this observation is unknown.")
            c = obj.get("viewCenter") or obj["coordinates"]
            center_ra, center_dec = c["raDeg"], c["decDeg"]
        try:
            irsa.validate_position(center_ra, center_dec)
            return imaging.GridSpec(center_ra, center_dec, fov_arcmin)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

    @staticmethod
    def _failure(base: dict, exc: Exception) -> dict:
        if isinstance(exc, irsa.IrsaError):
            status = "failed" if exc.retryable else "unavailable"
            return {**base, "availability": {"status": status, "statusReason": str(exc), **({"retryAfterSeconds": 30} if exc.retryable else {})}}
        return {**base, "availability": {"status": "failed", "statusReason": f"The data file could not be read: {exc}"}}

    def _render_image(self, observation_id: str, rec: dict, grid: imaging.GridSpec, stretch: str, low_pct: float, high_pct: float,
                      png_path: Path, meta_path: Path) -> dict:
        """Fetch the cutout, resample, render and save. The PNG is written LAST (atomically): its existence means 'done'."""
        import json
        import os
        url = irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)
        cutout = read_cutout(self.cutouts.get(url))
        arr = imaging.to_grid(cutout, grid)
        png, st = imaging.render_png(arr, stretch, low_pct, high_pct)
        if cutout.flags is not None:
            rec["quality"] = {"flags": quality.summarize(cutout.flags), "summary": quality.quality_summary(cutout.flags)}
        wl, bw = self._wave_at(cutout, grid.center_ra, grid.center_dec)
        if wl is not None:
            rec["wavelengthAtObject"] = {"um": wl, **({"bandwidthUm": bw} if bw is not None else {})}
        self.registry.put_observation(observation_id, rec)
        meta_path.write_text(json.dumps(st))
        tmp = png_path.with_name(png_path.name + ".tmp")
        tmp.write_bytes(png)
        os.replace(tmp, png_path)
        return st

    def get_image(self, observation_id: str, *, stretch: str = "asinh", low_pct: float = 1.0, high_pct: float = 99.5,
                  center_ra: float | None = None, center_dec: float | None = None, fov_arcmin: float = imaging.DEFAULT_FOV_ARCMIN,
                  wait_s: float | None = None) -> dict:
        import json
        rec = self.registry.get_observation(observation_id)
        if rec is None:
            raise NotFound("Unknown observation.")
        if stretch not in imaging.STRETCHES or not (0 <= low_pct < high_pct <= 100):
            raise BadRequest("Invalid stretch or percentiles.")
        grid = self._grid(rec, center_ra, center_dec, fov_arcmin)
        base = {"observationId": observation_id, "generatedAt": now_iso()}

        name = f"{observation_id}_{grid.grid_id}_{stretch}_{low_pct:g}_{high_pct:g}"
        png_path, meta_path = self.images_dir / f"{name}.png", self.images_dir / f"{name}.json"
        if png_path.exists() and meta_path.exists():
            st = json.loads(meta_path.read_text())
        else:
            created = False
            with self._lock:
                fut = self._jobs.get(name)
                if fut is None:
                    fut = self._pool.submit(self._render_image, observation_id, rec, grid, stretch, low_pct, high_pct, png_path, meta_path)
                    self._jobs[name] = fut
                    created = True
            if created:                       # outside the lock: a finished future runs its callback immediately, and that takes the lock
                fut.add_done_callback(lambda f, n=name: self._job_done(n))
            try:
                st = fut.result(timeout=self.image_wait_s if wait_s is None else wait_s)
            except FutureTimeout:
                return {**base, "availability": {"status": "processing", "retryAfterSeconds": 3,
                                                 "statusReason": "Fetching this image from the SPHEREx archive and preparing it."}}
            except (irsa.IrsaError, FitsReadError) as exc:
                return self._failure(base, exc)
        return {**base, "availability": {"status": "available"}, "imageUrl": f"/media/images/{name}.png",
                "width": grid.npix, "height": grid.npix, "stretch": st, "wcs": grid.wcs_summary(), "gridId": grid.grid_id}

    def _job_done(self, name: str) -> None:
        with self._lock:
            self._jobs.pop(name, None)

    def pixel_to_sky(self, observation_id: str, x: float, y: float, *, center_ra: float | None = None,
                     center_dec: float | None = None, fov_arcmin: float = imaging.DEFAULT_FOV_ARCMIN) -> dict:
        rec = self.registry.get_observation(observation_id)
        if rec is None:
            raise NotFound("Unknown observation.")
        grid = self._grid(rec, center_ra, center_dec, fov_arcmin)
        ra, dec = grid.raster_to_sky(x, y)
        return {"sky": {"raDeg": ra, "decDeg": dec}}

    # ---- spectrum --------------------------------------------------------------------------------------------------
    def _spectrum_stub(self, obj: dict, sid: str, ra, dec, radius: float, status: dict) -> dict:
        pos = {"raDeg": ra, "decDeg": dec} if ra is not None else obj["coordinates"]
        return {"spectrumId": sid, "objectId": obj["objectId"], "availability": status, "position": pos, "apertureRadiusArcsec": radius,
                "extractionMethod": "Aperture photometry on each SPHEREx observation, one point per band.", "wavelengthUnit": "um",
                "fluxUnit": "mJy", "points": [], "warnings": [], "generatedAt": now_iso()}

    def get_spectrum(self, object_id: str, *, ra: float | None = None, dec: float | None = None,
                     radius: float = spectrum.DEFAULT_RADIUS_ARCSEC, wait_s: float | None = None) -> dict:
        if (ra is None) != (dec is None):
            raise BadRequest("Provide both ra and dec, or neither.")
        if ra is not None:
            try:
                ra, dec = irsa.validate_position(ra, dec)
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc
        if not (3.0 <= radius <= 60.0):
            raise BadRequest("apertureRadiusArcsec must be between 3 and 60.")
        obj = self.registry.get_object(object_id)
        if obj is None:
            raise NotFound("Unknown object.")
        sid = spectrum.spectrum_id(object_id, ra, dec, radius)
        stored = self.registry.get_spectrum(sid)
        if stored and (not stored.get("partial") or self._age_s(stored.get("savedAt")) < self.spectrum_partial_ttl_s):
            return stored["result"]
        created = False
        with self._lock:
            fut = self._spectrum_jobs.get(sid)
            if fut is None:
                fut = self._spectrum_pool.submit(self._build_spectrum, obj, ra, dec, radius, sid)
                self._spectrum_jobs[sid] = fut
                created = True
        if created:
            fut.add_done_callback(lambda f, k=sid: self._spectrum_done(k))
        try:
            return fut.result(timeout=self.spectrum_wait_s if wait_s is None else wait_s)
        except FutureTimeout:
            return self._spectrum_stub(obj, sid, ra, dec, radius, {"status": "processing", "retryAfterSeconds": 3,
                                                                    "statusReason": "Measuring this position in many SPHEREx observations."})
        except irsa.IrsaError as exc:
            raise Upstream(str(exc), exc.retryable) from exc

    def _build_spectrum(self, obj: dict, ra, dec, radius: float, sid: str) -> dict:
        result, partial = spectrum.build(self, obj, ra, dec, radius, sid, now_iso)
        if result["availability"]["status"] == "available":
            # A complete spectrum is kept for good. One that is missing frames because the archive was slow or down is only kept briefly,
            # so the next visit tries again instead of showing an old gap for ever.
            self.registry.put_spectrum(sid, {"result": result, "partial": partial, "savedAt": now_iso()})
        return result

    def _spectrum_done(self, sid: str) -> None:
        with self._lock:
            self._spectrum_jobs.pop(sid, None)

    def get_stored_spectrum(self, spectrum_id: str) -> dict:
        rec = self.registry.get_spectrum(spectrum_id)
        if rec is None:
            raise NotFound("Unknown spectrum.")
        return rec["result"]

    def prewarm_spectra(self, names: list[str] | None = None) -> int:
        """Build the spectrum of each curated target one after another (a background job started at server start), so the first visitor to a
        Spectrum page finds it ready. Failures are logged, never raised. Returns how many were built."""
        built = 0
        for name in names or [t.name for t in targets.TARGETS]:
            try:
                obj = self.resolve(q=name)
                res = self.get_spectrum(obj["objectId"], wait_s=600)
                built += res["availability"]["status"] == "available"
            except Exception as exc:                                  # a failed warm-up must never take the server down
                log.warning("spectrum pre-warm for %s failed: %s", name, exc)
        return built

    # ---- candidates ------------------------------------------------------------------------------------------------
    def compute_candidates(self, object_id: str) -> dict:
        """Run the candidate search now (blocking) and store the result. Used by the background job and by the bundle script."""
        obj = self.registry.get_object(object_id)
        if obj is None:
            raise NotFound("Unknown object.")
        found, info = candidates.build(self, obj, now_iso)
        run = {"computedAt": now_iso(), "note": info["note"], "partial": info["partial"], "candidateIds": [c["candidateId"] for c in found]}
        self.registry.put_candidates(object_id, found, run)
        return run

    def _candidate_job_done(self, object_id: str) -> None:
        with self._lock:
            self._candidate_jobs.pop(object_id, None)

    def get_candidates(self, object_id: str, *, limit: int = 20, offset: int = 0, min_score: float | None = None, sort: str = "-score") -> dict:
        if not (1 <= limit <= 100) or offset < 0:
            raise BadRequest("limit must be 1-100 and offset >= 0.")
        if sort not in ("-score", "score"):
            raise BadRequest("sort must be -score or score.")
        if self.registry.get_object(object_id) is None:
            raise NotFound("Unknown object.")
        run = self.registry.get_candidate_run(object_id)
        fresh = run is not None and (not run.get("partial") or self._age_s(run.get("computedAt")) < self.candidate_partial_ttl_s)
        running = False
        if not fresh:
            created = False
            with self._lock:
                if object_id not in self._candidate_jobs:
                    fut = self._candidate_pool.submit(self.compute_candidates, object_id)
                    self._candidate_jobs[object_id] = fut
                    created = True
                running = True
            if created:
                fut.add_done_callback(lambda f, k=object_id: self._candidate_job_done(k))
        items = self.registry.list_candidates(object_id)
        if min_score is not None:
            items = [c for c in items if c["score"] >= min_score]
        items.sort(key=lambda c: c["score"], reverse=(sort == "-score"))
        out = {"items": items[offset:offset + limit], "total": len(items), "limit": limit, "offset": offset}
        if not items:
            out["coverageNote"] = ("Looking for candidates in this target's observations. This can take a minute; reload to see the result."
                                   if running else (run or {}).get("note") or "No candidates were found.")
        return out

    def get_candidate(self, candidate_id: str) -> dict:
        c = self.registry.get_candidate(candidate_id)
        if c is None:
            raise NotFound("Unknown candidate.")
        return c

    def library(self, fresh: bool = False) -> dict:
        from . import library
        return library.build(self, fresh)

    def get_guided(self, guided_id: str, object_id: str | None = None) -> dict:
        if guided_id != "spot-the-shift":
            raise NotFound("Unknown guided investigation.")
        return guided.spot_the_shift(self, object_id)

    # ---- measurements --------------------------------------------------------------------------------------------
    def _cutout_for_measurement(self, rec: dict, ra: float, dec: float, outer_arcsec: float, quick: bool = False):
        """Prefer the object-centred cutout the viewer already fetched; fall back to one centred on the region.
        `quick` limits a download to one retry and 15 s (used by the spectrum, which fetches many frames in one request)."""
        kw = {"retries": 1, "timeout_s": 15.0} if quick else {}
        grid = self._grid(rec, None, None, imaging.DEFAULT_FOV_ARCMIN)
        cutout = read_cutout(self.cutouts.get(irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg), **kw))
        if photometry.fits_in_image(cutout, ra, dec, outer_arcsec):
            return cutout
        size = min(1.0, max(0.05, 2 * outer_arcsec * 1.15 / 3600.0))
        return read_cutout(self.cutouts.get(irsa.cutout_url(rec["file"]["uri"], ra, dec, size), **kw))

    def _measure_one(self, rec: dict, ra: float, dec: float, radius: float):
        """Fetch the cutout and run aperture photometry. Raises Upstream / DataUnavailable with user-safe messages."""
        annulus = (2 * radius, 3 * radius)
        try:
            cutout = self._cutout_for_measurement(rec, ra, dec, annulus[1])
            return cutout, photometry.aperture_flux(cutout, ra, dec, radius_arcsec=radius, annulus_arcsec=annulus)
        except irsa.IrsaError as exc:
            raise Upstream(str(exc), exc.retryable) from exc
        except FitsReadError as exc:
            raise DataUnavailable(f"The data file could not be read: {exc}") from exc
        except photometry.Unmeasurable as exc:
            raise DataUnavailable(str(exc)) from exc

    @staticmethod
    def _parse_region(region: dict | None) -> tuple[float, float, float]:
        if region is None:
            raise BadRequest("This measurement needs a region (center and radiusArcsec).")
        c, radius = region["center"], float(region["radiusArcsec"])
        try:
            ra, dec = irsa.validate_position(c["raDeg"], c["decDeg"])
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        return ra, dec, radius

    def create_measurement(self, kind: str, observation_ids: list[str], region: dict | None) -> dict:
        if kind not in ("aperture_flux", "flux_change"):
            raise BadRequest(f"Measurement kind '{kind}' is not supported yet; available: aperture_flux, flux_change.")
        want = 1 if kind == "aperture_flux" else 2
        if len(observation_ids) != want or len(set(observation_ids)) != want:
            raise BadRequest(f"{kind} needs exactly {want} different observation id{'s' if want > 1 else ''}.")
        recs = []
        for oid in observation_ids:
            rec = self.registry.get_observation(oid)
            if rec is None:
                raise NotFound("Unknown observation.")
            recs.append(rec)
        ra, dec, radius = self._parse_region(region)
        mid = "meas_" + hashlib.sha1(f"{kind}|{'|'.join(observation_ids)}|{ra:.6f}|{dec:.6f}|{radius:.3f}".encode()).hexdigest()[:10]
        existing = self.registry.get_measurement(mid)
        if existing:
            return existing
        out = (self._aperture_flux_measurement(mid, observation_ids[0], recs[0], ra, dec, radius) if kind == "aperture_flux"
               else self._flux_change_measurement(mid, observation_ids, recs, ra, dec, radius))
        self.registry.put_measurement(mid, out)
        return out

    def _aperture_flux_measurement(self, mid: str, obs_id: str, rec: dict, ra: float, dec: float, radius: float) -> dict:
        cutout, res = self._measure_one(rec, ra, dec, radius)
        out = {"measurementId": mid, "kind": "aperture_flux", "value": res.flux_mjy, "unit": photometry.FLUX_UNIT,
               "uncertainty": res.uncertainty_mjy, "uncertaintyBasis": res.uncertainty_basis, "signalToNoise": res.snr,
               "observationIds": [obs_id], "region": {"center": {"raDeg": ra, "decDeg": dec}, "radiusArcsec": radius},
               "method": res.method, "qualityFlags": res.quality_flags, "warnings": list(cutout.warnings) + res.warnings,
               "createdAt": now_iso()}
        if res.wavelength_um is not None:
            out["wavelengthUm"] = res.wavelength_um
        if res.bandwidth_um is not None:
            out["bandwidthUm"] = res.bandwidth_um
        return out

    def _flux_change_measurement(self, mid: str, ids: list[str], recs: list[dict], ra: float, dec: float, radius: float) -> dict:
        """target minus base, in mJy, for one sky region. Refused unless the two frames are comparable at matched wavelength."""
        (cut_b, res_b), (cut_t, res_t) = self._measure_one(recs[0], ra, dec, radius), self._measure_one(recs[1], ra, dec, radius)
        verdict = compare.assess(self._side(recs[0], cut_b, (res_b.wavelength_um, res_b.bandwidth_um)),
                                 self._side(recs[1], cut_t, (res_t.wavelength_um, res_t.bandwidth_um)), "difference")
        checks = {c["name"]: c for c in verdict["checks"]}
        if verdict["verdict"] == "not_comparable" or checks["wavelength"]["result"] != "pass":
            raise DataUnavailable("A flux change was not computed because the two frames cannot be compared at the same wavelength. "
                                  + checks["wavelength"]["detail"]
                                  + " Measure each frame separately (aperture_flux) and compare them as a spectrum instead.")
        value = res_t.flux_mjy - res_b.flux_mjy
        stat = math.sqrt(res_b.uncertainty_mjy ** 2 + res_t.uncertainty_mjy ** 2)
        sys_floor = photometry.SYSTEMATIC_FRACTION * math.hypot(res_b.flux_mjy, res_t.flux_mjy)
        sigma = math.sqrt(stat ** 2 + sys_floor ** 2)
        snr = value / sigma
        warnings = [f"Base frame: {w}" for w in res_b.warnings] + [f"Target frame: {w}" for w in res_t.warnings]
        warnings += [f"{c['name']}: {c['detail']}" for c in verdict["checks"]
                     if c["result"] in ("warn", "notEvaluated") and c["name"] not in ("wavelength", "alignment")]
        warnings.append("alignment: Each frame was measured on its own pixels, with the position taken from that frame's own sky coordinates; "
                        "the frame-to-frame alignment was not measured for this number.")
        if res_b.snr < photometry.LOW_SNR and res_t.snr < photometry.LOW_SNR:
            warnings.append("Neither frame significantly detects a source here, so a change in brightness is not meaningful.")
        if abs(snr) < photometry.LOW_SNR:
            warnings.append(f"The change is {abs(snr):.1f} times its uncertainty (within {photometry.LOW_SNR:g} sigma), so it is consistent with no change.")
        else:
            warnings.append(f"The change is {abs(snr):.1f} times its uncertainty. That does not by itself show the source varied: "
                            "calibration, pointing, resampling and background differences are not ruled out.")
        flags = ([{**f, "description": f"Base frame: {f['description']}"} for f in res_b.quality_flags]
                 + [{**f, "description": f"Target frame: {f['description']}"} for f in res_t.quality_flags])
        return {"measurementId": mid, "kind": "flux_change", "value": value, "unit": photometry.FLUX_UNIT, "uncertainty": sigma,
                "uncertaintyBasis": (f"Quadrature sum of the two statistical aperture-flux uncertainties ({stat:.3g} mJy, the frames are independent exposures) "
                                     f"and a systematic floor of {photometry.SYSTEMATIC_FRACTION * 100:g} percent of each frame's flux ({sys_floor:.3g} mJy), "
                                     "the repeatability observed between frames at matching wavelength. "
                                     f"Base: {res_b.uncertainty_basis} Target: {res_t.uncertainty_basis}"),
                "signalToNoise": snr, "observationIds": list(ids),
                "region": {"center": {"raDeg": ra, "decDeg": dec}, "radiusArcsec": radius},
                "method": (f"Target minus base. Both measured with the same aperture. Base: {res_b.method} Target: {res_t.method} "
                           f"The two frames sample the object at {res_b.wavelength_um:.3f} and {res_t.wavelength_um:.3f} um, matched to within a quarter of one bandwidth."),
                "qualityFlags": flags, "warnings": warnings, "createdAt": now_iso()}

    def get_measurement(self, measurement_id: str) -> dict:
        rec = self.registry.get_measurement(measurement_id)
        if rec is None:
            raise NotFound("Unknown measurement.")
        return rec

    # ---- stacks (co-added images) ----------------------------------------------------------------------------------
    def get_coadd(self, object_id: str, *, bandpass: str = "SPHEREx-D3", fov_arcmin: float = imaging.DEFAULT_FOV_ARCMIN, max_frames: int = 16) -> dict:
        """A stacked picture of one object in one detector band, built ONLY from frames already stored on this server (never waits for
        the archive), using only frames that sample the object at matching wavelengths. See processing/coadd.py for the method.
        Frontend-only operation, not in the contract: the result says plainly that it is a derived stack and lists its inputs."""
        import hashlib
        import os
        if not re.fullmatch(r"SPHEREx-D[1-6]", bandpass or ""):
            raise BadRequest("bandpass must be SPHEREx-D1 to SPHEREx-D6.")
        if not (2 <= max_frames <= 40):
            raise BadRequest("maxFrames must be 2-40.")
        obj = self.get_object(object_id)
        items = self.list_observations(object_id, bandpass=bandpass, limit=200)["items"]
        order = [o["observationId"] for o in items]
        recs = self.registry.get_observations(order)
        rejected = {"not stored here": 0, "poor quality": 0, "wavelength unknown": 0}
        ok: list[tuple[str, float, float]] = []
        grid = None
        for oid in order:
            rec = recs.get(oid)
            if rec is None:
                continue
            grid = grid or self._grid(rec, None, None, fov_arcmin)
            url = irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)
            if not self.cutouts.is_cached(url):
                rejected["not stored here"] += 1
            elif (rec.get("quality") or {}).get("summary") == "poor":
                rejected["poor quality"] += 1
            elif not rec.get("wavelengthAtObject") or rec["wavelengthAtObject"].get("bandwidthUm") is None:
                rejected["wavelength unknown"] += 1
            else:
                ok.append((oid, rec["wavelengthAtObject"]["um"], rec["wavelengthAtObject"]["bandwidthUm"]))
        group = coadd.best_wavelength_group(ok, compare.WAVELENGTH_MATCH_FRACTION)
        mismatched = len(ok) - len(group)
        if len(group) > max_frames:
            wl = {i: w for i, w, _ in ok}
            med = float(np.median([wl[i] for i in group]))
            group = sorted(sorted(group, key=lambda i: abs(wl[i] - med))[:max_frames], key=order.index)
        base = {"objectId": object_id, "bandpass": bandpass, "kind": "derived_stack", "generatedAt": now_iso()}
        cid = "coadd_" + hashlib.sha1(f"v{coadd.METHOD_VERSION}|{object_id}|{bandpass}|{fov_arcmin:.4f}|{','.join(sorted(group))}".encode()).hexdigest()[:10]
        stored = self.registry.get_coadd(cid)
        png_path = self.images_dir / f"{cid}.png"
        if stored and png_path.exists():
            return stored
        if len(group) < coadd.MIN_FRAMES:
            why = (f"Only {len(group)} stored frame(s) in {bandpass} sample this object at matching wavelengths; a stack needs at least {coadd.MIN_FRAMES}. "
                   f"Left out: {rejected['not stored here']} not stored on this server, {rejected['poor quality']} of poor quality, "
                   f"{rejected['wavelength unknown']} without a known wavelength, {mismatched} at a different wavelength.")
            return {**base, "coaddId": cid, "availability": {"status": "unavailable", "statusReason": why}, "inputObservationIds": group}
        arrays = [self._grid_array(recs[i], grid)[0] for i in group]
        try:
            res = coadd.stack(arrays)
        except ValueError as exc:
            return {**base, "coaddId": cid, "availability": {"status": "unavailable", "statusReason": str(exc)}, "inputObservationIds": group}
        png, st = imaging.render_png(res.image)
        tmp = png_path.with_name(png_path.name + ".tmp")
        tmp.write_bytes(png)
        os.replace(tmp, png_path)
        wl = {i: (w, b) for i, w, b in ok}
        wls = [wl[i][0] for i in group]
        bws = [wl[i][1] for i in group]
        dates = sorted(recs[i]["observation"]["observedAt"] for i in group if recs[i]["observation"].get("observedAt"))
        warnings = ["This is a derived picture made by averaging several observations. It is not a NASA observation and it is not a single date.",
                    "The pictures were aligned with the archive's own sky coordinates only (no extra shifting). Each frame's sky background was removed, "
                    f"frames were weighted by their noise, and values more than {coadd.SIGMA_CLIP:g} noise units from the typical value were dropped "
                    f"({res.clipped_fraction * 100:.2f} percent of values) to remove cosmic-ray hits.",
                    "The wavelength changes across each frame (by more than one bandwidth over 15 arcminutes in the example we checked), so frames were "
                    "matched at the object's own position only; other parts of the picture blend slightly different wavelengths."]
        pm = obj.get("properMotion") or {}
        if len(dates) > 1 and pm.get("pmRaMasPerYr") is not None and pm.get("pmDecMasPerYr") is not None:
            years = (iso_to_mjd(dates[-1]) - iso_to_mjd(dates[0])) / 365.25
            moved = float(np.hypot(pm["pmRaMasPerYr"], pm["pmDecMasPerYr"])) / 1000.0 * years
            if moved > 3.0:
                warnings.append(f"This object moves about {moved:.1f} arcseconds across these dates (catalogue proper motion), so it is smeared in the stack; "
                                "use the stack for the surrounding stars and faint background, not for the moving object.")
        warnings.append("Use the stack to look at faint things, not to measure brightness: stars differ a little between dates (the wavelength at each pixel changes), "
                        "so bright pixels are partly clipped. Brightness measurements in this app always come from single frames.")
        if mismatched:
            warnings.append(f"{mismatched} stored frame(s) were left out because they sample the object at a different wavelength.")
        if res.frames_used >= 4 and res.measured_gain < 1.2:
            warnings.append("The pixel-to-pixel noise barely improved, so these frames are probably not independent enough (or too few) to gain from stacking.")
        out = {**base, "coaddId": cid, "availability": {"status": "available"}, "imageUrl": f"/media/images/{cid}.png",
               "width": int(res.image.shape[1]), "height": int(res.image.shape[0]),
               "stretch": st, "inputObservationIds": group, "frameCount": res.frames_used,
               "observedFrom": dates[0] if dates else None, "observedTo": dates[-1] if dates else None,
               "wavelengthUm": {"min": min(wls), "max": max(wls), "median": float(np.median(wls)), "bandwidthUm": float(np.median(bws))},
               "noise": {"singleFrame": res.single_frame_sigma, "stack": res.stack_sigma, "measuredGain": res.measured_gain, "idealGain": res.ideal_gain,
                         "largeScaleGain": res.large_scale_gain, "unit": "MJy/sr",
                         "note": "Noise is the pixel-to-pixel scatter (slowly varying structure removed), measured the same way in a single frame and in the stack. "
                                 "The ideal gain is what averaging this many independent frames would give. The large-scale gain is the same ratio with smooth "
                                 "structure included; it is smaller because that structure differs from date to date and does not average down like noise."},
               "leftOut": {**rejected, "different wavelength": mismatched},
               "method": f"Weighted mean of {res.frames_used} {bandpass} frames matched in wavelength at the object (within {compare.WAVELENGTH_MATCH_FRACTION:g} of one bandwidth).",
               "warnings": warnings}
        self.registry.put_coadd(cid, out)
        return out

    # ---- comparisons ---------------------------------------------------------------------------------------------
    def _grid_array(self, rec: dict, grid: imaging.GridSpec):
        url = irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)
        cutout = read_cutout(self.cutouts.get(url))
        return imaging.to_grid(cutout, grid), cutout

    @staticmethod
    def _wave_at(cutout, ra: float, dec: float) -> tuple[float | None, float | None]:
        """Wavelength and bandwidth (um) at the object's pixel, or (None, None) when unavailable."""
        if cutout.wavelength_um is None:
            return None, None
        try:
            x, y = photometry.sky_to_pixel(cutout, ra, dec)
        except photometry.Unmeasurable:
            return None, None
        ny, nx = cutout.wavelength_um.shape
        i, j = int(round(y)), int(round(x))
        if not (0 <= i < ny and 0 <= j < nx):
            return None, None
        wl, bw = float(cutout.wavelength_um[i, j]), float(cutout.bandwidth_um[i, j])
        return (wl if np.isfinite(wl) else None), (bw if np.isfinite(bw) else None)

    @staticmethod
    def _side(rec: dict, cutout, wave: tuple[float | None, float | None] = (None, None)) -> compare.Side:
        obs = rec["observation"]
        zodi = None
        if cutout.zodi is not None and np.isfinite(cutout.zodi).any():
            zodi = float(np.nanmedian(cutout.zodi))
        q = quality.quality_summary(cutout.flags) if cutout.flags is not None else "unknown"
        return compare.Side(obs.get("bandpass"), obs.get("dataRelease"), q, zodi, wave[0], wave[1])

    def create_comparison(self, base_id: str, target_id: str, mode: str) -> dict:
        if mode not in ("blink", "overlay", "difference"):
            raise BadRequest("mode must be blink, overlay or difference.")
        if base_id == target_id:
            raise BadRequest("Choose two different observations.")
        base, target = self.registry.get_observation(base_id), self.registry.get_observation(target_id)
        if base is None or target is None:
            raise NotFound("Unknown observation.")
        grid = self._grid(base, None, None, imaging.DEFAULT_FOV_ARCMIN)
        cid = "cmp_" + hashlib.sha1(f"{base_id}|{target_id}|{mode}|{grid.grid_id}".encode()).hexdigest()[:12]
        existing = self.registry.get_comparison(cid)
        if existing:
            return existing

        out = {"comparisonId": cid, "mode": mode, "baseObservationId": base_id, "targetObservationId": target_id,
               "measurements": [], "warnings": [], "createdAt": now_iso()}
        try:
            arr_b, cut_b = self._grid_array(base, grid)
            arr_t, cut_t = self._grid_array(target, grid)
        except irsa.IrsaError as exc:      # failures are reported in the resource and NOT stored, so a retry can succeed
            return {**out, "availability": {"status": "failed" if exc.retryable else "unavailable", "statusReason": str(exc),
                                            **({"retryAfterSeconds": 30} if exc.retryable else {})},
                    "compatibility": {"verdict": "unknown", "checks": []}}
        except FitsReadError as exc:
            return {**out, "availability": {"status": "failed", "statusReason": f"A data file could not be read: {exc}"},
                    "compatibility": {"verdict": "unknown", "checks": []}}

        est = alignment.estimate_offset(arr_b, arr_t, grid.scale_arcsec)
        a = compare.assess(self._side(base, cut_b, self._wave_at(cut_b, grid.center_ra, grid.center_dec)),
                           self._side(target, cut_t, self._wave_at(cut_t, grid.center_ra, grid.center_dec)), mode, est)
        out.update(availability={"status": "available"}, comparisonKind=a["comparisonKind"], warnings=a["warnings"],
                   compatibility={"verdict": a["verdict"], "checks": a["checks"]},
                   alignment={"method": "Each cutout was resampled (bilinear) onto a common north-up TAN grid using its own WCS."
                                        + (" Residual = median offset of matched bright stars between the two frames." if est.measured else ""),
                              **({"residualArcsec": est.residual_arcsec} if est.measured else {})})
        if a["verdict"] != "not_comparable":
            out["baseImage"] = self.get_image(base_id, wait_s=300)      # wait: a 'processing' stub must never be stored
            out["targetImage"] = self.get_image(target_id, wait_s=300)
            if mode == "difference":
                png, st = imaging.render_difference_png(arr_t - arr_b)
                name = f"{cid}_diff"
                (self.images_dir / f"{name}.png").write_bytes(png)
                out["differenceImage"] = {
                    "observationId": target_id, "availability": {"status": "available"}, "imageUrl": f"/media/images/{name}.png",
                    "width": grid.npix, "height": grid.npix, "stretch": st, "wcs": grid.wcs_summary(), "gridId": grid.grid_id,
                    "generatedAt": now_iso()}
        self.registry.put_comparison(cid, out)
        return out

    def get_comparison(self, comparison_id: str) -> dict:
        rec = self.registry.get_comparison(comparison_id)
        if rec is None:
            raise NotFound("Unknown comparison.")
        return rec
