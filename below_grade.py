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

When the source carries a site terrain (``BuildingModel.terrain``, #641) the
ground decides per wall and per slab instead of per storey, which is what a
walk-out basement needs:

- a wall with grade at or above its top is ``UndergroundWall``, at or below
  its base ``ExteriorWall``, and in between it is ``Split`` (the multi-storey
  gbXML writer cuts it at grade into an underground and an exterior part);
- a lowest floor is ``SlabOnGrade`` when grade drops to the slab anywhere
  along its edge (the exposed side), ``UndergroundSlab`` when buried all round;
- a wall or slab the terrain does not cover keeps the storey's type and goes
  to review, since no grade source covers it.

Levels with the property unstated keep today's above-ground types, so models
without it are classified exactly as the single-storey writers already
export them; a level that looks like a basement but is unstated is flagged,
not reclassified. The model is not modified apart from review items.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from building_model import Provenance
from grade import GRADE_TOL_M, Terrain

REVIEW_CONF = 0.5
ELEV_TOL_M = 1e-3


@dataclass
class BoundaryResult:
    walls: Dict[str, Optional[str]] = field(default_factory=dict)  # EnvelopeWall.id -> type
    horizontals: Dict[str, Optional[str]] = field(default_factory=dict)  # surface id -> type
    below_grade_levels: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    split_walls: List[str] = field(default_factory=list)  # part-buried by terrain (#641)


def _prov(note: str) -> Provenance:
    return Provenance(
        sheet_id="model", revision=0, method="below_grade", confidence=REVIEW_CONF, note=note
    )


def wall_by_grade(profile, z0: float, z1: float) -> str:
    """Wall type from a ground profile [(u, z)] and the wall's base/top heights."""
    if all(z >= z1 - GRADE_TOL_M for _, z in profile):
        return "UndergroundWall"
    if all(z <= z0 + GRADE_TOL_M for _, z in profile):
        return "ExteriorWall"
    return "Split"


def _edge_grade_min(terrain, geom) -> Optional[float]:
    """Lowest ground along the outer edge of a slab piece, or None if not covered."""
    parts = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    low = None
    for part in parts:
        pts = list(part.exterior.coords)
        for a, b in zip(pts, pts[1:]):
            prof = terrain.profile(a, b)
            if prof is None:
                return None
            m = min(z for _, z in prof)
            low = m if low is None else min(low, m)
    return low


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
    by_id = {lv.id: lv for lv in levels}
    terrain = Terrain(getattr(model, "terrain", None) or [])

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
        stype = "UndergroundWall" if is_below(lid) else "ExteriorWall"
        if terrain:
            lv = by_id[lid]
            prof = terrain.profile(w.from_m, w.to_m)
            if prof is None:
                note(
                    f"envelope wall {w.id} is not covered by the site terrain; "
                    f"{stype} from the storey"
                )
            else:
                stype = wall_by_grade(prof, lv.elevation_z_m, lv.elevation_z_m + lv.wall_height_m)
                if stype == "Split":
                    res.split_walls.append(w.id)
        res.walls[w.id] = stype

    for s in interstory.surfaces:
        if s.kind == "interior":
            res.horizontals[s.id] = "InteriorFloor"
        elif s.kind == "ground":
            lid = level_of_space[s.upper_space_id]
            stype = "UndergroundSlab" if is_below(lid) else "SlabOnGrade"
            if terrain:
                low = _edge_grade_min(terrain, s.geom)
                if low is None:
                    note(
                        f"{s.id}: slab edge not covered by the site terrain; {stype} from the storey"
                    )
                else:
                    stype = "SlabOnGrade" if low <= s.z_m + GRADE_TOL_M else "UndergroundSlab"
            res.horizontals[s.id] = stype
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


__all__ = ["BoundaryResult", "boundary_types", "wall_by_grade"]
