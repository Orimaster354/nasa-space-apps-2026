"""Deterministic summary used when the LLM is unavailable, over quota, or fails the numeric check. No model is involved: it restates
the backend's own facts and warnings. Always labelled as automated."""
from __future__ import annotations

from .facts import EvidencePack

LABEL = "Automated summary (not written by the AI, built directly from the measurements):"


def summarize(pack: EvidencePack) -> str:
    lines = [LABEL]
    shown = [f for f in pack.facts if f.target_type] or [f for f in pack.facts if f.id == "F0"]
    for f in shown:
        lines.append(f"- {f.text}")
    step = next_step(pack)
    if step:
        lines.append(f"Next step: {step}")
    return "\n".join(lines)


def next_step(pack: EvidencePack) -> str | None:
    if not pack.has_data:
        return "Open an object and select an observation, a comparison or a measurement, then ask again."
    texts = " ".join(f.text for f in pack.facts)
    if pack.comparison_id and "wavelength = warn" in texts:
        return "Pick two observations of the same band that land at a similar wavelength, then run the comparison again."
    if pack.comparison_id and not pack.measurement_ids:
        return "Measure the brightness of the source in both observations to see whether it changed more than its uncertainty."
    if pack.measurement_ids:
        return "Check the warnings next to the measurement before drawing any conclusion from the value."
    if len(pack.observation_ids) >= 2:
        return "Compare two of the selected observations to look for a change."
    return "Select a second observation of the same band to compare against."
