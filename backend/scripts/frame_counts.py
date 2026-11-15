"""Where do the frame counts come from? For each curated target: observations listed -> by detector -> the default timeline (D3) -> stored pictures."""
import json
import os
import sys
from collections import Counter

from app.main import build_default_services
from services import targets

svc = build_default_services()
rendered = set()
for e in os.scandir(svc.images_dir):
    if e.name.startswith("obs_") and e.name.endswith(".png"):
        rendered.add("_".join(e.name.split("_", 2)[:2]))
out = {}
for t in targets.TARGETS:
    obj = svc.resolve(q=t.name)
    oid = obj["objectId"]
    page = svc.list_observations(oid, limit=200)
    total = page["total"]
    allitems = []
    off = 0
    while off < total:
        p = svc.list_observations(oid, limit=200, offset=off)
        allitems += p["items"]
        off += 200
    recs = svc.registry.get_observations([o["observationId"] for o in allitems])
    by_band = Counter(o.get("bandpass") or "?" for o in allitems)
    by_coll = Counter((recs[o["observationId"]]["file"].get("collection") if o["observationId"] in recs else "?") for o in allitems)
    d3 = [o for o in allitems if o.get("bandpass") == "SPHEREx-D3"]
    stored = [o for o in allitems if o["observationId"] in rendered]
    stored_by_band = Counter(o.get("bandpass") or "?" for o in stored)
    d3_stored = [o for o in d3 if o["observationId"] in rendered]
    d3_missing = [o for o in d3 if o["observationId"] not in rendered]
    poor = [o for o in d3_missing if (recs.get(o["observationId"], {}).get("quality") or {}).get("summary") == "poor"]
    out[t.name] = dict(listed_all_detectors=total, by_detector=dict(sorted(by_band.items())), by_collection=dict(by_coll), d3_listed=len(d3),
                       d3_stored=len(d3_stored), d3_not_stored=len(d3_missing), d3_not_stored_poor=len(poor), stored_total=len(stored),
                       stored_by_detector=dict(sorted(stored_by_band.items())))
print(json.dumps(out, indent=1))
