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
import math
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from processing import alignment, compare, imaging, irsa, photometry, quality
from processing.fits_io import FitsReadError, read_cutout
from store.registry import Registry

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
                 discovery_ttl_s: float = 600.0):
        self.registry, self.cutouts = registry, cutouts
        self.images_dir = Path(images_dir)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self._discover, self._resolver = discover, resolver
        self._ttl = discovery_ttl_s
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
        r_ra, r_dec = self._resolver(q.strip())
        return self._make_object(q.strip(), r_ra, r_dec, "CDS Sesame via astropy", q.strip())

    def get_object(self, object_id: str) -> dict:
        obj = self.registry.get_object(object_id)
        if obj is None:
            raise NotFound("Unknown object.")
        return obj

    # ---- observations --------------------------------------------------------------------------------------------
    def _discover_cached(self, ra: float, dec: float) -> list[irsa.ArchiveFile]:
        key = (round(ra, 4), round(dec, 4))
        with self._lock:
            hit = self._disc_cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        try:
            files = self._discover(ra, dec)
        except irsa.IrsaError as exc:
            raise Upstream(str(exc), exc.retryable) from exc
        with self._lock:
            self._disc_cache[key] = (time.monotonic(), files)
        return files

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
        files = self._discover_cached(c["raDeg"], c["decDeg"])

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
        for f in keep:
            oid = observation_id_for(f.uri)
            prior = self.registry.get_observation(oid)
            observation = self._observation_dict(f, obj, (prior or {}).get("quality"))
            object_ids = sorted(set((prior or {}).get("observation", {}).get("objectIds", [])) | {object_id})
            observation["objectIds"] = object_ids
            self.registry.put_observation(oid, {"observation": observation, "file": asdict(f), "quality": (prior or {}).get("quality")})
            items.append(observation)

        note = None
        if not files:
            note = "No SPHEREx observations were found covering this position."
        elif not keep:
            note = "Observations exist here, but none match the filters."
        page = items[offset:offset + limit]
        out = {"items": page, "total": len(items), "limit": limit, "offset": offset}
        if note:
            out["coverageNote"] = note
        return out

    def get_observation(self, observation_id: str) -> dict:
        rec = self.registry.get_observation(observation_id)
        if rec is None:
            raise NotFound("Unknown observation.")
        obs = dict(rec["observation"])
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
            center_ra, center_dec = obj["coordinates"]["raDeg"], obj["coordinates"]["decDeg"]
        try:
            irsa.validate_position(center_ra, center_dec)
            return imaging.GridSpec(center_ra, center_dec, fov_arcmin)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

    def get_image(self, observation_id: str, *, stretch: str = "asinh", low_pct: float = 1.0, high_pct: float = 99.5,
                  center_ra: float | None = None, center_dec: float | None = None, fov_arcmin: float = imaging.DEFAULT_FOV_ARCMIN) -> dict:
        rec = self.registry.get_observation(observation_id)
        if rec is None:
            raise NotFound("Unknown observation.")
        if stretch not in imaging.STRETCHES or not (0 <= low_pct < high_pct <= 100):
            raise BadRequest("Invalid stretch or percentiles.")
        grid = self._grid(rec, center_ra, center_dec, fov_arcmin)
        base = {"observationId": observation_id, "generatedAt": now_iso()}

        name = f"{observation_id}_{grid.grid_id}_{stretch}_{low_pct:g}_{high_pct:g}"
        png_path, meta_path = self.images_dir / f"{name}.png", self.images_dir / f"{name}.json"
        try:
            if not png_path.exists():
                url = irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)
                cutout = read_cutout(self.cutouts.get(url))
                arr = imaging.to_grid(cutout, grid)
                png, st = imaging.render_png(arr, stretch, low_pct, high_pct)
                png_path.write_bytes(png)
                import json
                meta_path.write_text(json.dumps(st))
                if cutout.flags is not None:
                    rec["quality"] = {"flags": quality.summarize(cutout.flags), "summary": quality.quality_summary(cutout.flags)}
                    self.registry.put_observation(observation_id, rec)
            else:
                import json
                st = json.loads(meta_path.read_text())
        except irsa.IrsaError as exc:
            status = "failed" if exc.retryable else "unavailable"
            return {**base, "availability": {"status": status, "statusReason": str(exc),
                                             **({"retryAfterSeconds": 30} if exc.retryable else {})}}
        except FitsReadError as exc:
            return {**base, "availability": {"status": "failed", "statusReason": f"The data file could not be read: {exc}"}}

        return {**base, "availability": {"status": "available"}, "imageUrl": f"/media/images/{name}.png",
                "width": grid.npix, "height": grid.npix, "stretch": st, "wcs": grid.wcs_summary(), "gridId": grid.grid_id}

    def pixel_to_sky(self, observation_id: str, x: float, y: float, *, center_ra: float | None = None,
                     center_dec: float | None = None, fov_arcmin: float = imaging.DEFAULT_FOV_ARCMIN) -> dict:
        rec = self.registry.get_observation(observation_id)
        if rec is None:
            raise NotFound("Unknown observation.")
        grid = self._grid(rec, center_ra, center_dec, fov_arcmin)
        ra, dec = grid.raster_to_sky(x, y)
        return {"sky": {"raDeg": ra, "decDeg": dec}}

    # ---- measurements --------------------------------------------------------------------------------------------
    def _cutout_for_measurement(self, rec: dict, ra: float, dec: float, outer_arcsec: float):
        """Prefer the object-centred cutout the viewer already fetched; fall back to one centred on the region."""
        grid = self._grid(rec, None, None, imaging.DEFAULT_FOV_ARCMIN)
        cutout = read_cutout(self.cutouts.get(irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)))
        if photometry.fits_in_image(cutout, ra, dec, outer_arcsec):
            return cutout
        size = min(1.0, max(0.05, 2 * outer_arcsec * 1.15 / 3600.0))
        return read_cutout(self.cutouts.get(irsa.cutout_url(rec["file"]["uri"], ra, dec, size)))

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
        sigma = math.sqrt(res_b.uncertainty_mjy ** 2 + res_t.uncertainty_mjy ** 2)
        snr = value / sigma
        warnings = [f"Base frame: {w}" for w in res_b.warnings] + [f"Target frame: {w}" for w in res_t.warnings]
        warnings += [f"{c['name']}: {c['detail']}" for c in verdict["checks"] if c["result"] in ("warn", "notEvaluated") and c["name"] != "wavelength"]
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
                "uncertaintyBasis": "Quadrature sum of the two aperture-flux uncertainties (the frames are independent exposures). "
                                    f"Base: {res_b.uncertainty_basis} Target: {res_t.uncertainty_basis}",
                "signalToNoise": snr, "observationIds": list(ids),
                "region": {"center": {"raDeg": ra, "decDeg": dec}, "radiusArcsec": radius},
                "method": (f"Target minus base. Both measured with the same aperture. Base: {res_b.method} Target: {res_t.method} "
                           f"The two frames sample the object at {res_b.wavelength_um:.3f} and {res_t.wavelength_um:.3f} um, matched within one bandwidth."),
                "qualityFlags": flags, "warnings": warnings, "createdAt": now_iso()}

    def get_measurement(self, measurement_id: str) -> dict:
        rec = self.registry.get_measurement(measurement_id)
        if rec is None:
            raise NotFound("Unknown measurement.")
        return rec

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
            out["baseImage"] = self.get_image(base_id)
            out["targetImage"] = self.get_image(target_id)
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
