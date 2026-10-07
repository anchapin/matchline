"""Cited construction library for envelope U-values (#747).

Drawings rarely state U-values, so a wall-type tag such as ``W-1 8" CMU`` or a
roof note such as ``rigid insulation above deck`` is resolved to the ASHRAE
90.1-2019 Table 5.5 maximum assembly value for that construction class and
climate zone (``construction_library_data.py``, generated from the same
openstudio-standards tag as the space-use defaults, #685).

Rules, same as the DOE prototype defaults:

* A stated value (drawing, schedule, IFC ThermalTransmittance) is never
  overwritten; only constructions whose ``u_value_w_m2k`` is unset are filled.
* Nothing is guessed. A construction resolves only when its tag or
  description names exactly one construction class; no match, two classes, or
  no climate zone leaves it unset and reported.
* Every filled value carries Provenance method ``construction_default`` whose
  note names the source, edition, table, climate zone, row and the words that
  matched. The value is the code maximum, not the drawn assembly, so the
  confidence stays below the review threshold's comfortable range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from building_model import Provenance
from construction_library_data import (
    CONSTRUCTION_LIBRARY,
    EDITION,
    SOURCE,
    SOURCE_VERSION,
)

METHOD = "construction_default"
CONFIDENCE = 0.6  # a code-maximum baseline, not the assembly on the drawings
CATEGORIES = ("Nonresidential", "Residential", "Semiheated")

# Order matters: framing governs over cladding ("brick veneer on steel studs"
# is a steel-framed wall), so framing classes are tested before Mass. Two
# framing classes in one description is ambiguous and resolves to nothing.
_WALL_FRAMING: List[Tuple[str, re.Pattern]] = [
    (
        "Metal Building",
        re.compile(r"metal\s+building|pre-?engineered|insulated\s+metal\s+panel|\bimp\b"),
    ),
    (
        "SteelFramed",
        re.compile(r"(steel|metal|mtl\.?|light[- ]gau?ge|cold[- ]formed)\s+(stud|framing|framed)"),
    ),
    ("WoodFramed", re.compile(r"wood\s+(stud|framing|framed)|\b2\s*x\s*[468]\s+stud")),
]
_WALL_MASS = re.compile(
    r"\bcmu\b|concrete|masonry|\bbrick\b|\bblock\b|tilt[- ]?up|precast|\bmass\s+wall"
)
_ROOF: List[Tuple[str, re.Pattern]] = [
    (
        "IEAD",
        re.compile(
            r"\biead\b|(insulation|insul\.?)\s+(entirely\s+)?above\s+(the\s+)?deck"
            r"|rigid\s+(roof\s+)?insulation\s+(on|over)\s+(metal|steel|concrete)\s+deck"
        ),
    ),
    ("Metal Building", re.compile(r"metal\s+building|standing\s+seam")),
    ("Attic and Other", re.compile(r"\battic\b|wood\s+(joist|truss)")),
]


def climate_zone_number(cz: str) -> str:
    """'5A' -> '5', '7' -> '7'. Table 5.5 is split by zone number only."""
    m = re.fullmatch(r"\s*(?:cz\s*)?([0-8])\s*([abc])?\s*", (cz or "").lower())
    if not m:
        raise ValueError(f"not an ASHRAE climate zone: {cz!r} (expected 0-8 with A/B/C)")
    return m.group(1)


def classify(text: str, surface: str) -> Tuple[Optional[str], str]:
    """Construction class named by ``text`` for ``surface``, plus the reason.

    Returns ``(None, reason)`` when nothing or more than one class matches.
    """
    t = (text or "").lower()
    if surface == "ExteriorWall":
        hits = [(c, m.group(0)) for c, rx in _WALL_FRAMING if (m := rx.search(t))]
        if len(hits) > 1:
            return None, "ambiguous: " + ", ".join(f"{c} ({w!r})" for c, w in hits)
        if hits:
            return hits[0][0], f"matched {hits[0][1]!r}"
        m = _WALL_MASS.search(t)
        if m:
            return "Mass", f"matched {m.group(0)!r}"
        return None, "no wall construction class named"
    if surface == "ExteriorRoof":
        hits = [(c, m.group(0)) for c, rx in _ROOF if (m := rx.search(t))]
        if len(hits) > 1:
            return None, "ambiguous: " + ", ".join(f"{c} ({w!r})" for c, w in hits)
        if hits:
            return hits[0][0], f"matched {hits[0][1]!r}"
        return None, "no roof construction class named"
    return None, f"surface {surface} not resolved by this library yet"


@dataclass
class LibraryRow:
    category: str
    climate_zone: str
    surface: str
    construction_type: str
    source_construction: str
    u_ip: Optional[float]
    u_si: Optional[float]

    @property
    def table(self) -> str:
        return f"Table 5.5-{self.climate_zone}"


def lookup(
    surface: str, construction_type: str, climate_zone: str, category: str = "Nonresidential"
) -> Optional[LibraryRow]:
    cz = climate_zone_number(climate_zone)
    row = CONSTRUCTION_LIBRARY.get((category, cz, surface, construction_type))
    if row is None:
        return None
    return LibraryRow(
        category,
        cz,
        surface,
        construction_type,
        row["source_construction"],
        row["u_ip"],
        row["u_si"],
    )


@dataclass
class LibrarySummary:
    climate_zone: str = ""
    category: str = ""
    resolved: Dict[str, dict] = field(default_factory=dict)  # construction id -> row info
    kept: List[str] = field(default_factory=list)  # ids with a stated U, untouched
    unmatched: Dict[str, str] = field(default_factory=dict)  # id -> reason
    not_run: str = ""  # why the library did not run at all

    def to_dict(self) -> dict:
        return {
            "source": f"{SOURCE} ({SOURCE_VERSION})",
            "climate_zone": self.climate_zone,
            "building_category": self.category,
            "not_run": self.not_run,
            "resolved": self.resolved,
            "kept_stated_values": sorted(self.kept),
            "unmatched": self.unmatched,
        }


def _prov(row: LibraryRow, cid: str, why: str, given_cz: str) -> Provenance:
    return Provenance(
        sheet_id="",
        revision=0,
        method=METHOD,
        confidence=CONFIDENCE,
        note=(
            f"{SOURCE}, {EDITION} {row.table} ({SOURCE_VERSION}); {row.category} "
            f"{row.surface} {row.construction_type}, climate zone {given_cz}; "
            f"U {row.u_ip} Btu/h-ft2-F; {cid}: {why}; code maximum, not the drawn assembly"
        ),
    )


def apply_construction_library(
    model, climate_zone: str = "", category: str = "Nonresidential"
) -> LibrarySummary:
    """Fill unset construction U-values from Table 5.5; never overwrite.

    With no climate zone the library does not run (a zone is never assumed)
    and every unset construction is reported. Space wall U-values are rolled
    up again afterwards so the exports see the filled values.
    """
    from constructions import apply_wall_u_rollup

    if category not in CATEGORIES:
        raise ValueError(f"building category must be one of {CATEGORIES}, got {category!r}")
    s = LibrarySummary(climate_zone=climate_zone or "", category=category)
    if climate_zone:
        climate_zone_number(climate_zone)  # validate early
        model.climate_zone = climate_zone
        model.building_category = category
    else:
        s.not_run = "no climate zone given"
    roof_id = getattr(model, "roof_construction_id", "") or ""
    slab_id = getattr(model, "slab_construction_id", "") or ""
    for cid, c in sorted((getattr(model, "constructions", None) or {}).items()):
        if c.u_value_w_m2k is not None and c.u_value_w_m2k > 0:
            s.kept.append(cid)
            continue
        if cid == slab_id:
            s.unmatched[cid] = "slab: Table 5.5 gives an F-factor, not resolved yet"
            continue
        if not climate_zone:
            s.unmatched[cid] = "no climate zone given"
            continue
        surface = "ExteriorRoof" if cid == roof_id else "ExteriorWall"
        ctype, why = classify(f"{cid} {c.name}", surface)
        if ctype is None:
            s.unmatched[cid] = why
            continue
        row = lookup(surface, ctype, climate_zone, category)
        if row is None or row.u_si is None:
            s.unmatched[cid] = f"no Table 5.5 U-value for {category} {surface} {ctype}"
            continue
        c.u_value_w_m2k = row.u_si
        c.provenance = _prov(row, cid, why, climate_zone)
        s.resolved[cid] = {
            "surface": surface,
            "construction_type": ctype,
            "table": row.table,
            "u_ip": row.u_ip,
            "u_si": row.u_si,
            "why": why,
        }
    if s.resolved:
        apply_wall_u_rollup(model)
    return s
