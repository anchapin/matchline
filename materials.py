"""Material -> thermal conductivity lookup, shared by both frontends.

Roadmap item 7: Pset_MaterialThermal is usually absent in real IFC files, and
the drawing path has no conductivities at all. This table fills a layer's
conductivity from its material NAME when the file does not state one.

Source: ASHRAE Handbook of Fundamentals 2005, ch. 30 Table 19 and ch. 25, as
published in EnergyPlus ``datasets/ASHRAE_2005_HOF_Materials.idf``
(https://github.com/NREL/EnergyPlus, BSD-3-Clause). Each entry names the
dataset material its value is taken from.

Matching is deliberately conservative. A name is lowercased and reduced to
words; an entry matches when ALL of its keyword phrases appear. A match whose
keywords are a strict subset of another match's is dropped (so "lightweight
concrete" beats "concrete"). If the remaining matches disagree on
conductivity, or the name mentions an air layer, there is no value: an
unknown layer is never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple

SOURCE = "ASHRAE HOF 2005 via EnergyPlus datasets/ASHRAE_2005_HOF_Materials.idf"


@dataclass(frozen=True)
class MaterialEntry:
    id: str
    keywords: Tuple[str, ...]  # every phrase must appear in the name
    conductivity_w_mk: float
    source_material: str  # the dataset Material it comes from


# fmt: off
TABLE: Tuple[MaterialEntry, ...] = (
    MaterialEntry("gypsum_board", ("gypsum",), 0.16, "G01 16mm gypsum board"),
    MaterialEntry("plasterboard", ("plasterboard",), 0.16, "G01 16mm gypsum board"),
    MaterialEntry("drywall", ("drywall",), 0.16, "G01 16mm gypsum board"),
    MaterialEntry("plywood", ("plywood",), 0.12, "G02 16mm plywood"),
    MaterialEntry("osb", ("osb",), 0.091, "Waferboard"),
    MaterialEntry("oriented_strand", ("oriented strand",), 0.091, "Waferboard"),
    MaterialEntry("fiberboard", ("fiberboard",), 0.07, "G03 13mm fiberboard sheathing"),
    MaterialEntry("wood", ("wood",), 0.15, "G04 13mm wood"),
    MaterialEntry("timber", ("timber",), 0.15, "G04 13mm wood"),
    MaterialEntry("brick", ("brick",), 0.89, "M01 100mm brick"),
    MaterialEntry("concrete", ("concrete",), 1.95, "M14 150mm heavyweight concrete"),
    MaterialEntry("lw_concrete", ("lightweight", "concrete"), 0.53, "M12 150mm lightweight concrete"),
    MaterialEntry("concrete_block", ("concrete", "block"), 1.11, "M05 200mm concrete block"),
    MaterialEntry("cmu", ("cmu",), 1.11, "M05 200mm concrete block"),
    MaterialEntry("lw_concrete_block", ("lightweight", "concrete", "block"), 0.50,
                  "M03 200mm lightweight concrete block"),
    MaterialEntry("stucco", ("stucco",), 0.72, "F07 25mm stucco"),
    MaterialEntry("render", ("render",), 0.72, "F07 25mm stucco"),
    MaterialEntry("stone", ("stone",), 3.17, "F10 25mm stone"),
    MaterialEntry("metal", ("metal",), 45.28, "F08 Metal surface"),
    MaterialEntry("steel", ("steel",), 45.28, "F08 Metal surface"),
    MaterialEntry("mineral_wool", ("mineral wool",), 0.05, "I04 89mm batt insulation"),
    MaterialEntry("rock_wool", ("rock wool",), 0.05, "I04 89mm batt insulation"),
    MaterialEntry("glass_wool", ("glass wool",), 0.05, "I04 89mm batt insulation"),
    MaterialEntry("fiberglass", ("fiberglass",), 0.05, "I04 89mm batt insulation"),
    MaterialEntry("batt", ("batt",), 0.05, "I04 89mm batt insulation"),
    MaterialEntry("insulation_board", ("insulation", "board"), 0.03, "I01 25mm insulation board"),
    MaterialEntry("eps", ("eps",), 0.036,
                  "Insulation: Expanded polystyrene - molded beads - 20kg/m3 density"),
    MaterialEntry("xps", ("xps",), 0.029,
                  "Insulation: Expanded polystyrene - extruded (smooth skin surface) (HCFC-142b exp.)"),
    MaterialEntry("extruded_polystyrene", ("extruded polystyrene",), 0.029,
                  "Insulation: Expanded polystyrene - extruded (smooth skin surface) (HCFC-142b exp.)"),
    MaterialEntry("polyiso", ("polyiso",), 0.0245,
                  "Insulation: Cellular polyurethane/polyisocyanuratei (CFC11 exp.) (unfaced)"),
    MaterialEntry("polyisocyanurate", ("polyisocyanurate",), 0.0245,
                  "Insulation: Cellular polyurethane/polyisocyanuratei (CFC11 exp.) (unfaced)"),
    MaterialEntry("pir", ("pir",), 0.0245,
                  "Insulation: Cellular polyurethane/polyisocyanuratei (CFC11 exp.) (unfaced)"),
    MaterialEntry("polyurethane", ("polyurethane",), 0.0245,
                  "Insulation: Cellular polyurethane/polyisocyanuratei (CFC11 exp.) (unfaced)"),
    MaterialEntry("cellular_glass", ("cellular glass",), 0.05, "Insulation: Cellular glass - 50mm"),
)
# fmt: on

_AIR = re.compile(r"\b(air|cavity|void|gap)\b")


def _norm(name: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", (name or "").lower())) + " "


def lookup_conductivity(name: str) -> Optional[MaterialEntry]:
    """The table entry for a material name, or None (unknown, ambiguous, or air)."""
    n = _norm(name)
    if not n.strip() or _AIR.search(n):
        return None
    hits = [e for e in TABLE if all(f" {k} " in n for k in e.keywords)]
    keep = [e for e in hits if not any(set(e.keywords) < set(o.keywords) for o in hits)]
    if not keep or len({e.conductivity_w_mk for e in keep}) != 1:
        return None
    return keep[0]


# --- unventilated air layers ------------------------------------------------

AIR_SOURCE = "ISO 6946:2007 Table 2, horizontal heat flow"

# (thickness mm, R m2K/W) for unventilated air layers with high-emissivity
# faces, horizontal heat flow (walls). Linear interpolation between rows, as
# the standard's note allows. ISO 6946 5.3.1: no single U for air layers
# thicker than 0.3 m, so those get no value.
AIR_LAYER_R_HORIZONTAL: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.00),
    (5.0, 0.11),
    (7.0, 0.13),
    (10.0, 0.15),
    (15.0, 0.17),
    (25.0, 0.18),
    (50.0, 0.18),
    (100.0, 0.18),
    (300.0, 0.18),
)


AIR_SOURCE_UPWARD = "ISO 6946:2007 Table 2, upward heat flow"

# Same table, upward heat flow (roofs, heating case).
AIR_LAYER_R_UPWARD: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.00),
    (5.0, 0.11),
    (7.0, 0.13),
    (10.0, 0.15),
    (15.0, 0.16),
    (25.0, 0.16),
    (50.0, 0.16),
    (100.0, 0.16),
    (300.0, 0.16),
)


def is_air_name(name: str) -> bool:
    """Does a material or layer name describe an air layer (air/cavity/void/gap)?"""
    return bool(_AIR.search(_norm(name)))


def air_layer_resistance(thickness_m: float, direction: str = "horizontal") -> Optional[float]:
    """R of an unventilated air layer (m2K/W), or None outside 0 < t <= 0.3 m.

    ``direction`` is the heat flow: "horizontal" (walls) or "upward" (roofs).
    """
    try:
        t = float(thickness_m) * 1000.0
    except (TypeError, ValueError):
        return None
    if direction == "upward":
        rows = AIR_LAYER_R_UPWARD
    elif direction == "horizontal":
        rows = AIR_LAYER_R_HORIZONTAL
    else:
        raise ValueError(f"unknown heat flow direction {direction!r}")
    if not (t > 0 and t <= rows[-1][0]):
        return None
    for (t0, r0), (t1, r1) in zip(rows, rows[1:]):
        if t <= t1:
            return r0 + (r1 - r0) * (t - t0) / (t1 - t0)
    return None  # unreachable
