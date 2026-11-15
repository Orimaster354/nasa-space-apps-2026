"""Make a ship bundle serve exactly what it holds: refusals are stored, and no spectrum is left 'partial' (partial ones expire after 10 minutes and
would trigger a slow rebuild)."""
import json, sqlite3, sys, time
from app.main import build_default_services
from services.core import now_iso
svc = build_default_services()
from services import targets
# Rebuild EVERY stored spectrum from the stored frames with the current photometry code (the aperture sampling was corrected on 2026-11-15; spectra
# built earlier carry a bias of up to +1.2 percent). Needs the network for any small cutouts that were never downloaded (the cutout service, not the
# search); the saved observation lists are used instead of the search.
import sqlite3 as _sq
_db = _sq.connect(sys.argv[1] + "/registry.sqlite"); _db.execute("DELETE FROM spectra WHERE id LIKE 'spec_%'"); _db.commit(); _db.close()
for item in svc.library(fresh=True)["items"]:
    t0 = time.time(); r = svc.get_spectrum(item["objectId"], wait_s=900)
    print(f"{item['name']}: spectrum {r['availability']['status']}, {len([p for p in r.get('points', []) if p.get('available')])} bands, rebuilt in {time.time() - t0:.0f}s", flush=True)
for t in targets.TARGETS:
    o = svc.resolve(q=t.name)
    r = svc.get_spectrum(o["objectId"], wait_s=300)
    from services import spectrum as sp
    sid = sp.spectrum_id(o["objectId"], None, None, sp.DEFAULT_RADIUS_ARCSEC)
    rec = svc.registry.get_spectrum(sid)
    if rec is None and r["availability"]["status"] == "unavailable":
        svc.registry.put_spectrum(sid, {"result": r, "partial": False, "savedAt": now_iso()})
        print(t.name, "refusal stored")
# Remove poor-quality frames that a snapshot may have caught between a re-fetch and the clean-up step
from services import bundle
dropped = 0
for t in targets.TARGETS:
    o = svc.resolve(q=t.name)
    tot = svc.list_observations(o["objectId"], limit=1)["total"]
    ids, off = [], 0
    while off < tot:
        ids += [i["observationId"] for i in svc.list_observations(o["objectId"], limit=200, offset=off)["items"]]
        off += 200
    dropped += len(bundle.prune_poor(svc, ids))
print("poor-quality frames removed:", dropped)
# Recompute the stored motion candidates from the stored frames (the candidate was first built from fewer frames and an older photometry version)
svc.candidate_cached_only = True
for t in targets.TARGETS:
    o = svc.resolve(q=t.name)
    run = svc.compute_candidates(o["objectId"])
    print(f"{t.name}: candidates recomputed: {len(run['candidateIds'])} ({(run.get('note') or '')[:80]})", flush=True)
db = sqlite3.connect(sys.argv[1] + "/registry.sqlite")
n = 0
for k, body in db.execute("SELECT id, body FROM spectra").fetchall():
    j = json.loads(body)
    if j.get("partial"):
        j["partial"] = False; db.execute("UPDATE spectra SET body=? WHERE id=?", (json.dumps(j), k)); n += 1
db.commit(); db.close()
print("partial flags cleared:", n)
from services import bundle as _bundle
_r = _bundle.slim_directory(sys.argv[1] + "/cutouts")
print(f"slimmed {_r['files']} cutouts: {_r['bytesBefore'] / 1e6:.0f} MB -> {_r['bytesAfter'] / 1e6:.0f} MB")
