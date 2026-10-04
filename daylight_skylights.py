"""Daylight area under skylights (roadmap item 3, toplighting).

Definition used (ASHRAE 90.1 Section 3.2, as revised by the addenda package
t ... co to 90.1-2016, Figure 3.2-2): the daylight area under each skylight
is bounded by the opening beneath the skylight and extends horizontally in
each direction by the smaller of

  a. 70% of the ceiling height (0.7 x CH), CH taken at the lowest edge of
     the skylight, or
  b. the distance to the nearest face of any opaque vertical obstruction
     farther away than 0.7 x (CH - OH), OH being the obstruction's height.

The combined daylight area of a space is the union of the per-skylight
areas within it.

What is modelled: (a) exactly, and (b) for full-height obstructions only:
the space's own boundary is a wall reaching the ceiling (OH = CH), so the
zone stops at its face, which is the clip to the space polygon. Partial-
height obstructions inside a space (shelving, low partitions) are NOT in
the model, so zones may be optimistic in such spaces. Multistory spaces
(Figure 3.2-5) are not handled. Skylights are axis-aligned in plan
(width along x, height along y), matching the IFC import convention.

A skylight with no known plan position or size is listed in
``SpaceDaylight.unplaced_skylights``, never placed by guess.
"""

from __future__ import annotations

from building_model import DaylitZone, Provenance

SOURCE = (
    "ASHRAE 90.1 Sec. 3.2 'daylight area under skylights' (addenda t..co to "
    "90.1-2016, Fig. 3.2-2): skylight opening + min(0.7xCH, obstruction) each "
    "direction; full-height boundary walls only, partial-height obstructions "
    "and multistory spaces not modelled"
)
SPREAD_FACTOR = 0.7  # x ceiling height, each horizontal direction
METHOD = "daylight:under_skylight"


def _skylight_ok(o) -> bool:
    c = getattr(o, "plan_center_m", None)
    return c is not None and len(c) == 2 and (o.width_m or 0) > 0 and (o.height_m or 0) > 0


def compute_skylight_daylight(space, ceiling_height_m: float | None) -> None:
    """Fill ``space.daylight.toplit`` from the space's skylights.

    Clears any previous toplit result first, so re-running is idempotent.
    With no usable ceiling height, every skylight is reported unplaced:
    the spread cannot be computed and is not assumed.
    """
    from shapely.geometry import Polygon, box
    from shapely.ops import unary_union

    dl = space.daylight
    dl.toplit, dl.toplit_m2, dl.unplaced_skylights = [], 0.0, []
    skylights = [o for o in space.openings if o.category == "skylight"]
    if not skylights:
        return
    room = Polygon(space.polygon_m) if space.polygon_m and len(space.polygon_m) >= 3 else None
    if room is not None and not room.is_valid:
        room = room.buffer(0)
    if room is None or room.is_empty or not ceiling_height_m or ceiling_height_m <= 0:
        dl.unplaced_skylights = sorted(o.id for o in skylights)
        return

    spread = SPREAD_FACTOR * ceiling_height_m
    shapes = []
    for o in sorted(skylights, key=lambda o: o.id):
        if not _skylight_ok(o):
            dl.unplaced_skylights.append(o.id)
            continue
        cx, cy = o.plan_center_m
        hw, hh = o.width_m / 2 + spread, o.height_m / 2 + spread
        zone = box(cx - hw, cy - hh, cx + hw, cy + hh).intersection(room)
        if zone.is_empty or zone.area <= 0:
            dl.unplaced_skylights.append(o.id)
            continue
        shapes.append(zone)
        geom = zone if zone.geom_type == "Polygon" else max(zone.geoms, key=lambda g: g.area)
        conf = o.provenance.confidence if o.provenance is not None else 0.5
        dl.toplit.append(
            DaylitZone(
                id=f"{space.id}-DLS-{o.id}",
                zone_class="under_skylight",
                window_id=o.id,
                polygon_m=[[round(x, 4), round(y, 4)] for x, y in list(geom.exterior.coords)[:-1]],
                area_m2=round(zone.area, 4),
                head_height_m=ceiling_height_m,
                provenance=Provenance(
                    sheet_id=o.provenance.sheet_id if o.provenance else "derived",
                    revision=o.provenance.revision if o.provenance else 0,
                    method=METHOD,
                    confidence=conf,
                    note=f"{SOURCE}; CH={ceiling_height_m:g} m, spread={spread:.3f} m",
                ),
            )
        )
    if shapes:
        dl.toplit_m2 = round(unary_union(shapes).area, 4)
