"""Area definitions: one registry, every formula spelled out (#975).

The model currently reports one area notion. Real projects need several, and
they differ (ROADMAP Part II, "Explicit area definitions"). This module is
the registry: each definition carries its formula spelled out, computed from
the model's own geometry, and the deltas between the definitions are
attributed to the convention that caused them, in the convention-report
spirit: the module only measures, and a number that cannot be measured is
reported as unavailable with the reason, never as zero.

Definitions (the registry is the deliverable; :func:`compute` applies them):

- ``gross``: exterior face of the exterior walls (GSF). Per level, the
  space-union ring grown outward by the exterior wall thickness:
  ``union_area + perimeter * t + pi * t**2``. Same ring, same Steiner term,
  and (via the caller) the same thickness evidence as the
  ``convention_bias`` check, so the two can never disagree. With no
  thickness evidence the definition is unavailable, never zero.
- ``construction``: interior face: the area of the union of the level's
  space polygons (the same ``footprint_from_regions`` ring the battery
  uses: outer ring only, interior holes dropped, largest part of a
  multi-wing union). Interior partitions contribute only where the source's
  space polygons cover them.
- ``rentable``: BOMA 2017 office Method A, whole-floor approximation:
  construction minus vertical penetrations (shafts, elevator cores). The
  model cannot yet identify floor common area or circulation, so the
  residual between rentable and assignable is reported as ``unallocated``,
  never as a BOMA factor.
- ``assignable``: program/assignable area: spaces classified ``room``
  (closets merge into rooms by the space_merge rule before this counts).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SCHEMA = "matchline.area_definitions/1"
PENETRATION_TYPES = ("shaft", "elevator_core")
# 1 mm^2 absorbs float noise without hiding a real ordering defect.
_ORDER_TOL_M2 = 1e-6

try:
    from geometry.geometry_simplify import footprint_from_regions, ring_perimeter

    _HAS_SIMPLIFY = True
except Exception:  # pragma: no cover - geometry_simplify is a hard dep
    footprint_from_regions = None
    ring_perimeter = None
    _HAS_SIMPLIFY = False

try:
    from datasets_adapter import polygon_area_px2
except Exception:  # pragma: no cover
    polygon_area_px2 = None


@dataclass(frozen=True)
class AreaDefinition:
    """One area definition: id, display name, formula, and why it is this way."""

    id: str
    name: str
    formula: str
    note: str = ""


REGISTRY: tuple[AreaDefinition, ...] = (
    AreaDefinition(
        id="gross",
        name="Gross floor area (exterior face)",
        formula="per level: union_area + perimeter * t + pi * t^2, summed over levels",
        note=(
            "exterior face of the exterior walls. t is the exterior wall "
            "thickness from the convention_bias evidence chain "
            "(material_layers, then thickness_m); the pi*t^2 corner term is "
            "the Steiner term documented in _check_convention_bias. With no "
            "thickness on the source, gross is unavailable, never zero."
        ),
    ),
    AreaDefinition(
        id="construction",
        name="Construction area (interior face)",
        formula="per level: area of the union of the level's space polygons",
        note=(
            "the union is the same footprint_from_regions ring the battery "
            "uses (outer ring only, interior holes dropped, largest part of "
            "a multi-wing union); interior partitions contribute only where "
            "the source's space polygons cover them."
        ),
    ),
    AreaDefinition(
        id="rentable",
        name="BOMA rentable (2017 office, Method A, whole-floor approximation)",
        formula=("per level: construction - vertical penetration area (shaft + elevator_core)"),
        note=(
            "whole-floor approximation: BOMA 2017 measures rentable to the "
            "dominant portion of the exterior wall and excludes vertical "
            "penetrations. The model cannot yet identify floor common area, "
            "so the residual between rentable and assignable is reported as "
            "unallocated, never as a BOMA factor."
        ),
    ),
    AreaDefinition(
        id="assignable",
        name="Program / assignable area (rooms)",
        formula="sum of area_m2 of spaces with poly_type == 'room'",
        note=(
            "closets merge into rooms by the space_merge rule before this "
            "counts; shafts and elevator cores are excluded here and "
            "itemized in the reconciliation."
        ),
    ),
)


def _ptype(space) -> str:
    return getattr(space, "poly_type", "room") or "room"


def _ring_area(ring) -> float:
    """Shoelace area of a ring; matches polygon_area_px2's absolute area."""
    area = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _union_level(spaces):
    """(union_area, perimeter, reason) for one level's space polygons."""
    if not _HAS_SIMPLIFY:
        return None, None, "geometry_simplify unavailable"
    polys = [sp.polygon_m for sp in spaces if getattr(sp, "polygon_m", None)]
    if not polys:
        return None, None, "no space polygons on this level"
    ring = footprint_from_regions(polys)
    if not ring:
        return None, None, "no usable space polygons on this level"
    area = polygon_area_px2(ring) if polygon_area_px2 is not None else _ring_area(ring)
    return float(area), float(ring_perimeter(ring)), None


