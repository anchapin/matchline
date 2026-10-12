"""Lighting power takeoff: fixtures x schedule watts -> LPD per space.

Extends the three-stage count x schedule pipeline (datasets_adapter) to
lighting:

  (1) FIXTURE SPOTTING ... classify fixture symbols on the floor plan ->
      count per schedule tag (Detection with tag, e.g. "A").
  (2) SCHEDULE PARSING ... lighting fixture schedule -> {tag: watts/fixture}
      (v1: CSV via parse_lighting_schedule_csv).
  (3) POWER ROLLUP ...... count x watts per tag = installed watts; fixtures
      are assigned to room polygons (centroid point-in-polygon) so each
      space gets total watts and lighting power density (LPD, W/m2 and
      W/ft2). Building total = sum over spaces + unassigned fixtures.

Unlike the window/door area rollup, lighting needs room AREAS, so a drawing
scale (or precomputed space areas in m2) is required for LPD. Watts need no
scale: they come from the schedule.

Output contract
---------------
* ``LightingLine`` - one joined row: count x watts for one tag.
* ``RoomLighting``  - one space: area, fixture count, watts, LPD.
* ``LightingResult``- per-tag lines, building total watts, per-room LPD,
  unmatched detections (tag with no schedule entry -- reported, never
  dropped), unassigned detections (matched tag, but centroid in no room).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from building_model import Provenance
from datasets_adapter import (
    TAKEOFF_CATEGORIES,
    Detection,
    DrawingScale,
    ScheduleEntry,
    polygon_area_px2,
)
from room_labels import LabeledSpace, point_in_polygon

assert "lighting" in TAKEOFF_CATEGORIES  # adapter extended for this module

M2_PER_FT2 = 0.09290304
FT2_PER_M2 = 1.0 / M2_PER_FT2  # 10.7639


@dataclass
class LightingLine:
    """Stage-3 output: count x scheduled watts for one fixture tag."""

    tag: str
    description: str
    count: int
    watts_each: float | None
    total_w: float | None  # count * watts_each (None if watts unknown)
    provenance: Provenance | None = None


@dataclass
class RoomLighting:
    """Per-space lighting rollup."""

    name: str
    number: str
    area_m2: float | None
    fixture_count: int
    watts: float
    lpd_w_m2: float | None  # watts / area_m2
    lpd_w_ft2: float | None
    provenance: Provenance | None = None


@dataclass
class LightingResult:
    drawing_type: str = "floor_plan"
    lines: list = field(default_factory=list)  # LightingLine per tag
    rooms: list = field(default_factory=list)  # RoomLighting per space
    total_w: float = 0.0  # building installed watts
    total_fixtures: int = 0
    unmatched: list = field(default_factory=list)  # Detection: tag not in schedule
    unassigned: list = field(default_factory=list)  # Detection: tag ok, no room found
    no_watts: list = field(default_factory=list)  # Detection: schedule entry lacks watts
    provenance: Provenance | None = None


def _detection_centroid(d: Detection) -> tuple:
    xtl, ytl, xbr, ybr = d.bbox
    return ((xtl + xbr) / 2.0, (ytl + ybr) / 2.0)


def assign_fixtures_to_spaces(
    detections: list[Detection], spaces: list[LabeledSpace]
) -> dict[int, list[Detection]]:
    """Map each detection to a space index by centroid point-in-polygon.

    Returns {space_idx: [detections]}; detections whose centroid falls in no
    space map to key -1 (unassigned -- reported, never dropped).
    """
    out: dict[int, list[Detection]] = {i: [] for i in range(len(spaces))}
    out[-1] = []
    for d in detections:
        c = _detection_centroid(d)
        hit = -1
        for i, s in enumerate(spaces):
            if point_in_polygon(c, s.polygon_px):
                hit = i
                break
        out[hit].append(d)
    return out


def lighting_takeoff(
    detections: list[Detection],
    schedule: dict[str, ScheduleEntry],
    spaces: list[LabeledSpace],
    scale: DrawingScale,
    space_areas_m2: list[float | None] | None = None,
    drawing_type: str = "floor_plan",
) -> LightingResult:
    """Stage 3 for lighting: join detections to the fixture schedule.

    * ``detections`` -- fixture Detections with schedule tags (stage 1).
    * ``schedule``   -- parse_lighting_schedule_csv output (stage 2).
    * ``spaces``     -- LabeledSpace list (room polygons + names/numbers).
    * ``scale``      -- DrawingScale for px->m2 (LPD needs areas; watts don't).
    * ``space_areas_m2`` -- optional precomputed areas (e.g. synthetic GT in
      meters); overrides polygon measurement when given.

    Watts come from the schedule, so totals need no drawing scale. LPD does:
    spaces with unknown area get watts and fixture counts but LPD None.
    """
    res = LightingResult(drawing_type=drawing_type)

    # -- per-tag rollup (mirrors datasets_adapter.rollup_takeoff) ------------
    by_tag: dict[str, list[Detection]] = {}
    for d in detections:
        by_tag.setdefault(d.tag, []).append(d)

    tag_watts: dict[str, float | None] = {}
    for tag in sorted(by_tag):
        ds = by_tag[tag]
        entry = schedule.get(tag)
        if entry is None:
            res.unmatched.extend(ds)
            continue
        n = len(ds)
        w = entry.watts
        if w is None:
            res.no_watts.extend(ds)
            total = None
        else:
            total = n * w
            res.total_w += total
        res.total_fixtures += n
        res.lines.append(
            LightingLine(
                tag=tag, description=entry.description, count=n, watts_each=w, total_w=total
            )
        )
        tag_watts[tag] = w

    # -- per-space assignment -------------------------------------------------
    assignment = assign_fixtures_to_spaces(
        [d for ds in by_tag.values() for d in ds if d.tag in schedule], spaces
    )
    res.unassigned = assignment.pop(-1, [])

    for i, s in enumerate(spaces):
        ds = assignment.get(i, [])
        watts = sum(tag_watts.get(d.tag) or 0.0 for d in ds if tag_watts.get(d.tag) is not None)
        if space_areas_m2 is not None:
            area = space_areas_m2[i]
        elif scale.m_per_px:
            area = polygon_area_px2(s.polygon_px) * scale.m_per_px**2
        else:
            area = None
        if area:
            lpd_m2 = watts / area
            lpd_ft2 = lpd_m2 / FT2_PER_M2
        else:
            lpd_m2 = lpd_ft2 = None
        res.rooms.append(
            RoomLighting(
                name=s.name,
                number=s.number,
                area_m2=area,
                fixture_count=len(ds),
                watts=watts,
                lpd_w_m2=lpd_m2,
                lpd_w_ft2=lpd_ft2,
            )
        )
    return res


def summarize_lighting(res: LightingResult) -> str:
    """One-line-per-room human summary (for logs, not a user document)."""
    out = [f"total {res.total_w:.1f} W across {res.total_fixtures} fixtures"]
    for r in res.rooms:
        tag = f"{r.name} {r.number}".strip() or "(unlabeled)"
        lpd = f"{r.lpd_w_m2:.2f} W/m2" if r.lpd_w_m2 is not None else "LPD n/a"
        out.append(f"  {tag}: {r.watts:.1f} W, {r.fixture_count} fixt, {lpd}")
    if res.unmatched:
        out.append(f"  UNMATCHED tags: {sorted({d.tag for d in res.unmatched})}")
    if res.unassigned:
        out.append(f"  UNASSIGNED fixtures: {len(res.unassigned)}")
    return "\n".join(out)
