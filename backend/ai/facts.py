"""Turn the backend's own stored results into numbered text facts for the LLM (and for the automated fallback).

Only results our deterministic code produced go in: object, observation metadata, comparison verdict and checks, measurements.
No pixel data, no arrays, no images, no FITS. Each fact has an ID the model must cite, a technical sentence containing the numbers
(`text`, for the model), a friendly sentence (`plain`, for the no-AI fallback), the raw numbers it is allowed to quote, and the
warnings that become user-facing caveats.

GUIDE facts are short, number-free explanations of how this tool works (what blinking shows, what black patches are, ...). They let
the companion answer beginner questions even when nothing is selected, without inventing anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from services.core import NotFound, Services

MAX_ITEMS = 6
MAX_CAVEATS = 5

MISSION_FACT = ("SPHEREx observes the sky in 102 infrared spectral bands across six detectors (D1 to D6). Within one band the wavelength "
                "depends on where the object falls on the detector, so the same object lands at different wavelengths on different "
                "pointings and its brightness changes with wavelength even if the object itself does not change.")

GUIDE_FACTS = (
    "Blinking means flipping quickly between two pictures of the same patch of sky taken on different dates. Stars that stay put look "
    "still. An object that moves, or changes brightness, appears to jump or flicker.",
    "Black patches in an image are pixels the instrument flagged as unusable, most often because a very bright star overloaded the "
    "detector. They do not mean empty sky; they mean there is no trustworthy data at that spot.",
    "Proper motion is a star's slow drift across the sky. A few nearby stars, such as Barnard's Star, drift fast enough that over a "
    "couple of decades they move a noticeable distance, which is why the image is centred where the star is expected to be.",
    "Every measurement comes with an uncertainty. If a change is not much bigger than its uncertainty, it can easily be noise instead of "
    "a real change.",
    "A candidate is something worth a closer look. It is not a confirmed discovery, and this tool never calls anything one.",
    "Infrared light has longer wavelengths than visible light, so our eyes cannot see it. It is useful for seeing cool objects and dust.",
)


@dataclass
class Fact:
    id: str
    text: str
    target_type: str | None = None          # AIReference.targetType, or None for background facts
    target_id: str | None = None
    numbers: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    plain: str | None = None                # friendly rendering for the no-AI fallback; None = not shown there


@dataclass
class EvidencePack:
    facts: list[Fact]
    missing: list[str] = field(default_factory=list)
    observation_ids: list[str] = field(default_factory=list)
    comparison_id: str | None = None
    measurement_ids: list[str] = field(default_factory=list)

    @property
    def has_data(self) -> bool:
        return any(f.target_type for f in self.facts)

    def texts(self) -> dict[str, str]:
        return {f.id: f.text for f in self.facts}

    def numbers(self) -> dict[str, list[float]]:
        return {f.id: f.numbers for f in self.facts}

    def caveats(self) -> list[str]:
        out: list[str] = []
        for f in self.facts:
            for w in f.warnings:
                w = w.strip()
                if w and w not in out:
                    out.append(w[:300])
        if self.missing:
            out.append("Some selected items could not be found: " + ", ".join(self.missing[:4]) + ".")
        return out[:MAX_CAVEATS]


def _g(v: float, digits: int = 4) -> str:
    return f"{v:.{digits}g}"


def friendly_date(iso: str | None) -> str:
    if not iso:
        return "at an unknown time"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return f"{d.day} {d.strftime('%b %Y')}"
    except ValueError:
        return iso


_VERDICT_WORDS = {
    "comparable": "These two pictures can be compared fairly.",
    "comparable_with_caveats": "These two pictures can be compared, but with some caution.",
    "not_comparable": "These two pictures should not be compared directly.",
    "unknown": "I could not tell whether these two pictures can be compared.",
}


def build(services: Services, ctx: dict | None) -> EvidencePack:
    ctx = ctx or {}
    facts: list[Fact] = [Fact("F0", MISSION_FACT)]
    facts += [Fact(f"G{i}", t) for i, t in enumerate(GUIDE_FACTS, 1)]
    pack = EvidencePack(facts)
    n = [0]

    def add(text, tt=None, tid=None, numbers=None, warnings=None, plain=None) -> Fact:
        n[0] += 1
        f = Fact(f"F{n[0]}", text, tt, tid, list(numbers or []), list(warnings or []), plain)
        facts.append(f)
        return f

    if ctx.get("objectId"):
        try:
            o = services.get_object(ctx["objectId"])
            c = o["coordinates"]
            kind = f" ({o['objectType']})" if o.get("objectType") else ""
            text = f"Object '{o['name']}'{kind} at RA {c['raDeg']:.4f} deg, Dec {c['decDeg']:.4f} deg (position from {o['resolver']['name']})."
            plain = f"You're looking at {o['name']}{kind}, located at RA {c['raDeg']:.4f}°, Dec {c['decDeg']:.4f}°."
            nums = [c["raDeg"], c["decDeg"]]
            pm = o.get("properMotion")
            if pm:
                text += (f" Catalogue proper motion: {pm['value']:.3g} {pm['unit']} (source: {pm['source']}; the catalogue position is at epoch "
                         f"{pm.get('referenceEpoch', 'unknown')}, so the object has since moved and the image is centred on its expected position).")
                plain += f" It drifts across the sky by about {pm['value']:.3g} {pm['unit'].replace('/yr', ' per year')}."
                nums.append(pm["value"])
            add(text, "object", o["objectId"], nums, plain=plain)
        except NotFound:
            pack.missing.append("the selected object")

    selected = [ctx["selection"]["observationId"]] if ctx.get("selection") and ctx["selection"].get("observationId") else []
    obs_ids = list(dict.fromkeys([*(ctx.get("observationIds") or []), *selected]))
    for oid in obs_ids[:MAX_ITEMS]:
        try:
            o = services.get_observation(oid)
        except NotFound:
            pack.missing.append(oid)
            continue
        pack.observation_ids.append(oid)
        band = o.get("bandpass", "unknown")
        bits = [f"Observation {o.get('irsaObservationId', oid)}: band {band}",
                f"observed {o.get('observedAt', 'at an unknown time')}", f"data release {o.get('dataRelease', 'unknown')}",
                f"image quality {o.get('qualitySummary', 'unknown')}"]
        nums = []
        wr = o.get("wavelengthRangeUm")
        plain = f"A picture taken on {friendly_date(o.get('observedAt'))} in the {band} band"
        if wr:
            bits.append(f"the whole band spans {wr['min']:g} to {wr['max']:g} micrometres")
            nums += [wr["min"], wr["max"]]
            plain += f", which sees infrared light from {wr['min']:.2f} to {wr['max']:.2f} micrometres"
        plain += f". Picture quality: {o.get('qualitySummary', 'unknown')}."
        if o.get("wavelengthAtObjectUm") is not None:
            bits.append(f"the object falls at {o['wavelengthAtObjectUm']:.3f} micrometres within the band")
            nums.append(o["wavelengthAtObjectUm"])
            plain += f" Here the object sits at about {o['wavelengthAtObjectUm']:.2f} micrometres within that band."
        flags = o.get("qualityFlags") or []
        warn = []
        if o.get("qualitySummary") in ("degraded", "poor"):
            warn.append(f"Observation {o.get('irsaObservationId', oid)} has {o['qualitySummary']} image quality (many unusable pixels).")
        txt = "; ".join(bits) + "."
        if flags:
            txt += " Quality flags present: " + ", ".join(f"{f['code']} ({f['severity']})" for f in flags[:6]) + "."
            if any(f["severity"] == "critical" for f in flags):
                plain += " Some pixels were flagged as unusable (they show as black patches)."
        add(txt, "observation", oid, nums, warn, plain)
    if pack.observation_ids:
        add(f"{len(pack.observation_ids)} observation(s) are selected.", None, None, [len(pack.observation_ids)])

    if ctx.get("comparisonId"):
        try:
            c = services.get_comparison(ctx["comparisonId"])
        except NotFound:
            pack.missing.append("the selected comparison")
        else:
            pack.comparison_id = c["comparisonId"]
            comp = c.get("compatibility", {})
            checks = comp.get("checks", [])
            nums = []
            al = c.get("alignment") or {}
            if al.get("residualArcsec") is not None:
                nums.append(al["residualArcsec"])
            txt = (f"Comparison ({c['mode']}) of two observations: verdict {comp.get('verdict', 'unknown')}, kind {c.get('comparisonKind', 'unknown')}. Checks: "
                   + " | ".join(f"{k['name']} = {k['result']}: {k['detail']}" for k in checks))
            warn = list(c.get("warnings", [])) + [f"{k['name']}: {k['detail']}" for k in checks if k["result"] in ("warn", "fail")]
            plain = _VERDICT_WORDS.get(comp.get("verdict", "unknown"), _VERDICT_WORDS["unknown"])
            for k in checks:
                if k["result"] in ("warn", "fail"):
                    plain += f" {k['detail']}"
            add(txt, "comparison", c["comparisonId"], nums, warn, plain)

    for mid in (ctx.get("measurementIds") or [])[:MAX_ITEMS]:
        try:
            m = services.get_measurement(mid)
        except NotFound:
            pack.missing.append(mid)
            continue
        pack.measurement_ids.append(mid)
        unc = m.get("uncertainty")
        txt = (f"Measurement ({m['kind']}): {_g(m['value'])}"
               + (f" with uncertainty {_g(unc, 3)}" if unc is not None else " (no uncertainty available)") + f" {m['unit']}")
        nums = [m["value"]] + ([unc] if unc is not None else [])
        snr = abs(m["signalToNoise"]) if m.get("signalToNoise") is not None else None
        if snr is not None:
            txt += f"; the value is {snr:.1f} times its uncertainty"
            nums.append(snr)
        if m.get("wavelengthUm") is not None:
            txt += f"; measured at {m['wavelengthUm']:.3f} micrometres"
            nums.append(m["wavelengthUm"])
        r = m.get("region")
        if r:
            txt += f"; aperture radius {r['radiusArcsec']:g} arcsec"
            nums.append(r["radiusArcsec"])
        txt += f". Method: {m['method']}"
        notes = [w for w in m.get("warnings", []) if not w.startswith(("Base frame:", "Target frame:"))][:4]
        if notes:
            txt += " Notes from the software: " + " ".join(n[:220] for n in notes)
        what = "the object's brightness" if m["kind"] == "aperture_flux" else "the change in brightness between the two pictures"
        plain = f"I measured {what} as about {_g(m['value'])}" + (f" ± {_g(unc, 3)}" if unc is not None else "") + f" {m['unit']}."
        if snr is not None:
            if snr < 3:
                plain += f" That is only {snr:.1f} times its uncertainty, so it could easily be noise."
            elif m["kind"] == "flux_change":
                plain += f" That is {snr:.1f} times its uncertainty, so it stands out, but it does not prove the object really changed."
            else:
                plain += f" That is {snr:.1f} times its uncertainty, so it is a clear signal."
        add(txt, "measurement", mid, nums, list(m.get("warnings", [])), plain)

    if ctx.get("candidateId"):
        add("Candidate details are not available from the backend yet, so nothing about this candidate can be stated.",
            plain="Candidate details are not available yet, so I can't say anything about that candidate.")
    if ctx.get("spectrumId"):
        add("Spectrum data is not available from the backend yet, so nothing about this spectrum can be stated.",
            plain="Spectrum data is not available yet, so I can't say anything about that spectrum.")
    return pack
