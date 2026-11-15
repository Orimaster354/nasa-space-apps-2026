"""The five challenge targets as a curated table, so the demo works by name even when the name resolver is down or does not know the
spelling (CDS Sesame does not know "WISE 0855-0714", for example).

Every number here was retrieved from the CDS Sesame service (which serves SIMBAD data) on 2026-11-15, not typed from memory:
J2000 position and proper motion. SIMBAD's pmRA includes cos(dec). Positions are at epoch J2000.

Why proper motion matters here: three of the five targets move several arcseconds per year, so after 25 years the catalogue
position is minutes of arc away from the object. Verified on real data: Barnard's Star sits 266 arcsec north of its J2000 position
in a 2025-08 SPHEREx frame (a saturated source about 1000x the background at the predicted pixel, flat sky at the catalogue pixel).
For WISE 0855 the challenge text's own coordinate is consistent with this table's position plus proper motion to about 0.4 arcsec
if that coordinate is at epoch 2010.4, which also supports the J2000 epoch assumed here.
So the image field is centred where the object is expected to be at VIEW_EPOCH, while the object's `coordinates` stay the
catalogue (J2000) values users know.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

VIEW_EPOCH = 2026.0            # SPHEREx observes from 2025 into 2026; centre the field at the middle of that span
SOURCE = "CDS Sesame (SIMBAD data), retrieved 2026-11-15"


@dataclass(frozen=True)
class Target:
    name: str
    aliases: tuple[str, ...]
    ra: float                  # degrees, ICRS, epoch J2000
    dec: float
    pmra_mas_yr: float         # includes cos(dec)
    pmdec_mas_yr: float
    catalog_id: str            # the name SIMBAD uses
    object_type: str           # general description, not taken from the resolver
    note: str                  # what has and has not been checked

    @property
    def pm_total_arcsec_yr(self) -> float:
        return math.hypot(self.pmra_mas_yr, self.pmdec_mas_yr) / 1000.0


TARGETS: tuple[Target, ...] = (
    Target("Barnard's Star", ("Barnard's star", "Barnards star", "Barnard star", "GJ 699", "Gl 699", "HIP 87937"),
           269.4520769586187, 4.69336496657667, -801.551, 10362.394, "NAME Barnard's star", "Star (red dwarf)",
           "Position and motion verified against a real SPHEREx frame (saturated star found at the predicted pixel)."),
    Target("WISE 0855-0714", ("WISE 0855", "WISE0855", "WISE 0855-0714", "WISE J085510.83-071442.5", "WISEA J085510.74-071442.5", "WISE 0855 0714"),
           133.81890825, -7.247048, -8123.7, 673.2, "WISEA J085510.74-071442.5", "Brown dwarf",
           "Position plus proper motion agrees with the challenge text's coordinate to about 0.4 arcsec if that coordinate is at epoch 2010.4. Very faint: SPHEREx detection not yet checked."),
    Target("RW Aurigae", ("RW Aur", "RW Aur A", "RW Aurigae A"),
           76.95652603458, 30.40143817389, 7.23, -20.33, "V* RW Aur", "Young variable star",
           "Negligible proper motion. Interesting for brightness changes, not motion."),
    Target("R Draconis", ("R Dra",),
           248.16761083295, 66.75497941722, -20.643, 6.149, "V* R Dra", "Mira variable star",
           "Small proper motion. Interesting for brightness changes, not motion."),
    Target("Epsilon Indi B", ("eps Indi B", "eps Ind B", "Epsilon Ind B", "Epsilon Indi Ba", "eps Indi Ba", "Eps Indi B"),
           331.04414658852124, -56.78284419511611, 3981.977, -2466.832, "* eps Ind B", "Brown dwarf (binary)",
           "Catalogue position assumed J2000; not separately checked in SPHEREx data. The pair also orbits each other."),
)


def normalize(text: str) -> str:
    t = text.lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"'s\b", "s", t)
    return re.sub(r"[^a-z0-9+.]+", " ", t).strip()


_INDEX = {normalize(n): t for t in TARGETS for n in (t.name, *t.aliases)}


def find(query: str | None) -> Target | None:
    return _INDEX.get(normalize(query)) if query else None


def position_at(t: Target, epoch: float) -> tuple[float, float]:
    """Catalogue position carried forward to `epoch` by straight-line proper motion (adequate over 26 years for these targets)."""
    dt = epoch - 2000.0
    dec = t.dec + t.pmdec_mas_yr / 3.6e6 * dt
    cos_dec = math.cos(math.radians((t.dec + dec) / 2.0))
    ra = t.ra + t.pmra_mas_yr / 3.6e6 * dt / cos_dec
    return ra % 360.0, dec


def view_center(t: Target) -> tuple[float, float]:
    return position_at(t, VIEW_EPOCH)


def proper_motion_model(t: Target) -> dict:
    return {"value": round(t.pm_total_arcsec_yr, 4), "unit": "arcsec/yr", "source": SOURCE, "provenanceKind": "catalog",
            "catalogName": "SIMBAD via CDS Sesame", "catalogId": t.catalog_id, "pmRaMasPerYr": t.pmra_mas_yr, "pmDecMasPerYr": t.pmdec_mas_yr,
            "convention": "pmRA includes cos(dec)", "referenceEpoch": "J2000"}
