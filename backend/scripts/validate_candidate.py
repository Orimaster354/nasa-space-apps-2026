"""Stress-test the Epsilon Indi B motion candidate (priority 2). Run from backend/:

    PYTHONPATH=. SPX_DATA_DIR=<data dir with the stored frames> python scripts/validate_candidate.py ["Epsilon Indi B"]

Uses only stored frames. Questions it answers:
  1. What is the motion, and how does it compare with the catalogue (the headline number)?
  2. Is the formal uncertainty honest? Frame bootstrap and leave-one-out give an empirical one.
  3. Is it stable in time? The first and second halves of the observations fitted separately.
  4. NEGATIVE CONTROLS: the same tracking applied to ordinary field stars, which should show no motion. Their scatter calibrates the
     real uncertainty and the false-motion rate of the method.
  5. Could the wavelength at the object (which changes frame to frame) fake a drift?
"""
from __future__ import annotations

import math
import sys

import numpy as np

from app.main import build_default_services
from processing import alignment, astrometry, imaging
from services import candidates, targets


def main(name="Epsilon Indi B"):
    svc = build_default_services()
    svc.candidate_cached_only = True                                   # stored frames only
    obj = svc.resolve(q=name)
    target = targets.find(name)
    rec_obj = svc.registry.get_object(obj["objectId"])
    c = rec_obj.get("viewCenter") or rec_obj["coordinates"]
    grid = imaging.GridSpec(c["raDeg"], c["decDeg"], imaging.DEFAULT_FOV_ARCMIN)
    scale = grid.scale_arcsec
    frames, info = candidates._frames_for(svc, obj, target, grid)
    wcs = grid.wcs()

    def expected(year):
        ra, dec = targets.position_at(target, year)
        x, y = wcs.wcs_world2pix([[ra, dec]], 0)[0]
        return float(x), float(y)

    rows, fit = candidates.measure_motion(frames, expected, scale)
    print(f"===== {name}: {len(frames)} stored frames loaded, {len(rows)} tracked =====")
    print(f"1. motion: west {fit.west_rate:+.2f} +- {fit.west_err:.2f}, north {fit.north_rate:+.2f} +- {fit.north_err:.2f} arcsec/yr; total {fit.total:.2f} +- {fit.total_err:.2f} "
          f"({fit.significance:.1f} sigma) over {fit.span_years:.2f} yr; rms about the line {fit.rms_arcsec:.2f} arcsec; {fit.mean_stars:.1f} reference stars per frame")
    cat_w, cat_n = -target.pmra_mas_yr / 1000.0, target.pmdec_mas_yr / 1000.0          # +west, +north
    if cat_w is not None:
        cat_tot = math.hypot(cat_w, cat_n)
        print(f"   catalogue: west {cat_w:+.2f}, north {cat_n:+.2f}, total {cat_tot:.2f}; differences {(fit.west_rate - cat_w) / fit.west_err:+.1f} and {(fit.north_rate - cat_n) / fit.north_err:+.1f} formal sigma; "
              f"total {fit.total - cat_tot:+.2f} arcsec/yr ({(fit.total - cat_tot) / fit.total_err:+.1f} sigma)")

    # 2. bootstrap and leave-one-out over frames
    rng = np.random.default_rng(1)
    tot, west, north = [], [], []
    for _ in range(500):
        pick = sorted(rng.integers(0, len(rows), len(rows)))
        f = astrometry.fit_motion([rows[i] for i in pick])
        if f:
            tot.append(f.total); west.append(f.west_rate); north.append(f.north_rate)
    print(f"2. frame bootstrap (500 resamples): total {np.mean(tot):.2f} +- {np.std(tot):.2f} (formal {fit.total_err:.2f}); west {np.mean(west):+.2f} +- {np.std(west):.2f} (formal {fit.west_err:.2f}); "
          f"north {np.mean(north):+.2f} +- {np.std(north):.2f} (formal {fit.north_err:.2f})")
    loo = [astrometry.fit_motion(rows[:i] + rows[i + 1:]) for i in range(len(rows))]
    lt = [f.total for f in loo if f]
    print(f"   leave-one-out: total ranges {min(lt):.2f} to {max(lt):.2f} (no single frame changes it by more than {max(abs(x - fit.total) for x in lt):.2f})")

    # 3. split in time (independent halves)
    half = len(rows) // 2
    a, b = astrometry.fit_motion(rows[:half]), astrometry.fit_motion(rows[half:])
    if a and b:
        print(f"3. first half ({a.n} frames, {a.span_years:.2f} yr): west {a.west_rate:+.2f} +- {a.west_err:.2f}, north {a.north_rate:+.2f} +- {a.north_err:.2f}; "
              f"second half ({b.n}, {b.span_years:.2f} yr): west {b.west_rate:+.2f} +- {b.west_err:.2f}, north {b.north_rate:+.2f} +- {b.north_err:.2f}")
    # odd / even frames (same time span each)
    o, e = astrometry.fit_motion(rows[0::2]), astrometry.fit_motion(rows[1::2])
    if o and e:
        print(f"   odd frames: west {o.west_rate:+.2f} +- {o.west_err:.2f}, north {o.north_rate:+.2f} +- {o.north_err:.2f}; even frames: west {e.west_rate:+.2f} +- {e.west_err:.2f}, north {e.north_rate:+.2f} +- {e.north_err:.2f}")

    # 4. negative controls: ordinary field stars through the same tracker
    first = frames[0].arr
    src = alignment.find_sources(first, scale)
    ex, ey = expected(frames[0].year)
    keep = [s for s in src if math.hypot(s[0] - ex, s[1] - ey) * scale > 25.0]
    keep.sort(key=lambda s: -s[2] if len(s) > 2 else 0)
    controls = keep[:14]
    zs, rates = [], []
    for s in controls:
        sx, sy = float(s[0]), float(s[1])
        crow = astrometry.track([(f.key, f.year, f.arr) for f in frames], lambda y, sx=sx, sy=sy: (sx, sy), scale)
        cf = astrometry.fit_motion(crow)
        if cf is None or cf.n < 8:
            continue
        zs.append(cf.total / cf.total_err if cf.total_err > 0 else 0.0)
        rates.append((cf.west_rate, cf.north_rate, cf.total, cf.total_err, cf.n))
    if rates:
        tots = np.array([r[2] for r in rates])
        w = np.array([r[0] for r in rates]); n = np.array([r[1] for r in rates]); errs = np.array([r[3] for r in rates])
        print(f"4. negative controls: {len(rates)} ordinary field stars tracked the same way (they should not move):")
        print(f"   apparent motion of each: median {np.median(tots):.2f} arcsec/yr, largest {tots.max():.2f}; formal error typically {np.median(errs):.2f}; "
              f"west rates {np.mean(w):+.2f} +- {np.std(w):.2f}, north rates {np.mean(n):+.2f} +- {np.std(n):.2f} (scatter across stars = the real per-star uncertainty)")
        print(f"   controls with total/error above 3 sigma: {sum(z > 3 for z in zs)} of {len(zs)}; above 5 sigma: {sum(z > 5 for z in zs)}; "
              f"the target is at {fit.significance:.1f} sigma, and its total {fit.total:.2f} is {fit.total / max(1e-9, np.max(tots)):.1f}x the largest control")

    # 5. wavelength versus time at the object
    wl = np.array([f.wl for f in frames if f.wl is not None]); ts = np.array([f.year for f in frames if f.wl is not None])
    if len(wl) > 5:
        r = float(np.corrcoef(ts, wl)[0, 1])
        print(f"5. wavelength at the object: {wl.min():.3f} to {wl.max():.3f} um; correlation with time {r:+.2f}")
        med = np.median(wl)
        sel = [fr for fr in frames if fr.wl is not None and abs(fr.wl - med) < 0.08]
        rows2 = astrometry.track([(f.key, f.year, f.arr) for f in sel], expected, scale)
        f2 = astrometry.fit_motion(rows2)
        if f2:
            print(f"   frames within 0.08 um of {med:.2f} um only ({len(rows2)} tracked): west {f2.west_rate:+.2f} +- {f2.west_err:.2f}, north {f2.north_rate:+.2f} +- {f2.north_err:.2f}, total {f2.total:.2f} +- {f2.total_err:.2f}")


if __name__ == "__main__":
    main(*(sys.argv[1:2] or ["Epsilon Indi B"]))
