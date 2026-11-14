"""Throwaway latency probe: SIA2 vs TAP discovery, and cutout download, for one position. Prints timings as it goes."""
import sys
import time

import httpx

ra, dec = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (133.795129, -7.245147)
print(f"position {ra} {dec}", flush=True)


def timed(label, fn):
    t = time.time()
    try:
        out = fn()
        print(f"{label}: {time.time() - t:.1f}s -> {out}", flush=True)
        return out
    except Exception as e:
        print(f"{label}: FAILED after {time.time() - t:.1f}s: {repr(e)[:200]}", flush=True)


def sia(collection):
    url = f"https://irsa.ipac.caltech.edu/SIA?COLLECTION={collection}&POS=circle+{ra}+{dec}+0.01&RESPONSEFORMAT=CSV"
    r = httpx.get(url, timeout=120)
    lines = r.text.strip().splitlines()
    return f"HTTP {r.status_code}, {max(0, len(lines) - 1)} rows, header: {lines[0][:160] if lines else ''}"


for coll in ("spherex_dr1", "spherex_qr3"):
    timed(f"SIA2 {coll}", lambda c=coll: sia(c))


def tap():
    from astroquery.ipac.irsa import Irsa
    q = ("SELECT a.uri FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
         f"WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly)")
    return f"{len(Irsa.query_tap(q).to_table())} rows"


timed("TAP sync join", tap)
