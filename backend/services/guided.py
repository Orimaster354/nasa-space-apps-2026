"""The "Spot the shift" guided investigation, built from a REAL candidate (item P1-09).

The UI's guided card already knows how to run a definition of this shape (steps, hints, an answer candidate, a click tolerance). In mock
mode that definition is a fixture written around invented geometry. Here it is generated from the real candidate's measured track and
motion, so every hint is true of the data the user is looking at. The wording follows the frontend's own steps; only the facts are ours.

Honesty rules: the shift is described as small when it is small (Epsilon Indi B moves a pixel or two on screen over six months); the
result is called a candidate, never a discovery; if no target has a candidate the endpoint says so instead of inventing one.
"""
from __future__ import annotations

import math
import re

from . import targets

TOLERANCE_PX = 14                  # click tolerance on the 300-pixel raster (about 28 arcsec): near the star, not "anywhere in the middle"
_RATES = re.compile(r"West ([+-][\d.]+) \+- [\d.]+, north ([+-][\d.]+) \+- [\d.]+ arcsec per year")


def _screen_direction(west: float, north: float) -> str:
    """On the image east is left and north is up."""
    horizontal = "to the right" if west > 0 else "to the left"
    vertical = "upward" if north > 0 else "downward"
    if abs(west) >= 2 * abs(north):
        return horizontal
    if abs(north) >= 2 * abs(west):
        return vertical
    return f"{horizontal} and {vertical}"


def _position_words(x: float, y: float) -> str:
    dx, dy = x - 150.0, y - 150.0
    if math.hypot(dx, dy) < 25:
        return "the middle of the field"
    return "the " + ("upper" if dy < 0 else "lower") + "-" + ("left" if dx < 0 else "right") + " part of the field"


def _pick(svc, object_id: str | None):
    from .core import NotFound, object_id_for
    ids = [object_id] if object_id else [object_id_for(t.ra, t.dec) for t in targets.TARGETS]
    best = None
    for oid in ids:
        for c in svc.registry.list_candidates(oid):
            if c["kind"] in ("positional_change", "both") and c.get("motion") and len(c["track"]) >= 3:
                if best is None or c["score"] > best[1]["score"]:
                    best = (oid, c)
    if best is None:
        raise NotFound("No candidate with a measured shift is available to build a guided investigation from.")
    return best


def spot_the_shift(svc, object_id: str | None = None) -> dict:
    oid, cand = _pick(svc, object_id)
    obj = svc.registry.get_object(oid)
    name = obj["name"]
    track = cand["track"]
    first, last = track[0]["pixel"], track[-1]["pixel"]
    rates = None
    if cand.get("evidenceRefs"):
        m = svc.registry.get_measurement(cand["evidenceRefs"][0]["targetId"])
        found = _RATES.search((m or {}).get("method", ""))
        rates = (float(found.group(1)), float(found.group(2))) if found else None
    west, north = rates if rates else (first["x"] - last["x"], last["y"] - first["y"])
    direction = _screen_direction(west, north)

    dates = []
    for t in (track[0], track[-1]):
        rec = svc.registry.get_observation(t["observationId"])
        dates.append((rec or {}).get("observation", {}).get("observedAt"))
    from .core import iso_to_mjd
    span_years = (iso_to_mjd(dates[1]) - iso_to_mjd(dates[0])) / 365.25 if all(dates) else None
    shift = cand["motion"]["value"] * span_years if span_years and span_years > 0 else None
    small = f" about {shift:.0f} arcseconds between the earliest and latest date, only a pixel or two on screen, so zoom in" if shift is not None else ", only slightly, so zoom in"

    return {
        "guidedId": "spot-the-shift", "title": "Spot the shift", "objectId": oid, "answerCandidateId": cand["candidateId"],
        "tolerancePx": TOLERANCE_PX,
        "intro": f"Some objects change position between observations. {name} is a real SPHEREx target that moves across the sky, but slowly. Can you find it?",
        "steps": [
            {"id": "browse", "title": "Step through the dates",
             "prompt": "Use the timeline or press play. Watch the stars between dates.",
             "done": "Nice. Most stars stay put, which is what makes movers stand out."},
            {"id": "find", "title": "Click what moved",
             "prompt": "Click the object you think changed position. (Candidate markers are hidden for this step.)",
             "hints": ["Most stars sit in exactly the same place on every date. Look for one that is slightly displaced.",
                       f"It is in {_position_words((first['x'] + last['x']) / 2, (first['y'] + last['y']) / 2)}, and it is one of the brighter dots.",
                       f"Over the dates it drifts {direction} by{small}."],
             "correct": f"Yes, that is the one. Candidate {cand['label']} shifts between dates, towards the {_compass(west, north)}.",
             "incorrect": "Not quite. That source stays put. Try another, or ask for a hint."},
            {"id": "confirm", "title": "Check it with a blink comparison",
             "prompt": "Switch the viewer to Blink, pick the earliest and a late date, and zoom in. Does the source shift?",
             "done": "Blinking is a classic way to spot movement."},
        ],
        "explanation": (f"Sources that appear to move are candidates, not established findings. {name}'s shift was measured from real SPHEREx "
                        f"observations as {cand['motion']['value']:.1f} +- {cand['motion']['uncertainty']:.1f} arcseconds per year relative to the stars around it, "
                        "and compared with the catalogue's value. Checking it properly needs alignment checks, uncertainty estimates and follow-up outside this app."),
    }


def _compass(west: float, north: float) -> str:
    from .candidates import _compass as c
    return c(west, north)
