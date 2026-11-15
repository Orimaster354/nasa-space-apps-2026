"""Friendly summary used when the LLM is unavailable, over its limit, or fails the numeric check. No model is involved: it restates the
backend's own results in plain language (each fact carries a `plain` sentence), so a visitor still gets something useful.
It is always labelled, so nobody mistakes it for an AI-written answer."""
from __future__ import annotations

from .facts import EvidencePack

LABEL = "Navi's AI helper isn't available for this question, so here is a plain-language summary built straight from the measurements:"
GENERAL = ("Navi's AI helper isn't available right now. Meanwhile, here is how to get started: search for an object, pick an observation "
           "from the timeline, and try the Blink button to flip between dates. Anything that moves or flickers is worth a closer look.")


def summarize(pack: EvidencePack) -> str:
    shown = [f for f in pack.facts if (f.target_type or f.in_summary) and f.plain]
    if not shown:
        return GENERAL + "\n\nA good next step: " + (next_step(pack) or "")
    lines = [LABEL, ""]
    lines += [f"• {f.plain}" for f in shown]
    step = next_step(pack)
    if step:
        lines += ["", f"A good next step: {step}"]
    return "\n".join(lines)


def next_step(pack: EvidencePack) -> str | None:
    if not pack.has_data:
        return "open an object and select an observation, a comparison or a measurement, then ask again."
    texts = " ".join(f.text for f in pack.facts)
    if pack.comparison_id and "wavelength = warn" in texts:
        return "pick two observations of the same band that land at a similar wavelength, then run the comparison again."
    if pack.comparison_id and not pack.measurement_ids:
        return "measure the brightness of the source in both observations to see whether it changed by more than its uncertainty."
    if pack.measurement_ids:
        return "read the notes under the measurement before drawing any conclusion from the number."
    if len(pack.observation_ids) >= 2:
        return "compare two of the selected observations to look for a change."
    return "select a second observation of the same band to compare against."
