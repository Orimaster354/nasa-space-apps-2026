"""Make a ship bundle correct and self-contained before it is shipped. Run from backend/ with the bundle as the data directory:

    SPX_DATA_DIR=<ship dir> PYTHONPATH=. python scripts/ship_finalize.py <ship dir>

For EVERY target in the bundle (curated and added ones):
  1. rebuild the spectrum from the stored frames with the current photometry code (the aperture sampling was corrected on 2026-11-15; spectra built
     earlier carry a bias of up to +1.2 percent). Needs the network for small cutouts that were never downloaded (the cutout service, not the search);
     the saved observation lists are used instead of the search;
  2. store refusals (a saturated star cannot be measured; that answer is kept) and clear 'partial' flags (partial spectra expire after 10 minutes and would
     trigger a slow rebuild);
  3. remove poor-quality frames a snapshot may have caught between a re-fetch and the clean-up step;
  4. recompute the motion candidates (or record why there are none);
  5. slim the cutouts to what the app reads.
"""
import json
import sqlite3
import sys
import time

from app.main import build_default_services
from services import bundle
from services import spectrum as sp
from services.core import now_iso

svc = build_default_services()
ITEMS = svc.library(fresh=True)["items"]
db = sqlite3.connect(sys.argv[1] + "/registry.sqlite")
db.execute("DELETE FROM spectra WHERE id LIKE 'spec_%'")
db.commit()
db.close()

for it in ITEMS:                                                                            # 1 and 2: spectra
    t0 = time.time()
    r = svc.get_spectrum(it["objectId"], wait_s=900)
    sid = sp.spectrum_id(it["objectId"], None, None, sp.DEFAULT_RADIUS_ARCSEC)
    if svc.registry.get_spectrum(sid) is None and r["availability"]["status"] == "unavailable":
        svc.registry.put_spectrum(sid, {"result": r, "partial": False, "savedAt": now_iso()})
    print(f"{it['name']}: spectrum {r['availability']['status']}, {len([p for p in r.get('points', []) if p.get('available')])} bands, {time.time() - t0:.0f}s", flush=True)

dropped = 0                                                                                 # 3: poor frames
for it in ITEMS:
    tot = svc.list_observations(it["objectId"], limit=1)["total"]
    ids, off = [], 0
    while off < tot:
        ids += [i["observationId"] for i in svc.list_observations(it["objectId"], limit=200, offset=off)["items"]]
        off += 200
    dropped += len(bundle.prune_poor(svc, ids))
print("poor-quality frames removed:", dropped, flush=True)

svc.candidate_cached_only = True                                                            # 4: candidates (stored frames only)
for it in ITEMS:
    run = svc.compute_candidates(it["objectId"])
    print(f"{it['name']}: candidates {len(run['candidateIds'])} ({(run.get('note') or '')[:110]})", flush=True)

db = sqlite3.connect(sys.argv[1] + "/registry.sqlite")
n = 0
for k, body in db.execute("SELECT id, body FROM spectra").fetchall():
    j = json.loads(body)
    if j.get("partial"):
        j["partial"] = False
        db.execute("UPDATE spectra SET body=? WHERE id=?", (json.dumps(j), k))
        n += 1
db.commit()
db.close()
print("partial flags cleared:", n)

r = bundle.slim_directory(sys.argv[1] + "/cutouts")                                         # 5: slim
print(f"slimmed {r['files']} cutouts: {r['bytesBefore'] / 1e6:.0f} MB -> {r['bytesAfter'] / 1e6:.0f} MB")
