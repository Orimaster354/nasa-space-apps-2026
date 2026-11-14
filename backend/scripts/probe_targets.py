"""Coverage probe for the five challenge targets (checklist P0-06, P0-11). Throwaway.

Resolves each name with astropy (Sesame) and counts SPHEREx files, epochs and same-band epochs via IRSA SIA2.
Run from backend/:  PYTHONPATH=. python -u scripts/probe_targets.py
"""
from collections import defaultdict

from astropy.coordinates import SkyCoord

from processing import irsa

TARGETS = {
    "Barnard's Star": "Barnard's star",
    "Epsilon Indi B": "eps Indi B",
    "WISE 0855": None,  # coordinates given in the challenge text
    "RW Aurigae": "RW Aur",
    "R Draconis": "R Dra",
}
WISE0855 = (133.795129, -7.245147)

summary = []
for label, name in TARGETS.items():
    try:
        if name is None:
            ra, dec, src = *WISE0855, "challenge text"
        else:
            c = SkyCoord.from_name(name)
            ra, dec, src = c.ra.deg, c.dec.deg, "Sesame name resolver"
    except Exception as e:
        print(f"\n{label}: name resolution FAILED: {repr(e)[:120]}", flush=True)
        continue
    try:
        files = irsa.discover(ra, dec)
    except irsa.IrsaError as e:
        print(f"\n{label}: archive query FAILED: {e}", flush=True)
        continue
    days = defaultdict(set)
    for f in files:
        days[f.bandpass].add(int(f.mjd_start))
    all_days = {int(f.mjd_start) for f in files}
    print(f"\n{label}: ra={ra:.5f} dec={dec:.5f} ({src}) files={len(files)}", flush=True)
    if files:
        print(f"  MJD {min(f.mjd_start for f in files):.1f} .. {max(f.mjd_start for f in files):.1f}  "
              f"distinct days={len(all_days)}  releases={sorted({f.release for f in files})}", flush=True)
        print("  same-band days:", {k: len(v) for k, v in sorted(days.items())}, flush=True)
    summary.append((label, ra, dec, len(files), len(all_days), max((len(v) for v in days.values()), default=0)))

print("\nSUMMARY (target, files, distinct days, max same-band days)")
for r in summary:
    print(f"  {r[0]:16} files={r[3]:4} days={r[4]:3} sameBandMax={r[5]:3}  (ra={r[1]:.4f}, dec={r[2]:.4f})")
