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
ap.add_argument("--extra", default=None, help="comma separated extra object names, or a path to a text file with one name per line")
ap.add_argument("--keep-poor", action="store_true", help="keep frames whose own quality is 'poor' (default: drop them)")
ap.add_argument("--no-spectra", action="store_true", help="do not build and store spectra")
args = ap.parse_args()

svc = build_default_services()
t0 = time.time()
deadline = t0 + args.wait_minutes * 60
def _extra_names() -> list[str]:
    if not args.extra:
        return []
    from pathlib import Path
    raw = Path(args.extra).read_text(encoding="utf-8").splitlines() if Path(args.extra).is_file() else args.extra.split(",")
    return [n.strip() for n in raw if n.strip() and not n.strip().startswith("#")]


NAMES = [t.name for t in targets.TARGETS] + [n for n in _extra_names() if n.lower() not in {t.name.lower() for t in targets.TARGETS}]
todo = [n for n in NAMES if not args.only or args.only.lower() in n.lower()]
report: dict[str, dict] = {}
failed: dict[str, str] = {}
sweep = 0
while todo:
    sweep += 1
    print(f"\n=== sweep {sweep}: {len(todo)} target(s) to do ===", flush=True)
    again = []
    for t in todo:
        try:
            r = bundle.warm_target(svc, t, band=args.band, frames=args.frames, workers=args.workers, skip_poor=not args.keep_poor)
            if not args.no_spectra and not r["missing"] and "spectrum" not in r:
                r["spectrum"] = bundle.warm_spectrum(svc, r["objectId"])
        except (Upstream, NotFound) as exc:
            failed[t] = str(exc)
            print(f"{t}: archive not ready ({exc})", flush=True)
            again.append(t)
            continue
        # A target is only done when its observation list is SAVED as well (an incomplete archive answer is never saved): without it the target
        # would not open while the archive is down.
        c0 = svc.registry.get_object(r["objectId"])["coordinates"]
        if svc.registry.get_discovery(f"{round(c0['raDeg'], 4):.4f},{round(c0['decDeg'], 4):.4f}") is None:
            r["missing"] = r["missing"] + ["observation list"]
            print(f"{t}: frames stored but the archive's observation list was incomplete, so it is not saved yet; will retry", flush=True)
        report[t] = r
        failed.pop(t, None)
        if r["missing"]:
            again.append(t)
    todo = again
    if todo and time.time() < deadline:
        print(f"waiting 60 s before retrying {todo} ({(deadline - time.time()) / 60:.0f} min left) ...", flush=True)
        time.sleep(60)
    elif todo:
        print("out of time; giving up on the rest", flush=True)
        break

print("\nslimming cutouts ...", flush=True)
slim = bundle.slim_directory(settings.data_dir + "/cutouts")
print(f"slimmed {slim['files']} cutouts: {slim['bytesBefore'] / 1e6:.0f} MB -> {slim['bytesAfter'] / 1e6:.0f} MB")
print("\nSUMMARY")
for n in NAMES:
    if args.only and args.only.lower() not in n.lower():
        continue
    r = report.get(n)
    if r:
        sp = r.get("spectrum")
        print(f"  {n:24} {r['ready']}/{r['frames']} frames ready" + (f", {len(r['poor'])} poor dropped" if r.get("poor") else "")
              + (f"  MISSING {len(r['missing'])}" if r["missing"] else "") + (f"  spectrum {sp['status']} ({sp['rendered']}/{sp['bandImages']} band images)" if sp else ""))
    else:
        print(f"  {n:24} NOT BUILT ({failed.get(n, 'unknown')})")
print(f"done in {time.time() - t0:.0f} s; data directory: {settings.data_dir}")
sys.exit(0 if not todo else 1)