def _entry(delta, verified, attributed):
    return {"verified": verified, "delta_m2": delta, "attributed": attributed}


def _reconciliation(
    gross,
    construction,
    rentable,
    assignable,
    space_sum,
    reason_union,
    reason_gross,
):
    """Ordering relations between the definitions, each delta attributed."""
    rel = {}
    if gross is None or rentable is None:
        rel["gross_gte_rentable"] = _entry(
            None, None, f"gross unavailable ({reason_gross}); not verified, not zero"
        )
    else:
        d = gross - rentable
        rel["gross_gte_rentable"] = _entry(
            d, d >= -_ORDER_TOL_M2, "exterior wall ring plus Steiner corner term"
        )
    if construction is None or rentable is None:
        rel["construction_gte_rentable"] = _entry(
            None, None, f"construction unavailable ({reason_union})"
        )
    else:
        d = construction - rentable
        rel["construction_gte_rentable"] = _entry(
            d, d >= -_ORDER_TOL_M2, "vertical penetrations (shaft + elevator_core)"
        )
    if rentable is None or assignable is None:
        rel["assignable_lte_rentable"] = _entry(None, None, "rentable unavailable")
    else:
        d = rentable - assignable
        rel["assignable_lte_rentable"] = _entry(
            d,
            d >= -_ORDER_TOL_M2,
            "unallocated: closet/unclassified spaces plus circulation the "
            "model cannot yet identify",
        )
    if construction is None:
        rel["components_match_union"] = _entry(
            None, None, f"construction unavailable ({reason_union})"
        )
    else:
        d = space_sum - construction
        if abs(d) <= _ORDER_TOL_M2:
            attr = "spaces tile the union"
        elif d > 0:
            attr = "overlapping space polygons"
        else:
            attr = "gaps between space polygons"
        rel["components_match_union"] = _entry(d, None, attr)
    return rel


