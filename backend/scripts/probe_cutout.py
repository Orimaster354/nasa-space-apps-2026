"""Throwaway probe (P0-03/04/06 part): epochs per band from _probe_rows.csv, then download one cutout and inspect it."""
import sys
from collections import defaultdict
from astropy.table import Table
from astropy.io import fits
from astropy.wcs import WCS

ra, dec = 133.79513, -7.24515
t = Table.read("data/_probe_rows.csv")

by = defaultdict(set)
for r in t:
    by[r["energy_bandpassname"]].add(int(float(r["time_bounds_lower"])))
print("distinct observation days per bandpass:")
for k in sorted(by):
    print(f"  {k}: {len(by[k])} days, {len([r for r in t if r['energy_bandpassname']==k])} files")
print("all-bands distinct days:", len(set(int(float(x)) for x in t["time_bounds_lower"])))
print("path pieces:", sorted({str(u).split('/')[3] for u in t['uri']}))

row = t[0]
url = f"https://irsa.ipac.caltech.edu/{row['uri']}?center={ra},{dec}d&size=0.05"
print("\nURL:", url)
with fits.open(url, timeout=120) as h:
    h.info()
    for hdu in h:
        print("\n---", hdu.name, hdu.data.shape if hdu.data is not None else None, hdu.data.dtype if hdu.data is not None else "")
        keys = [k for k in hdu.header.keys() if k not in ("COMMENT", "HISTORY", "")]
        print(" ", len(keys), "header cards. first keys:", keys[:45])
    im = h["IMAGE"]
    print("\nIMAGE BUNIT/key info:", {k: im.header.get(k) for k in ("BUNIT", "EXTNAME", "CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "CD1_1", "CD1_2", "CD2_1", "CD2_2", "CDELT1", "CDELT2", "CRPIX1A", "CRPIX2A")})
    w = WCS(im.header)
    print("celestial WCS:", w.has_celestial, "pixel->sky center:", w.pixel_to_world(im.data.shape[1] / 2, im.data.shape[0] / 2))
    pr = h[0].header
    print("\nPRIMARY header (non-comment):")
    for k, v in pr.items():
        if k not in ("COMMENT", "HISTORY", ""):
            print(f"  {k} = {v}")
    fl = h["FLAGS"].data
    import numpy as np
    print("\nFLAGS unique bit values (first 15):", np.unique(fl)[:15], "nonzero frac:", float((fl > 0).mean()))
    print("VARIANCE finite frac:", float(np.isfinite(h['VARIANCE'].data).mean()), "IMAGE finite frac:", float(np.isfinite(im.data).mean()))
    print("WCS-WAVE:", h["WCS-WAVE"].data.dtype.names if h["WCS-WAVE"].data is not None else None)
