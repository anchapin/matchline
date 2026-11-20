"""Battery footprint derivation keeps every connected part (#1417, decision 1).

Authored models draw spaces to inner faces, so the rooms of a level are
disjoint islands separated by wall thickness. The battery used to derive
the level footprint with ``footprint_from_regions``, which keeps only the
largest part of a disjoint union: every other room's area vanished from
the derived footprint and the area/volume conservation laws fired on a
correct multi-room model (#1415: rooms sum 36.96 m2, battery footprint
23.28 m2). The export path already uses the multipart sibling
(``footprint_multipart_from_regions``, #1311), which drops nothing.

These tests pin the derivation: the footprint of a level is the sum of
its parts, a single-room level is unchanged, and rooms that overlap are
counted once (union, not sum).
"""

import pytest
from shapely.geometry import Polygon

from building_model import BuildingModel, Level, Space
from geometry.geometry_simplify import footprint_from_regions
from validate import _build_ctx, run_checks

H = 3.0


def _shoelace(poly) -> float:
    """Absolute polygon area from its vertices (no shapely dependency)."""
    pts = list(poly)
    total = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def _model(rooms, height: float = H) -> BuildingModel:
    """One level, one space per room polygon; area_m2 == polygon area."""
    m = BuildingModel(name="battery_footprint")
    m.levels.append(Level(id="L1", name="Level 1", wall_height_m=height))
    for i, poly in enumerate(rooms):
        area = _shoelace(poly)
        m.spaces[f"L1-10{i + 1}"] = Space(
            id=f"L1-10{i + 1}",
            level_id="L1",
            name=f"ROOM {i + 1}",
            number=f"10{i + 1}",
            polygon_m=[list(p) for p in poly],
            area_m2=area,
            volume_m3=area * height,
        )
    return m


# Two disjoint inner-face rooms: 10 m x 4 m and 6 m x 4 m, 2 m apart.
ROOM_A = [[0.0, 0.0], [10.0, 0.0], [10.0, 4.0], [0.0, 4.0]]
ROOM_B = [[12.0, 0.0], [18.0, 0.0], [18.0, 4.0], [12.0, 4.0]]


def _by_id(report, check_id):
    for res in report.results:
        if res.check_id == check_id:
            return res
    raise AssertionError(
        f"check {check_id!r} not in report: {[r.check_id for r in report.results]}"
    )


def test_two_disjoint_rooms_pass_area_and_volume_conservation():
    """The defect: disjoint rooms, the second one dropped from the footprint."""
    model = _model([ROOM_A, ROOM_B])
    report = run_checks(model)

    area = _by_id(report, "area_conservation")
    volume = _by_id(report, "volume_conservation")
    assert area.severity != "error", area.message
    assert volume.severity != "error", volume.message


def test_single_room_footprint_area_unchanged():
    """A one-room level derives the same footprint area as before."""
    poly = [[0.0, 0.0], [10.0, 0.0], [10.0, 4.0], [0.0, 4.0]]
    model = _model([poly])
    ring = footprint_from_regions([poly])
    before = _shoelace(ring)
    ctx = _build_ctx(model)
    assert ctx.footprint_area["L1"] == pytest.approx(before, rel=1e-9, abs=1e-12)


def test_overlapping_rooms_are_counted_once():
    """Overlapping rooms share one union: the area is the union area."""
    poly_a = [[0.0, 0.0], [10.0, 0.0], [10.0, 4.0], [0.0, 4.0]]
    poly_b = [[5.0, 0.0], [15.0, 0.0], [15.0, 4.0], [5.0, 4.0]]
    model = _model([poly_a, poly_b])
    union = Polygon(poly_a).union(Polygon(poly_b)).area
    ctx = _build_ctx(model)
    assert ctx.footprint_area["L1"] == pytest.approx(union, rel=1e-6)
