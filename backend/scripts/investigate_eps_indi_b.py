"""P0-38: one real investigation, end to end, on Epsilon Indi B, using only this app's own backend code and the demo bundle.

Run from backend/ (works offline except for the final AI question):
    PYTHONPATH=. SPX_DATA_DIR=<a COPY of demo_bundle> python -u scripts/investigate_eps_indi_b.py [--no-ai]

Steps: resolve the target, find two epochs at matched wavelength, measure its brightness with uncertainty in each, measure the change
(and show the safeguard refusing a mismatched pair), compare the two frames, check its motion against the catalogue proper motion,
then ask the AI companion a question grounded in those results. Prints what is and is not established.
"""
import argparse
import math
import warnings

import numpy as np

warnings.filterwarnings("ignore")

from app.main import build_default_companion, build_default_services           # noqa: E402
from processing import alignment, imaging, irsa                                 # noqa: E402
from processing.fits_io import read_cutout                                      # noqa: E402
from services import targets                                                    # noqa: E402
from services.core import DataUnavailable, Upstream, iso_to_mjd                           # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--no-ai", action="store_true")
args = ap.parse_args()

svc = build_default_services()
obj = svc.resolve(q="Epsilon Indi B")
oid = obj["objectId"]
target = targets.find("Epsilon Indi B")
view = svc.registry.get_object(oid)["viewCenter"]
pm = obj["properMotion"]
print(f"1. TARGET  {obj['name']} ({obj['objectType']}), catalogue proper motion {pm['value']:.2f} {pm['unit']} "
      f"(pmRA {pm['pmRaMasPerYr']:+.1f}, pmDec {pm['pmDecMasPerYr']:+.1f} mas/yr; {pm['source']})")

items = svc.list_observations(oid, bandpass="SPHEREx-D3", limit=200)["items"][:50]
have = [i for i in items if i.get("wavelengthAtObjectUm") is not None]
print(f"2. DATA    {len(items)} D3 observations, {items[0]['observedAt'][:10]} to {items[-1]['observedAt'][:10]}; wavelength at the target known for {len(have)}")

region = {"center": {"raDeg": view["raDeg"], "decDeg": view["decDeg"]}, "radiusArcsec": 12.0}
measured = []                                                       # frames where a brightness can be measured from the bundle alone
refused = 0
for it in have:
    try:
        svc.create_measurement("aperture_flux", [it["observationId"]], region)
        measured.append(it)
    except (DataUnavailable, Upstream):
        refused += 1
have = measured
print(f"   brightness measurable in {len(have)} of those frames ({refused} refused: aperture masked or not covered by the stored cutout)")

# ---- matched and mismatched pairs ------------------------------------------------------------------------------------------------
pairs = sorted(((abs(b["wavelengthAtObjectUm"] - a["wavelengthAtObjectUm"]), iso_to_mjd(b["observedAt"]) - iso_to_mjd(a["observedAt"]), a, b)
                for i, a in enumerate(have) for b in have[i + 1:] if iso_to_mjd(b["observedAt"]) - iso_to_mjd(a["observedAt"]) >= 100), key=lambda t: t[0])
dl, days, A, B = pairs[0]
print(f"   matched pair: {A['observedAt'][:10]} at {A['wavelengthAtObjectUm']:.3f} um  ->  {B['observedAt'][:10]} at {B['wavelengthAtObjectUm']:.3f} um "
      f"({days:.0f} days apart, wavelengths differ by {dl:.3f} um; bandwidth {A['bandwidthAtObjectUm']:.3f} um)")

mA = svc.create_measurement("aperture_flux", [A["observationId"]], region)
mB = svc.create_measurement("aperture_flux", [B["observationId"]], region)
print(f"3. BRIGHTNESS  {A['observedAt'][:10]}: {mA['value']:.2f} +- {mA['uncertainty']:.2f} mJy ({mA['signalToNoise']:.0f}x its uncertainty); "
      f"{B['observedAt'][:10]}: {mB['value']:.2f} +- {mB['uncertainty']:.2f} mJy ({mB['signalToNoise']:.0f}x)")
chg = svc.create_measurement("flux_change", [A["observationId"], B["observationId"]], region)
print(f"4. CHANGE  later minus earlier = {chg['value']:+.2f} +- {chg['uncertainty']:.2f} mJy, {abs(chg['signalToNoise']):.1f} times its uncertainty")
for w in chg["warnings"]:
    print("   -", w[:150])

mismatched = sorted(((abs(b["wavelengthAtObjectUm"] - a["wavelengthAtObjectUm"]), a, b) for i, a in enumerate(have) for b in have[i + 1:]),
                    key=lambda t: -t[0])