def compute(model, thickness=None, thickness_source="", thickness_reason="") -> dict:
    """Compute every registered definition from ``model``'s own geometry.

    ``thickness``/``thickness_source`` come verbatim from the convention_bias
    payload when the caller has one (the convention report passes them
    through); ``thickness_reason`` carries the check's skip message when it
    could not measure one. Returns a JSON-ready dict: the registry, per-level
    rows, a per-space inclusion table, totals, and the reconciliation with
    each delta attributed.
    """
    out = {
        "schema": SCHEMA,
        "building": model.name,
        "geometry_available": _HAS_SIMPLIFY,
        "thickness": {
            "available": thickness is not None,
            "thickness_m": thickness,
            "source": thickness_source if thickness is not None else "",
            "reason": (
                thickness_reason
                or (
                    "no exterior wall thickness evidence was supplied; "
                    "gross is unmeasurable, not zero"
                )
            )
            if thickness is None
            else None,
        },
        "definitions": [
            {"id": d.id, "name": d.name, "formula": d.formula, "note": d.note} for d in REGISTRY
        ],
        "per_level": {},
        "totals": {},
        "per_space": [],
        "reconciliation": {},
        "spaces_without_area": [],
    }

    by_level: dict = {}
    for s in model.spaces.values():
        by_level.setdefault(s.level_id, []).append(s)
    lids = [lv.id for lv in model.levels]
    seen = set(lids)
    lids += [l for l in by_level if l not in seen]

    for lid in lids:
        sps = by_level.get(lid, [])
        missing = [s.id for s in sps if s.area_m2 is None]
        out["spaces_without_area"].extend(missing)
        rooms = sum(abs(s.area_m2) for s in sps if s.area_m2 is not None and _ptype(s) == "room")
        pens = sum(
            abs(s.area_m2) for s in sps if s.area_m2 is not None and _ptype(s) in PENETRATION_TYPES
        )
        other = sum(
            abs(s.area_m2)
            for s in sps
            if s.area_m2 is not None and _ptype(s) not in PENETRATION_TYPES and _ptype(s) != "room"
        )
        space_sum = rooms + pens + other
        union, perim, reason = _union_level(sps)
        gross = None
        if union is not None and thickness is not None:
            gross = union + perim * thickness + math.pi * thickness**2
        rentable = union - pens if union is not None else None
        reason_union = reason or ""
        reason_gross = reason or ("no exterior wall thickness" if thickness is None else "")
        out["per_level"][lid] = {
            "construction_union_m2": union,
            "construction_reason": reason,
            "space_area_sum_m2": space_sum,
            "gross_m2": gross,
            "rentable_m2": rentable,
            "assignable_m2": rooms,
            "penetration_m2": pens,
            "other_space_m2": other,
            "unallocated_m2": (rentable - rooms) if rentable is not None else None,
            "perimeter_m": perim,
            "spaces_without_area": missing,
        }
        out["reconciliation"][lid] = _reconciliation(
            gross, union, rentable, rooms, space_sum, reason_union, reason_gross
        )
        for s in sorted(sps, key=lambda x: x.id):
            t = _ptype(s)
            has_area = s.area_m2 is not None
            excluded = ""
            if t in PENETRATION_TYPES:
                excluded = "vertical penetration"
            elif t != "room":
                excluded = "unallocated (closet or unclassified)"
            out["per_space"].append(
                {
                    "space_id": s.id,
                    "level_id": s.level_id,
                    "poly_type": t,
                    "area_m2": s.area_m2,
                    "in_assignable": has_area and t == "room",
                    "in_rentable": has_area and t not in PENETRATION_TYPES,
                    "excluded_as": excluded,
                }
            )

    rows = out["per_level"]
    totals = {
        "space_area_sum_m2": sum(r["space_area_sum_m2"] for r in rows.values()),
        "assignable_m2": sum(r["assignable_m2"] for r in rows.values()),
        "penetration_m2": sum(r["penetration_m2"] for r in rows.values()),
        "other_space_m2": sum(r["other_space_m2"] for r in rows.values()),
    }
    for key in ("construction_union_m2", "gross_m2", "rentable_m2"):
        vals = {lid: r[key] for lid, r in rows.items()}
        avail = [v for v in vals.values() if v is not None]
        totals[key] = sum(avail) if avail else None
        missing_lids = [lid for lid, v in vals.items() if v is None]
        if missing_lids:
            totals["levels_without_" + key] = missing_lids

    def _total_reason(key):
        missing = totals.get("levels_without_" + key) or []
        return "unavailable on: " + ", ".join(missing) if missing else ""

    unalloc = [r["unallocated_m2"] for r in rows.values() if r["unallocated_m2"] is not None]
    if unalloc:
        totals["unallocated_m2"] = sum(unalloc)
        if len(unalloc) < len(rows):
            totals["levels_without_unallocated_m2"] = [
                lid for lid, r in rows.items() if r["unallocated_m2"] is None
            ]
    out["totals"] = totals
    out["reconciliation"]["total"] = _reconciliation(
        totals.get("gross_m2"),
        totals.get("construction_union_m2"),
        totals.get("rentable_m2"),
        totals["assignable_m2"],
        totals["space_area_sum_m2"],
        _total_reason("construction_union_m2"),
        _total_reason("gross_m2") or ("no exterior wall thickness" if thickness is None else ""),
    )
    return out
