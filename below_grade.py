"""Below-grade storeys and ground/outdoor boundary types (#634).

A storey is below grade only when a source says so (``Level.above_ground``,
read from IFC ``Pset_BuildingStoreyCommon.AboveGround``); elevation alone
never decides it. From that and the inter-story surfaces of #632 this step
names a gbXML ``surfaceType`` for every envelope wall and horizontal surface:

- walls: ``UndergroundWall`` on a below-grade level, ``ExteriorWall`` otherwise;
- lowest floors: ``UndergroundSlab`` below grade, ``SlabOnGrade`` otherwise;
- floors between storeys: ``InteriorFloor``;
- upper floors over no lower space: ``RaisedFloor`` (outdoor air) above
  grade, ``UndergroundSlab`` (earth) below grade;
- roofs: ``Roof`` above grade, ``UndergroundCeiling`` when the level above is
  also below grade, and unresolved (None, sent to review) when a below-grade
  space has no below-grade storey over it, since only the drawings say
  whether that deck is earth-covered or open to the sky.

Levels with the property unstated keep today's above-ground types, so models
without it are classified exactly as the single-storey writers already
export them; a level that looks like a basement but is unstated is flagged,
not reclassified. The model is not modified apart from review items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from building_model import Provenance

REVIEW_CONF = 0.5
ELEV_TOL_M = 1e-3


@dataclass
class BoundaryResult:
    walls: Dict[str, Optional[str]] = field(default_factory=dict)  # EnvelopeWall.id -> type
    horizontals: Dict[str, Optional[str]] = field(default_factory=dict)  # surface id -> type
    below_grade_levels: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)


def _prov(note: str) -> Provenance:
    return Provenance(
        sheet_id="model", revision=0, method="below_grade", confidence=REVIEW_CONF, note=note
    )


def boundary_types(model, interstory=None, flag: bool = True) -> BoundaryResult:
    """Classify envelope walls and horizontal surfaces as above/below grade.

    ``interstory`` is a :class:`interstory.InterstoryResult`; when None it is
    computed with ``match_interstory(model, flag=False)``.
    """
    if interstory is None:
        from interstory import match_interstory

        interstory = match_interstory(model, flag=False)
    res = BoundaryResult()

    def note(text):
        res.findings.append(text)
        if flag:
            model.flag_for_review("below_grade", text, REVIEW_CONF, _prov(text))

    levels = sorted(model.levels, key=lambda lv: (lv.elevation_z_m, lv.id))
    below = {lv.id for lv in levels if lv.above_ground is False}
    res.below_grade_levels = [lv.id for lv in levels if lv.id in below]
    index = {lv.id: k for k, lv in enumerate(levels)}

    stated_above = [lv.elevation_z_m for lv in levels if lv.above_ground is True]
    lowest_above = min(stated_above) if stated_above else None
    for lv in levels:
        if lv.above_ground is None and (
            lv.elevation_z_m < -ELEV_TOL_M
            or (lowest_above is not None and lv.elevation_z_m < lowest_above - ELEV_TOL_M)
        ):
            note(
                f"level {lv.id} at {lv.elevation_z_m:.3f} m may be below grade but its source "
                "does not say (AboveGround not stated); kept as above ground"
            )
    for lo, up in zip(levels, levels[1:]):
        if lo.above_ground is True and up.above_ground is False:
            note(f"level {up.id} is stated below grade but sits above level {lo.id}, stated above")

    level_of_space = {sid: sp.level_id for sid, sp in model.spaces.items()}

    def is_below(level_id):
        return level_id in below

    for w in model.envelope:
        lid = level_of_space.get(getattr(w, "space_id", "") or "")
        if lid is None and len(levels) == 1:
            lid = levels[0].id
        if lid is None:
            if below:
                res.walls[w.id] = None
                note(f"envelope wall {w.id} has no space or level; above/below grade unresolved")
            else:
                res.walls[w.id] = "ExteriorWall"
            continue
        res.walls[w.id] = "UndergroundWall" if is_below(lid) else "ExteriorWall"

    for s in interstory.surfaces:
        if s.kind == "interior":
            res.horizontals[s.id] = "InteriorFloor"
        elif s.kind == "ground":
            lid = level_of_space[s.upper_space_id]
            res.horizontals[s.id] = "UndergroundSlab" if is_below(lid) else "SlabOnGrade"
        elif s.kind == "exposed_floor":
            lid = level_of_space[s.upper_space_id]
            res.horizontals[s.id] = "UndergroundSlab" if is_below(lid) else "RaisedFloor"
        elif s.kind == "roof":
            lid = level_of_space[s.lower_space_id]
            if not is_below(lid):
                res.horizontals[s.id] = "Roof"
                continue
            k = index[lid] + 1
            if k < len(levels) and is_below(levels[k].id):
                res.horizontals[s.id] = "UndergroundCeiling"
            else:
                res.horizontals[s.id] = None
                note(
                    f"{s.id}: top of below-grade space {s.lower_space_id} ({s.area_m2:.2f} m2) "
                    "has no storey over it; earth-covered or open deck is not stated"
                )
    return res


__all__ = ["BoundaryResult", "boundary_types"]
