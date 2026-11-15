"""Compute candidates for every curated target from the frames already stored in the demo bundle and save them in the bundle's registry,
so they are served instantly (and with the archive down). Uses stored frames only: no downloads. Run from backend/:

    PYTHONPATH=. SPX_DATA_DIR=demo_bundle python -u scripts/add_candidates_to_bundle.py
"""
import time

from app.main import build_default_services
from services import targets

svc = build_default_services()
svc.candidate_cached_only = True
for t in targets.TARGETS:
    obj = svc.resolve(q=t.name)
    t0 = time.time()
    run = svc.compute_candidates(obj["objectId"])
    page = svc.get_candidates(obj["objectId"])
    if page["items"]:
        c = page["items"][0]
        print(f"{t.name:16} {c['label']} {c['kind']} score {c['score']} ({time.time() - t0:.0f} s)")
    else:
        print(f"{t.name:16} none: {page['coverageNote']}")
