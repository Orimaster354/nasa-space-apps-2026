"""Proper motion for ANY named object, from SIMBAD through the CDS Sesame service.

Why it matters: the images, the photometry and the motion search must look where the object IS during the SPHEREx survey (2025 to 2026), not
where the catalogue put it for the year 2000. For a nearby fast mover that difference is tens to hundreds of arcseconds, far more than the
aperture. Curated targets carry hand-checked values; for every other name this lookup supplies them.

The response format was read from live Sesame answers on 2026-11-15 (Teegarden's Star, Proxima Centauri): a <Resolver> block with <jradeg>,
<jdedeg> and a <pm> block holding <pmRA> (which already includes cos(dec)) and <pmDE> in milliarcseconds per year, <oname>, <otype> and <refPos>.
SIMBAD positions are given for epoch J2000, the same convention the curated targets use.
"""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx

MIRRORS = ("https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?", "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/SNV?")
TIMEOUT_S = 12.0

_TYPES = {"*": "Star", "**": "Double or multiple star", "PM*": "High proper-motion star", "LM*": "Low-mass star", "BD*": "Brown dwarf", "brownD*": "Brown dwarf",
          "Mi*": "Mira variable star", "V*": "Variable star", "Er*": "Eruptive variable star", "Pl": "Planet", "G": "Galaxy", "GiG": "Galaxy in a group", "AGN": "Active galaxy",
          "SNR": "Supernova remnant", "HII": "HII region", "Neb": "Nebula", "RNe": "Reflection nebula", "PN": "Planetary nebula", "SG*": "Supergiant star",
          "s*r": "Red supergiant star", "C*": "Carbon star", "Y*O": "Young stellar object", "TT*": "T Tauri star", "WD*": "White dwarf", "RG*": "Red giant star",
          "Sy2": "Active galaxy", "SyG": "Active galaxy", "LIN": "Active galaxy", "BiC": "Binary system", "Cl*": "Star cluster", "GlC": "Globular cluster", "OpC": "Open cluster",
          "BY*": "Variable star (BY Draconis type)", "Fl*": "Flare star", "Ro*": "Rotationally variable star", "Pu*": "Pulsating variable star", "EB*": "Eclipsing binary star",
          "SB*": "Spectroscopic binary star", "Em*": "Emission-line star", "Be*": "Be star", "*iC": "Star in a cluster", "*iN": "Star in a nebula", "Ce*": "Cepheid variable star",
          "RR*": "RR Lyrae variable star", "Or*": "Orion variable star", "pr*": "Pre-main-sequence star", "BD?": "Brown dwarf candidate", "LM?": "Low-mass star candidate"}


class PmLookupError(Exception):
    """The lookup could not be completed; the message is user-safe and says why."""


def _tag(block: str, name: str) -> str | None:
    m = re.search(rf"<{name}>\s*([^<]*?)\s*</{name}>", block)
    return m.group(1) if m and m.group(1).strip() else None


def _num(text: str | None) -> float | None:
    try:
        return float(text) if text is not None else None
    except ValueError:
        return None


def parse(xml: str) -> dict | None:
    """The first <Resolver> with a position, preferring one that also has a proper motion. None when no resolver gave a position."""
    best = None
    for block in re.findall(r"<Resolver\b.*?</Resolver>", xml, flags=re.S):
        ra, dec = _num(_tag(block, "jradeg")), _num(_tag(block, "jdedeg"))
        if ra is None or dec is None:
            continue
        pmblock = re.search(r"<pm>.*?</pm>", block, flags=re.S)
        pmra = _num(_tag(pmblock.group(0), "pmRA")) if pmblock else None
        pmde = _num(_tag(pmblock.group(0), "pmDE")) if pmblock else None
        otype = _tag(block, "otype")
        item = {"ra": ra, "dec": dec, "pmra": pmra, "pmdec": pmde, "oname": _tag(block, "oname"), "otype": otype,
                "objectType": _TYPES.get(otype or "", f"Object (SIMBAD type {otype})" if otype else "Astronomical object"),
                "refPos": _tag(block, "refPos"), "source": "SIMBAD via CDS Sesame"}
        if pmra is not None and pmde is not None:
            return item
        best = best or item
    return best


def fetch_sesame(name: str, client: httpx.Client | None = None) -> dict:
    """Look the name up. Raises PmLookupError (with the reason) when no mirror answers or no position comes back; the dict it returns has
    pmra/pmdec set to None when SIMBAD knows the object but lists no proper motion."""
    c = client or httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)
    last = "no Sesame mirror answered"
    for base in MIRRORS:
        try:
            r = c.get(base + quote(name, safe=""))
        except httpx.HTTPError as exc:
            last = f"the CDS name service could not be reached ({type(exc).__name__})"
            continue
        if r.status_code != 200:
            last = f"the CDS name service answered {r.status_code}"
            continue
        got = parse(r.text)
        if got is None:
            last = "SIMBAD does not know this name"
            continue
        return got
    raise PmLookupError(last[0].upper() + last[1:] + ".")
