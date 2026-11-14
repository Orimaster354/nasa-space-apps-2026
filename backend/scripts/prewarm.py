"""Pre-warm the cache for the demo targets so they load instantly (cold frames take 3-8 s each; each cutout is about 4 MB).

For each target: resolve it, list its observations in one wavelength band, then fetch and render the first N images at the default
field of view (the frontend shows the first 50). Safe to re-run: cached frames are skipped.

Run from backend/ (uses SPX_DATA_DIR like the server):
    PYTHONPATH=. python -u scripts/prewarm.py [--band SPHEREx-D3] [--max-frames 50] [--fov 10] [--workers 4] [--only "RW Aur"]
"""
import argparse
import time
from concurrent.futures import ThreadPoolExecutor

from app.main import build_default_services
from services.core import NotFound, Upstream

TARGETS = ["Barnard's star", "eps Indi B", "133.795129 -7.245147", "RW Aur", "R Dra"]   # WISE 0855 by coordinates (challenge text)

ap = argparse.ArgumentParser()
ap.add_argument("--band", default="SPHEREx-D3")
ap.add_argument("--max-frames", type=int, default=50)
ap.add_argument("--fov", type=float, default=10.0)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--only", default=None, help="only this target string")
args = ap.parse_args()

svc = build_default_services()
t_all = time.time()
for q in ([args.only] if args.only else TARGETS):
    t0 = time.time()
    try:
        obj = svc.resolve(q=q)
        page = svc.list_observations(obj["objectId"], bandpass=args.band, limit=200)
    except (NotFound, Upstream) as e:
        print(f"{q}: SKIPPED ({e})", flush=True)
        continue
    frames = page["items"][: args.max_frames]
    print(f"{obj['name']}: {page['total']} {args.band} observations; warming {len(frames)} frames at {args.fov} arcmin ...", flush=True)

    def one(o):
        r = svc.get_image(o["observationId"], fov_arcmin=args.fov)
        return r["availability"]["status"]

    with ThreadPoolExecutor(args.workers) as pool:
        statuses = list(pool.map(one, frames))
    bad = [s for s in statuses if s != "available"]
    print(f"  done in {time.time() - t0:.0f}s: {len(statuses) - len(bad)} ok, {len(bad)} not available {set(bad) or ''}", flush=True)
print(f"all done in {time.time() - t_all:.0f}s", flush=True)
