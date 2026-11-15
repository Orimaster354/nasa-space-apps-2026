"""Make a ship bundle serve exactly what it holds: refusals are stored, and no spectrum is left 'partial' (partial ones expire after 10 minutes and
would trigger a slow rebuild)."""
import json, sqlite3, sys, time
from app.main import build_default_services
from services.core import now_iso
svc = build_default_services()
from services import targets
for t in targets.TARGETS:
    o = svc.resolve(q=t.name)
    r = svc.get_spectrum(o["objectId"], wait_s=300)
    from services import spectrum as sp
    sid = sp.spectrum_id(o["objectId"], None, None, sp.DEFAULT_RADIUS_ARCSEC)
    rec = svc.registry.get_spectrum(sid)
    if rec is None and r["availability"]["status"] == "unavailable":
        svc.registry.put_spectrum(sid, {"result": r, "partial": False, "savedAt": now_iso()})
        print(t.name, "refusal stored")
db = sqlite3.connect(sys.argv[1] + "/registry.sqlite")
n = 0
for k, body in db.execute("SELECT id, body FROM spectra").fetchall():
    j = json.loads(body)
    if j.get("partial"):
        j["partial"] = False; db.execute("UPDATE spectra SET body=? WHERE id=?", (json.dumps(j), k)); n += 1
db.commit(); db.close()
print("partial flags cleared:", n)