shown = False
for gap, a, b in mismatched[:40]:                                  # widest wavelength gaps first; skip pairs that fail for an unrelated reason
    try:
        svc.create_measurement("flux_change", [a["observationId"], b["observationId"]], region)
        print(f"   SAFEGUARD FAILED: a pair {gap:.2f} um apart was accepted")
        shown = True
        break
    except DataUnavailable as exc:
        if "same wavelength" in str(exc):
            print(f"   safeguard: {a['observedAt'][:10]} ({a['wavelengthAtObjectUm']:.3f} um) vs {b['observedAt'][:10]} ({b['wavelengthAtObjectUm']:.3f} um), "
                  f"{gap:.2f} um apart, was REFUSED as a flux change: {str(exc)[:230]}")
            shown = True
            break
if not shown:
    print("   (no mismatched pair could be demonstrated)")

cmp_ = svc.create_comparison(A["observationId"], B["observationId"], "difference")
al = cmp_.get("alignment", {})
print(f"5. COMPARE verdict {cmp_['compatibility']['verdict']}; alignment residual {al.get('residualArcsec', float('nan')):.2f} arcsec; "
      + ", ".join(f"{c['name']}={c['result']}" for c in cmp_["compatibility"]["checks"]))

# ---- motion check: the target's position relative to the field stars, over all frames, against the catalogue ---------------------------
grid = imaging.GridSpec(view["raDeg"], view["decDeg"], 10.0)
scale = grid.scale_arcsec
ref = None
rows = []
for it in items:
    rec = svc.registry.get_observation(it["observationId"])
    cut = read_cutout(svc.cutouts.get(irsa.cutout_url(rec["file"]["uri"], grid.center_ra, grid.center_dec, grid.cutout_size_deg)))
    arr = imaging.to_grid(cut, grid)
    src = alignment.find_sources(arr, scale)
    if len(src) < 4:
        continue
    t_year = 2000.0 + (cut.mjd_obs - 51544.5) / 365.25
    ra_e, dec_e = targets.position_at(target, t_year)
    ex, ey = grid.wcs().wcs_world2pix([[ra_e, dec_e]], 0)[0]
    d_t = np.hypot(src[:, 0] - ex, src[:, 1] - ey) * scale
    k = int(np.argmin(d_t))
    if d_t[k] > 8.0:
        continue
    tgt = src[k]
    others = np.delete(src, k, axis=0)
    if ref is None:
        ref = {"others": others, "tgt": tgt, "year": t_year}
    d = np.hypot(ref["others"][:, None, 0] - others[None, :, 0], ref["others"][:, None, 1] - others[None, :, 1]) * scale
    pairs_ = [(np.argmin(d[i]), i) for i in range(len(ref["others"])) if d[i].min() <= 10.0]
    if len(pairs_) < 3:
        continue
    off = np.array([others[j, :2] - ref["others"][i, :2] for j, i in pairs_]) * scale          # this frame minus the reference, per star
    g = np.median(off, axis=0)
    p = (tgt[:2] - ref["tgt"][:2]) * scale - g                                                   # target displacement relative to the field stars
    rows.append((t_year - ref["year"], p[0], p[1], len(pairs_), 1.4826 * np.median(np.abs(off - g), axis=0).mean()))
rows = np.array(rows)
print(f"6. MOTION  target tracked relative to field stars in {len(rows)} of {len(items)} frames (>=3 matched stars each)")
if len(rows) >= 8:
    t = rows[:, 0]
    out = {}
    for name, col, pred in (("west", 1, -target.pmra_mas_yr / 1000.0), ("north", 2, target.pmdec_mas_yr / 1000.0)):
        y = rows[:, col]
        A_ = np.vstack([t, np.ones_like(t)]).T
        coef, res, *_ = np.linalg.lstsq(A_, y, rcond=None)
        resid = y - A_ @ coef
        sigma = resid.std(ddof=2)
        cov = sigma ** 2 * np.linalg.inv(A_.T @ A_)
        slope, err = coef[0], math.sqrt(cov[0, 0])
        out[name] = (slope, err, pred)
        print(f"   {name:5}: measured {slope:+.2f} +- {err:.2f} arcsec/yr; catalogue predicts {pred:+.2f}  (difference {abs(slope - pred) / err:.1f} sigma)")
    tot = math.hypot(out["west"][0], out["north"][0])
    print(f"   total measured {tot:.2f} arcsec/yr versus catalogue {pm['value']:.2f} arcsec/yr; time span {t.max() - t.min():.2f} yr; scatter per frame {rows[:, 1].std():.2f} arcsec")
else:
    print("   too few frames for a motion fit")

# ---- the AI companion ------------------------------------------------------------------------------------------------------------------
if not args.no_ai:
    comp = build_default_companion(svc)
    ctx = {"objectId": oid, "observationIds": [A["observationId"], B["observationId"]], "comparisonId": cmp_["comparisonId"],
           "measurementIds": [chg["measurementId"]], "mode": "beginner"}
    print("\n7. ASK NAVI: 'Did this brown dwarf get brighter or fainter between those two dates?'")
    r = comp.respond("Did this brown dwarf get brighter or fainter between those two dates?", ctx, "investigation")
    print(r["message"]["content"])
    print("   references:", r["references"])
    print("   caveats:", [c[:90] for c in r["caveats"]])
