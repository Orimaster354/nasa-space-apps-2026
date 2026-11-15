"""Turn the backend's own stored results into numbered text facts for the LLM (and for the automated fallback).

Only results our deterministic code produced go in: object, observation metadata, comparison verdict and checks, measurements.
No pixel data, no arrays, no images, no FITS. Each fact has an ID the model must cite, a plain sentence containing the numbers,
the raw numbers it is allowed to quote, and the warnings that become user-facing caveats.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from services.core import NotFound, Services

MAX_ITEMS = 6
MAX_CAVEATS = 8

MISSION_FACT = ("SPHEREx observes the sky in 102 infrared spectral bands across six detectors (D1 to D6). Within one band the wavelength "
                "depends on where the object falls on the detector, so the same object lands at different wavelengths on different "
                "pointings and its brightness changes with wavelength even if the object itself does not change.")


@dataclass
class Fact:
    id: str
    text: str
    target_type: str | None = None          # AIReference.targetType, or None for background facts
    target_id: str | None = None
    numbers: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


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


def build(services: Services, ctx: dict | None) -> EvidencePack:
    ctx = ctx or {}
    facts: list[Fact] = [Fact("F0", MISSION_FACT)]
    pack = EvidencePack(facts)
    n = [0]

    def add(text, tt=None, tid=None, numbers=None, warnings=None) -> Fact:
        n[0] += 1
        f = Fact(f"F{n[0]}", text, tt, tid, list(numbers or []), list(warnings or []))
        facts.append(f)
        return f

    if ctx.get("objectId"):
        try:
            o = services.get_object(ctx["objectId"])
            c = o["coordinates"]
            add(f"Object '{o['name']}' at RA {c['raDeg']:.4f} deg, Dec {c['decDeg']:.4f} deg (position from {o['resolver']['name']}).",
                "object", o["objectId"], [c["raDeg"], c["decDeg"]])
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
        bits = [f"Observation {o.get('irsaObservationId', oid)}: band {o.get('bandpass', 'unknown')}",
                f"observed {o.get('observedAt', 'at an unknown time')}", f"data release {o.get('dataRelease', 'unknown')}",
                f"image quality {o.get('qualitySummary', 'unknown')}"]
        nums = []
        wr = o.get("wavelengthRangeUm")
        if wr:
            bits.append(f"the whole band spans {wr['min']:g} to {wr['max']:g} micrometres")
            nums += [wr["min"], wr["max"]]
        flags = o.get("qualityFlags") or []
        warn = []
        if o.get("qualitySummary") in ("degraded", "poor"):
            warn.append(f"Observation {o.get('irsaObservationId', oid)} has {o['qualitySummary']} image quality (many unusable pixels).")
        txt = "; ".join(bits) + "."
        if flags:
            txt += " Quality flags present: " + ", ".join(f"{f['code']} ({f['severity']})" for f in flags[:6]) + "."
        add(txt, "observation", oid, nums, warn)
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
            add(txt, "comparison", c["comparisonId"], nums, warn)

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
        if m.get("signalToNoise") is not None:
            txt += f"; the value is {abs(m['signalToNoise']):.1f} times its uncertainty"
            nums.append(abs(m["signalToNoise"]))
        if m.get("wavelengthUm") is not None:
            txt += f"; measured at {m['wavelengthUm']:.3f} micrometres"
            nums.append(m["wavelengthUm"])
        r = m.get("region")
        if r:
            txt += f"; aperture radius {r['radiusArcsec']:g} arcsec"
            nums.append(r["radiusArcsec"])
        txt += f". Method: {m['method']}"
        add(txt, "measurement", mid, nums, list(m.get("warnings", [])))

    if ctx.get("candidateId"):
        add("Candidate details are not available from the backend yet, so nothing about this candidate can be stated.")
    if ctx.get("spectrumId"):
        add("Spectrum data is not available from the backend yet, so nothing about this spectrum can be stated.")
    return pack
