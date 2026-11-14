"""Decode SPHEREx Level 2 FLAGS bits into contract QualityFlag records.

Bit names and terse meanings come from IRSA's "Notes on SPHEREx Photometry Flags" table in the SPHEREx Data Explorer
spectrophotometry help (https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/sp.html), read 2026-11-14 through a
summarizing fetch. Cross-check against the SPHEREx Explanatory Supplement before relying on any bit.

The *severity* of each flag is OUR classification (a contract field, origin "derived" for the severity, "source" for the
flag itself). It is not NASA's. Bits not in the table are reported as `info` with their raw bit number, never dropped.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FlagDef:
    bit: int
    name: str
    meaning: str          # IRSA's terse definition ("" when none was given)
    severity: str         # "info" | "warning" | "critical"  (our classification)


# Bits IRSA's own spectrophotometry tool ignores when fitting, plus DICHROIC ("unusable pixel"): treated as unusable.
_FLAGS = [
    FlagDef(0, "TRANSIENT", "e.g., CR hit", "warning"),
    FlagDef(1, "OVERFLOW", "nearing saturation", "warning"),
    FlagDef(2, "SUR_ERROR", "possible errors", "critical"),
    FlagDef(6, "NONFUNC", "dead pixel", "critical"),
    FlagDef(7, "DICHROIC", "unusable pixel", "critical"),
    FlagDef(9, "MISSING_DATA", "no data", "critical"),
    FlagDef(10, "HOT", "noisy pixel", "critical"),
    FlagDef(11, "COLD", "unresponsive pixel", "critical"),
    FlagDef(12, "FULLSAMPLE", "full history available", "info"),
    FlagDef(14, "PHANMISS", "can't be corrected", "warning"),
    FlagDef(15, "NONLINEAR", "can't be corrected", "critical"),
    FlagDef(17, "PERSIST", "persistent charge", "critical"),
    FlagDef(19, "OUTLIER", "flagged outlier", "warning"),
    FlagDef(20, "CROSSTALK", "", "warning"),
    FlagDef(21, "SOURCE", "known source", "info"),
    FlagDef(22, "GHOST", "", "warning"),
    FlagDef(25, "STREAK", "", "warning"),
    FlagDef(26, "BLOOM", "", "critical"),
    FlagDef(27, "SNOWBALL", "", "critical"),
    FlagDef(28, "HALO", "", "critical"),
    FlagDef(29, "SATELLITE_HALO", "", "critical"),
]
FLAGS_BY_BIT = {f.bit: f for f in _FLAGS}

# Pixels with any of these should be excluded from photometry.
UNUSABLE_MASK = sum(1 << f.bit for f in _FLAGS if f.severity == "critical")


def decode_value(value: int) -> list[FlagDef]:
    """Flag definitions set in one FLAGS pixel value. Unknown bits become synthetic `info` entries."""
    out: list[FlagDef] = []
    v = int(value)
    bit = 0
    while v:
        if v & 1:
            out.append(FLAGS_BY_BIT.get(bit, FlagDef(bit, f"BIT{bit}", "", "info")))
        v >>= 1
        bit += 1
    return out


def unusable(flags: np.ndarray) -> np.ndarray:
    """Boolean mask of pixels that should not be used for photometry."""
    return (np.asarray(flags).astype(np.int64) & UNUSABLE_MASK) != 0


def summarize(flags: np.ndarray, region: np.ndarray | None = None) -> list[dict]:
    """Contract-shaped QualityFlag dicts for the flags present in `flags` (optionally within a boolean `region`).

    Each entry says how many pixels carry the flag, so a client or the AI can tell a single pixel from a whole frame.
    """
    arr = np.asarray(flags).astype(np.int64)
    if region is not None:
        arr = arr[region]
    total = arr.size
    result = []
    for bit in sorted({d.bit for v in np.unique(arr) for d in decode_value(int(v))}):
        count = int(((arr >> bit) & 1).sum())
        d = FLAGS_BY_BIT.get(bit, FlagDef(bit, f"BIT{bit}", "", "info"))
        meaning = d.meaning or "no definition in IRSA's table"
        result.append({
            "code": d.name,
            "severity": d.severity,
            "description": f"{meaning}: {count} of {total} pixels (bit {bit}).",
            "origin": "source",
        })
    return result


def quality_summary(flags: np.ndarray, region: np.ndarray | None = None) -> str:
    """Rule-based roll-up for the contract's `qualitySummary`: share of unusable pixels.

    Thresholds are our own heuristics: <5% unusable -> good, <25% -> degraded, otherwise poor. Empty input -> unknown.
    """
    arr = np.asarray(flags)
    if region is not None:
        arr = arr[region]
    if arr.size == 0:
        return "unknown"
    frac = float(unusable(arr).mean())
    return "good" if frac < 0.05 else "degraded" if frac < 0.25 else "poor"
