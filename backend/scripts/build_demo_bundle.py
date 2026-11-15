"""Build the demo bundle (see services/bundle.py). Needs network access to IRSA. Run from backend/:

    PYTHONPATH=. SPX_DATA_DIR=demo_bundle python -u scripts/build_demo_bundle.py [--band SPHEREx-D3] [--frames 50] [--wait-minutes 90]

Start from an EMPTY directory so the bundle holds only the demo targets. It warms every curated target, then slims the cutouts in place.
If the archive is down or flaky it keeps retrying (cached frames are skipped, so a retry only fetches what is missing) until
--wait-minutes runs out. The exit code is 1 if any target is still incomplete.
The result (about 100 MB) is NOT committed to git; it is copied into the deploy source and the Dockerfile ships it as the data directory.
"""
import argparse
import sys
import time

from app.config import settings
from app.main import build_default_services
from services import bundle, targets
from services.core import NotFound, Upstream

ap = argparse.ArgumentParser()
ap.add_argument("--band", default="SPHEREx-D3")
ap.add_argument("--frames", type=int, default=50)
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--only", default=None, help="only this target name")
ap.add_argument("--wait-minutes", type=float, default=60.0, help="keep retrying for this long when the archive is down")
args = ap.parse_args()

svc = build_default_services()
t0 = time.time()
deadline = t0 + args.wait_minutes * 60
todo = [t for t in targets.TARGETS if not args.only or args.only.lower() in t.name.lower()]
report: dict[str, dict] = {}
failed: dict[str, str] = {}
sweep = 0
while todo:
    sweep += 1
    print(f"\n=== sweep {sweep}: {len(todo)} target(s) to do ===", flush=True)
    again = []
    for t in todo:
        try:
            r = bundle.warm_target(svc, t.name, band=args.band, frames=args.frames, workers=args.workers)
        except (Upstream, NotFound) as exc:
            failed[t.name] = str(exc)
            print(f"{t.name}: archive not ready ({exc})", flush=True)
            again.append(t)
            continue
        report[t.name] = r
        failed.pop(t.name, None)
        if r["missing"]:
            again.append(t)
    todo = again
    if todo and time.time() < deadline:
        print(f"waiting 60 s before retrying {[t.name for t in todo]} ({(deadline - time.time()) / 60:.0f} min left) ...", flush=True)
        time.sleep(60)
    elif todo:
        print("out of time; giving up on the rest", flush=True)
        break

print("\nslimming cutouts ...", flush=True)
slim = bundle.slim_directory(settings.data_dir + "/cutouts")
print(f"slimmed {slim['files']} cutouts: {slim['bytesBefore'] / 1e6:.0f} MB -> {slim['bytesAfter'] / 1e6:.0f} MB")
print("\nSUMMARY")
for t in targets.TARGETS:
    if args.only and args.only.lower() not in t.name.lower():
        continue
    r = report.get(t.name)
    if r:
        print(f"  {t.name:16} {r['ready']}/{r['frames']} frames ready" + (f"  MISSING {len(r['missing'])}" if r["missing"] else ""))
    else:
        print(f"  {t.name:16} NOT BUILT ({failed.get(t.name, 'unknown')})")
print(f"done in {time.time() - t0:.0f} s; data directory: {settings.data_dir}")
sys.exit(0 if not todo else 1)
