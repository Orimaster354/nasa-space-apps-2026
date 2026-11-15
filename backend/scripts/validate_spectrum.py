"""Independent validation of the spectrum calculations (priority 1). Run from backend/:

    PYTHONPATH=. SPX_DATA_DIR=<a data dir with the stored frames> python scripts/validate_spectrum.py [target name ...]

Reads only stored frames (it never needs the archive). It re-measures what the app measured with SEPARATE code and compares:
  A. flux: an independent aperture sum (20 x 20 sub-pixel sampling, own sigma-clipped annulus background, pixel area from the WCS
     Jacobian) against the app's stored spectrum points;
  B. repeatability: frames at matched wavelength (a quarter of a bandwidth) against each other, to check the 3 percent floor;
  C. continuity: the flux jump between neighbouring bands, at detector boundaries versus inside a detector;
  D. aperture: how much flux lies outside the 12 arcsec aperture (the spectrum applies no aperture correction).
It shares only the archive's pixel data and flag definitions with the app; it does not call the app's photometry.
"""
from __future__ import annotations

import math
import sys

import numpy as np
from astropy.wcs.utils import proj_plane_pixel_area

from app.main import build_default_services
from processing import photometry, quality
from services import spectrum, targets

SUB = 20


def own_background(img, good, rr, r_in, r_out):
    v = img[good & (rr >= r_in) & (rr <= r_out)]
    for _ in range(6):
        med, sd = np.median(v), 1.4826 * np.median(np.abs(v - np.median(v)))
        keep = np.abs(v - med) <= 3 * sd
        if keep.all():
            break
        v = v[keep]
    return float(np.median(v)), int(v.size)


def own_aperture_mjy(cut, ra, dec, radius_arcsec, ring=(24.0, 36.0)):
    """Independent flux in mJy, or None when the aperture or ring is not usable."""
    w = cut.wcs
    x0, y0 = (float(v) for v in w.all_world2pix([[ra, dec]], 0)[0])
    area_deg2 = proj_plane_pixel_area(w)                              # pixel area from the WCS Jacobian, independent of the app's own scale code
    omega = area_deg2 * (math.pi / 180.0) ** 2
    scale = math.sqrt(area_deg2) * 3600.0
    ny, nx = cut.image.shape
    img = cut.image.astype(float)
    good = np.isfinite(img) & ~((np.asarray(cut.flags).astype(np.int64) & quality.UNUSABLE_MASK) != 0)
    yy, xx = np.mgrid[0:ny, 0:nx]
    rr = np.hypot(xx - x0, yy - y0)
    bkg, n = own_background(img, good, rr, ring[0] / scale, ring[1] / scale)
    if n < 40:                                                      # the ring around a 12 arcsec aperture only holds about 60 native pixels
        return None
    r = radius_arcsec / scale
    off = (np.arange(SUB) + 0.5) / SUB - 0.5
    total = 0.0
    wsum = 0.0          # all of the aperture's area
    wgood = 0.0         # the part with usable pixels
    for j in range(max(0, int(y0 - r - 2)), min(ny, int(y0 + r + 3))):
        for i in range(max(0, int(x0 - r - 2)), min(nx, int(x0 + r + 3))):
            frac = np.mean((i + off[None, :] - x0) ** 2 + (j + off[:, None] - y0) ** 2 <= r * r)
            if frac <= 0:
                continue
            wsum += frac
            if good[j, i]:
                wgood += frac
                total += frac * (img[j, i] - bkg)
    full = math.pi * r * r
    if wgood < 0.5 * full:                                          # same idea as the app: too little usable aperture, no measurement
        return None
    return total * (full / wgood) * omega * 1e9                    # masked parts assumed to look like the rest (the app does the same)


def robust(a):
    a = np.asarray(a, float)
    return float(1.4826 * np.median(np.abs(a - np.median(a))))


