"""Catalogue overlays: stars from Gaia DR3 and AllWISE inside an object's field, placed on the image grid (P2-08).

Frontend-only operation (not in the contract): GET /objects/{id}/catalog?source=gaia|allwise.

Provenance is explicit: every source comes from a named public catalogue served by CDS VizieR, with its table name, the catalogue's own position epoch,
and the epoch the positions were carried to (using the catalogue's own proper motions, so a nearby moving star is drawn where it is at the
SPHEREx epoch, not where it was in 2016). Nothing here is measured by this app.

VERIFICATION STATUS (2026-11-15): the query goes through astroquery's Vizier client with the table and column names documented by CDS. The
CDS services could not be reached from the development machine on the day this was written, so the live query has NOT been run against
real data; the conversion, propagation, grid placement and failure handling are tested on injected rows. A catalogue result, once fetched,
is stored and reused (valid for 30 days), so it keeps working when the network does not.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from processing import imaging

SOURCES: dict[str, dict] = {
    "gaia": {"name": "Gaia DR3", "table": "I/355/gaiadr3", "catalog_epoch": 2016.0, "mag_band": "G", "mag_limit": 18.0,
             "columns": {"id": "Source", "ra": "RA_ICRS", "dec": "DE_ICRS", "pmra": "pmRA", "pmdec": "pmDE", "plx": "Plx", "mag": "Gmag"},
             "url": "https://www.cosmos.esa.int/web/gaia/dr3"},
    "allwise": {"name": "AllWISE", "table": "II/328/allwise", "catalog_epoch": 2010.5, "mag_band": "W1", "mag_limit": 15.0,
                "columns": {"id": "AllWISE", "ra": "RAJ2000", "dec": "DEJ2000", "pmra": "pmRA", "pmdec": "pmDE", "plx": None, "mag": "W1mag"},
                "url": "https://wise2.ipac.caltech.edu/docs/release/allwise/"},
}
DEFAULT_EPOCH = 2026.0
MAX_SOURCES = 300
CACHE_DAYS = 30.0


class CatalogUnavailable(Exception):
    """The catalogue service could not be reached or answered with an error."""


def fetch_vizier(source: str, ra: float, dec: float, radius_arcmin: float, limit: int) -> list[dict]:
    """Rows of the named catalogue inside a circle, brightest first, as plain dicts with the standard keys (id, ra, dec, pmra, pmdec, plx, mag).
    Raises CatalogUnavailable on any failure."""
    spec = SOURCES[source]
    try:
        import astropy.units as u
        from astroquery.vizier import Vizier
        from astropy.coordinates import SkyCoord
        cols = [c for c in spec["columns"].values() if c]
        v = Vizier(columns=cols, row_limit=limit, column_filters={spec["columns"]["mag"]: f"<{spec['mag_limit']}"}, timeout=20)
        tabs = v.query_region(SkyCoord(ra * u.deg, dec * u.deg), radius=radius_arcmin * u.arcmin, catalog=spec["table"])
    except Exception as exc:
        raise CatalogUnavailable(f"The {spec['name']} catalogue service did not answer ({type(exc).__name__}).") from exc
    if not tabs:
        return []
    t = tabs[0]
    rows = []
    for r in t:
        def val(key):
            col = spec["columns"].get(key)
            if not col or col not in t.colnames:
                return None
            x = r[col]
            try:
                return None if getattr(x, "mask", False) else float(x) if key != "id" else str(x)
            except (TypeError, ValueError):
                return None
        if val("ra") is None or val("dec") is None:
            continue
        rows.append({k: val(k) for k in ("id", "ra", "dec", "pmra", "pmdec", "plx", "mag")})
    rows.sort(key=lambda d: (d["mag"] is None, d["mag"]))
    return rows[:limit]


def propagate(ra: float, dec: float, pmra: float | None, pmdec: float | None, from_epoch: float, to_epoch: float) -> tuple[float, float]:
    """Carry a position along its proper motion (mas/yr; pmra already includes cos(dec), as in Gaia and AllWISE). No motion means no change."""
    if pmra is None or pmdec is None or not (math.isfinite(pmra) and math.isfinite(pmdec)):
        return ra, dec
    dt = to_epoch - from_epoch
    new_dec = dec + pmdec * dt / 3.6e6
    cosd = max(1e-6, math.cos(math.radians(dec)))
    return (ra + pmra * dt / 3.6e6 / cosd) % 360.0, new_dec


def build(svc, object_id: str, source: str, fov_arcmin: float, epoch: float, now_iso, fetch=None) -> dict:
    from .core import BadRequest, Upstream, iso_to_mjd
    if source not in SOURCES:
        raise BadRequest(f"source must be one of: {', '.join(SOURCES)}.")
    if not (0 < fov_arcmin <= imaging.MAX_FOV_ARCMIN):
        raise BadRequest(f"fovArcmin must be in (0, {imaging.MAX_FOV_ARCMIN}].")
    if not (1990.0 <= epoch <= 2040.0):
        raise BadRequest("epoch must be a year between 1990 and 2040.")
    obj = svc.get_object(object_id)
    c = obj.get("viewCenter") or obj["coordinates"]
    grid = imaging.GridSpec(c["raDeg"], c["decDeg"], fov_arcmin)
    spec = SOURCES[source]
    key = f"catalog_{source}_{c['raDeg']:.4f}_{c['decDeg']:.4f}_{fov_arcmin:.3f}"
    stored = svc.registry.get_spectrum(key)               # the registry's generic result table doubles as the catalogue cache
    rows, retrieved = None, None
    if stored and svc._age_s(stored.get("savedAt")) < CACHE_DAYS * 86400:
        rows, retrieved = stored["rows"], stored["savedAt"]
    if rows is None:
        radius = fov_arcmin * 0.5 * math.sqrt(2) * 1.05          # reaches the corners of the picture
        try:
            rows = (fetch or fetch_vizier)(source, grid.center_ra, grid.center_dec, radius, MAX_SOURCES * 2)
        except CatalogUnavailable as exc:
            if stored:                                              # an old copy is better than none; say so below
                rows, retrieved = stored["rows"], stored["savedAt"]
            else:
                raise Upstream(f"{exc} Try again later; nothing is shown rather than a guess.", True) from exc
        else:
            retrieved = now_iso()
            svc.registry.put_spectrum(key, {"rows": rows, "savedAt": retrieved, "result": {"availability": {"status": "available"}, "points": []}, "partial": False})
    out, n = [], grid.npix
    for r in rows:
        ra, dec = propagate(r["ra"], r["dec"], r.get("pmra"), r.get("pmdec"), spec["catalog_epoch"], epoch)
        x, y = grid.sky_to_raster(ra, dec)
        if not (0 <= x < n and 0 <= y < n):
            continue
        item = {"id": r["id"], "sky": {"raDeg": round(ra, 7), "decDeg": round(dec, 7)}, "raster": {"x": round(x, 2), "y": round(y, 2)}}
        if r.get("mag") is not None:
            item["magnitude"] = round(r["mag"], 3)
        if r.get("pmra") is not None and r.get("pmdec") is not None:
            item["properMotion"] = {"pmRaMasPerYr": round(r["pmra"], 3), "pmDecMasPerYr": round(r["pmdec"], 3)}
        if r.get("plx") is not None:
            item["parallaxMas"] = round(r["plx"], 3)
        out.append(item)
    truncated = len(out) > MAX_SOURCES
    out = out[:MAX_SOURCES]
    warnings = [f"These positions come from {spec['name']} (CDS VizieR table {spec['table']}), not from SPHEREx. Magnitudes are {spec['mag_band']}-band, limited to brighter than {spec['mag_limit']:g}.",
                f"Positions were carried from the catalogue epoch {spec['catalog_epoch']:g} to {epoch:g} with the catalogue's own proper motions; sources without a proper motion are not moved."]
    if truncated:
        warnings.append(f"Only the {MAX_SOURCES} brightest sources inside the picture are returned.")
    if stored and retrieved == stored["savedAt"] and rows is stored["rows"] and svc._age_s(stored.get("savedAt")) >= CACHE_DAYS * 86400:
        warnings.append("The catalogue service did not answer, so this is a stored copy older than 30 days.")
    return {"objectId": object_id, "source": source, "catalogName": spec["name"], "availability": {"status": "available"},
            "provenance": {"kind": "catalog", "name": spec["name"], "table": spec["table"], "url": spec["url"], "catalogEpoch": spec["catalog_epoch"],
                           "positionsAtEpoch": epoch, "retrievedAt": retrieved},
            "grid": {"gridId": grid.grid_id, "fovArcmin": fov_arcmin, "width": n, "height": n}, "count": len(out), "truncated": truncated,
            "sources": out, "warnings": warnings}
