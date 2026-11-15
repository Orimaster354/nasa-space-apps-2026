"""Evidence-backed challenges for the guided experience (P2-04).

Frontend-only operation (not in the contract): GET /objects/{id}/challenges. Each challenge is built from a RESULT the backend already
holds (a candidate, a stored spectrum, a measurement on a wavelength-matched pair of stored frames) and carries that evidence and the answer.
A challenge is offered only when its evidence exists and supports a fair question; an object with no usable evidence gets an empty list and a
note saying why, never an invented puzzle. The answer wording is generated from the numbers, so a "no change" result is presented as such.

Challenge kinds:
  find_the_mover   a candidate whose measured shift passed the validation checks (leads to the Spot the shift activity)
  brightness_check two stored frames at matching wavelength at least 100 days apart: did the object change? (answer from a flux_change measurement)
  spectrum_shape   a stored spectrum with at least 8 measured bands: where is the object brightest and where faintest?
"""
from __future__ import annotations

import logging
import math

from . import spectrum as spectrum_mod
from . import targets

log = logging.getLogger("spx.challenges")

MIN_DAYS_APART = 100.0
MIN_BANDS = 8
SIGMA = 3.0


def _find_pair(svc, obj_id: str):
    from processing import compare
    from .core import iso_to_mjd
    items = svc.list_observations(obj_id, bandpass="SPHEREx-D3", limit=200)["items"]
    recs = svc.registry.get_observations([o["observationId"] for o in items])
    ok = []
    for o in items:
        r = recs.get(o["observationId"])
        w = (r or {}).get("wavelengthAtObject")
        if r and w and w.get("bandwidthUm") and (r.get("quality") or {}).get("summary") != "poor" and r["observation"].get("observedAt"):
            ok.append((o["observationId"], iso_to_mjd(r["observation"]["observedAt"]), w["um"], w["bandwidthUm"]))
    ok.sort(key=lambda t: t[1])
    best = None
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            if b[1] - a[1] >= MIN_DAYS_APART and abs(a[2] - b[2]) <= compare.WAVELENGTH_MATCH_FRACTION * a[3]:
                mismatch, span = abs(a[2] - b[2]) / a[3], b[1] - a[1]
                if best is None or (mismatch, -span) < (best[3], -best[2]):     # the best-matched pair (least colour difference); the longer span breaks ties
                    best = (a[0], b[0], span, mismatch)
    return best[:3] if best else None


def build(svc, object_id: str) -> dict:
    obj = svc.get_object(object_id)
    name = obj["name"]
    items: list[dict] = []
    why: list[str] = []

    # --- find the mover
    best = None
    for c in svc.registry.list_candidates(object_id):
        if c["kind"] in ("positional_change", "both") and c.get("motion") and len(c["track"]) >= 3:
            if best is None or c["score"] > best["score"]:
                best = c
    if best:
        m = best["motion"]
        items.append({
            "challengeId": f"{object_id}:mover", "kind": "find_the_mover", "title": "Spot the shift",
            "prompt": f"Something near {name} moves against the background stars over the dates SPHEREx observed it. Step through the dates and click it.",
            "hints": ["Compare the same patch of sky on the earliest and latest dates.", "Blink two dates: a star that stays put looks still; the mover jumps a little."],
            "evidence": {"candidateId": best["candidateId"], "measurementIds": [r["targetId"] for r in best.get("evidenceRefs", []) if r.get("targetType") == "measurement"]},
            "answer": {"text": (f"The candidate {best['label']} shifts about {m['value']:.1f} {m['unit']} relative to the surrounding stars. "
                                "That is small, only a pixel or two on screen, and it is a candidate worth checking, not a confirmed discovery."),
                       "candidateId": best["candidateId"]},
            "activity": f"/api/v1/guided/spot-the-shift?objectId={object_id}", "illustrative": False})
    else:
        why.append("no candidate with a validated shift")

    # --- brightness check
    pair = None
    try:
        pair = _find_pair(svc, object_id)
    except Exception as exc:                          # a challenge is optional: never let one failure hide the others
        log.info("brightness challenge for %s: %s", name, exc)
    if pair:
        a, b, span = pair
        view = obj.get("viewCenter") or obj["coordinates"]
        t = targets.find(name)
        if t is not None:
            view = {"raDeg": targets.view_center(t)[0], "decDeg": targets.view_center(t)[1]}
        try:
            meas = svc.create_measurement("flux_change", [a, b], {"center": {"raDeg": view["raDeg"], "decDeg": view["decDeg"]}, "radiusArcsec": 12.0})
        except Exception as exc:
            meas = None
            log.info("brightness challenge for %s: measurement failed: %s", name, exc)
        if meas and meas.get("uncertainty") and meas.get("signalToNoise") is not None:
            snr = abs(meas["signalToNoise"])
            verdict = (f"The change is {meas['value']:+.3g} ± {meas['uncertainty']:.2g} {meas['unit']}, {snr:.1f} times its uncertainty, so it is "
                       + ("consistent with no change." if snr < SIGMA else "larger than the uncertainty: a lead to check, not proof the object changed.")
                       + " This is one pair of dates; another matched pair can give a different answer.")
            items.append({
                "challengeId": f"{object_id}:brightness", "kind": "brightness_check", "title": "Did it get brighter or fainter?",
                "prompt": f"Two observations of {name}, {span:.0f} days apart, sample it at the same wavelength. Did its brightness change between them?",
                "hints": ["Same wavelength matters: otherwise a difference could just be the object's colour.", "A change only counts if it is large compared with its uncertainty."],
                "evidence": {"observationIds": [a, b], "measurementIds": [meas["measurementId"]]},
                "answer": {"text": verdict, "significant": snr >= SIGMA, "measurementId": meas["measurementId"]}, "illustrative": False})
        else:
            why.append("the brightness of this object could not be measured (for example it saturates)")
    else:
        why.append("no two stored observations at matching wavelength at least 100 days apart")

    # --- spectrum shape
    rec = svc.registry.get_spectrum(spectrum_mod.spectrum_id(object_id, None, None, spectrum_mod.DEFAULT_RADIUS_ARCSEC))
    if rec and rec["result"]["availability"]["status"] == "available":
        pts = [p for p in rec["result"]["points"] if p.get("available")]
        if len(pts) >= MIN_BANDS:
            top, bottom = max(pts, key=lambda p: p["flux"]), min(pts, key=lambda p: p["flux"])
            items.append({
                "challengeId": f"{object_id}:spectrum", "kind": "spectrum_shape", "title": "Read the spectrum",
                "prompt": f"On the Spectrum page, find where {name} is brightest and where it is faintest.",
                "hints": ["Bands are slices of infrared colour; low numbers are shorter wavelengths.", "Gaps are bands with no measurement, not zero brightness."],
                "evidence": {"spectrumId": rec["result"]["spectrumId"]},
                "answer": {"text": (f"Brightest near {top['wavelengthUm']:.2f} µm and faintest near {bottom['wavelengthUm']:.2f} µm, "
                                    f"across {len(pts)} measured bands."), "brightestUm": top["wavelengthUm"], "faintestUm": bottom["wavelengthUm"]}, "illustrative": False})
        else:
            why.append(f"the stored spectrum has fewer than {MIN_BANDS} measured bands")
    else:
        why.append("no stored spectrum")

    out = {"objectId": object_id, "items": items, "total": len(items)}
    if not items:
        out["note"] = f"No challenge is offered for {name} because there is not enough evidence: " + "; ".join(why) + "."
    return out