def main(names):
    svc = build_default_services()
    report = {}
    for name in names:
        t = targets.find(name)
        obj = svc.resolve(q=name)
        oid = obj["objectId"]
        rec = svc.registry.get_spectrum(spectrum.spectrum_id(oid, None, None, spectrum.DEFAULT_RADIUS_ARCSEC))
        print(f"\n===== {name} =====")
        # ---- A. flux against an independent aperture sum
        if rec and rec["result"]["availability"]["status"] == "available":
            pts = [p for p in rec["result"]["points"] if p.get("available")]
            diffs, rows, errs, diffs_now = [], [], [], []
            for p in pts:
                r = svc.registry.get_observation(p["observationId"])
                year = 2000.0 + ((spectrum._mjd(r["observation"]) or 60800.0) - 51544.5) / 365.25
                pra, pdec = spectrum.position_for(obj, year, None, None)
                try:
                    cut = svc._cutout_for_measurement(r, pra, pdec, 36.0, quick=True)
                    f = own_aperture_mjy(cut, pra, pdec, 12.0)
                except Exception as exc:
                    errs.append(f"{type(exc).__name__}: {str(exc)[:80]}")
                    continue
                if f is None or p["flux"] == 0:
                    continue
                diffs.append((f - p["flux"]) / p["flux"])
                rows.append((p["bandIndex"], p["wavelengthUm"], p["flux"], f))
                try:
                    now = photometry.aperture_flux(cut, pra, pdec, radius_arcsec=12.0, annulus_arcsec=(24.0, 36.0)).flux_mjy
                    diffs_now.append((f - now) / now)
                except Exception:
                    pass
            if errs:
                print("   (frames that could not be re-measured:", len(errs), "e.g.", errs[0], ")")
            d = np.array(diffs) if diffs else np.array([np.nan])
            print(f"A. independent flux vs the app, {len(d)} of {len(pts)} bands re-measured: median difference {np.median(d) * 100:+.3f} %, "
                  f"largest {np.max(np.abs(d)) * 100:.3f} %, 95th percentile {np.percentile(np.abs(d), 95) * 100:.3f} %")
            if diffs_now:
                dn = np.array(diffs_now)
                print(f"A2. independent flux vs the app's CURRENT photometry code on the same frames ({len(dn)} bands): median {np.median(dn) * 100:+.3f} %, "
                      f"largest {np.max(np.abs(dn)) * 100:.3f} %, 95th percentile {np.percentile(np.abs(dn), 95) * 100:.3f} %")
            worst = sorted(rows, key=lambda r: -abs(r[3] - r[2]) / abs(r[2]))[:3]
            for b, wl, fa, fo in worst:
                print(f"     largest: band {b} at {wl:.3f} um: app {fa:.5g} mJy, independent {fo:.5g} mJy")
            # ---- C. continuity: deviation of a band from the average of its two neighbours (a steep but smooth spectrum gives small deviations,
            # a calibration or aperture problem at a detector boundary would give a large one)
            byb = {p["bandIndex"]: p for p in pts}
            inside, boundary = [], []
            for b, p in byb.items():
                lo_, hi_ = byb.get(b - 1), byb.get(b + 1)
                if not (lo_ and hi_):
                    continue
                dev = abs(p["flux"] - 0.5 * (lo_["flux"] + hi_["flux"])) / p["flux"]
                (boundary if (b % 17 in (0, 1)) else inside).append((b, dev))
            if inside:
                print(f"C. deviation of a band from the mean of its two neighbours: inside a detector median {np.median([d for _, d in inside]) * 100:.1f} %, "
                      f"90th percentile {np.percentile([d for _, d in inside], 90) * 100:.1f} % ({len(inside)} bands)"
                      + (f"; next to a detector boundary: {', '.join(f'band {b}: {d * 100:.1f} %' for b, d in boundary)}" if boundary else "; no band next to a detector boundary has both neighbours measured"))
        else:
            print("A/C. no stored spectrum to check (saturated target or not stored)")
        # ---- B. repeatability of frames at matched wavelength, and D. aperture
        items, off = [], 0
        tot = svc.list_observations(oid, bandpass="SPHEREx-D3", limit=1)["total"]
        while off < tot:
            items += svc.list_observations(oid, bandpass="SPHEREx-D3", limit=200, offset=off)["items"]
            off += 200
        recs = svc.registry.get_observations([i["observationId"] for i in items])
        frames = []
        archive = spectrum._Archive()
        for i in items:
            r = recs.get(i["observationId"])
            if not r or not r["observation"].get("wavelengthRangeUm"):
                continue
            if not spectrum._is_cached(svc, obj, r):
                continue
            fr = spectrum._measure(svc, r, 3, obj, None, None, 12.0, 1e18, archive)
            if fr.flux_mjy is not None and fr.wavelength_um and fr.bandwidth_um:
                frames.append(fr)
        pairs = []
        for a in range(len(frames)):
            for b in range(a + 1, len(frames)):
                fa, fb = frames[a], frames[b]
                if abs(fa.wavelength_um - fb.wavelength_um) <= 0.25 * fa.bandwidth_um:
                    pairs.append((fa, fb))
        if len(pairs) >= 5:
            z0 = [(q.flux_mjy - p.flux_mjy) / math.hypot(p.sigma_mjy, q.sigma_mjy) for p, q in pairs]
            frac = [(q.flux_mjy - p.flux_mjy) / (0.5 * (q.flux_mjy + p.flux_mjy)) for p, q in pairs]
            s_frac = robust(frac)
            # the floor f that makes the pair differences consistent: robust sigma of delta / sqrt(s1^2 + s2^2 + f^2 (F1^2 + F2^2)) = 1
            lo, hi = 0.0, 0.3
            for _ in range(40):
                f = 0.5 * (lo + hi)
                z = [(q.flux_mjy - p.flux_mjy) / math.sqrt(p.sigma_mjy ** 2 + q.sigma_mjy ** 2 + f * f * (p.flux_mjy ** 2 + q.flux_mjy ** 2)) for p, q in pairs]
                lo, hi = (f, hi) if robust(z) > 1.0 else (lo, f)
            z3 = [(q.flux_mjy - p.flux_mjy) / math.sqrt(p.sigma_mjy ** 2 + q.sigma_mjy ** 2 + 0.03 ** 2 * (p.flux_mjy ** 2 + q.flux_mjy ** 2)) for p, q in pairs]
            print(f"B. {len(pairs)} matched-wavelength pairs from {len(frames)} frames: robust scatter of the fractional difference {s_frac * 100:.2f} %; "
                  f"differences / statistical error: robust sigma {robust(z0):.2f} (1.00 would mean the formal errors are right); "
                  f"floor that makes them consistent {0.5 * (lo + hi) * 100:.2f} % per frame; with the app's 3 % floor: robust sigma {robust(z3):.2f}, "
                  f"{sum(abs(z) > 3 for z in z3)} of {len(z3)} pairs beyond 3 sigma (expect about {0.0027 * len(z3):.1f})")
            report[name] = dict(pairs=len(pairs), scatter=s_frac, floor=0.5 * (lo + hi), z_with_floor=robust(z3))
        else:
            print(f"B. only {len(pairs)} matched-wavelength pairs among {len(frames)} measurable stored frames: not enough to test repeatability")
        # D. aperture growth on a few bright, well-measured frames
        good = sorted([f for f in frames if f.snr and abs(f.snr) > 50], key=lambda f: -abs(f.snr))[:6]
        if good and t is not None:
            ratios = []
            for f in good:
                r = f.obs
                year = 2000.0 + ((spectrum._mjd(r["observation"]) or 60800.0) - 51544.5) / 365.25
                pra, pdec = spectrum.position_for(obj, year, None, None)
                try:
                    cut = svc._cutout_for_measurement(r, pra, pdec, 60.0, quick=True)
                    f12, f24 = own_aperture_mjy(cut, pra, pdec, 12.0, (40.0, 56.0)), own_aperture_mjy(cut, pra, pdec, 24.0, (40.0, 56.0))
                except Exception:
                    continue
                if f12 and f24 and f24 > 0:
                    ratios.append((f.wavelength_um, f12 / f24))
            if ratios:
                print("D. share of the flux inside 12 arcsec relative to 24 arcsec (the app applies no aperture correction): "
                      + ", ".join(f"{w:.2f} um: {r * 100:.0f} %" for w, r in sorted(ratios)))
    return report


if __name__ == "__main__":
    main(sys.argv[1:] or ["Epsilon Indi B", "WISE 0855-0714", "RW Aurigae"])
