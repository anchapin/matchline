"""Clinic walls harness (#740): plan cut of a mesh and the sheet round trip.

The Clinic IFC itself is held out; these use small meshes and shapely shapes.
"""

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from plan_walls import compare_rooms, extract_walls
from scripts.validate_clinic_walls import (
    DOOR_PPM,
    M_PER_PT,
    cut_polygons,
    door_sheet,
    door_swing,
    glazing_line,
    match_doors,
    match_windows,
    section,
    sheet_for,
    wall_bounded_truth,
)


def _box_mesh(x0, y0, x1, y1, z0, z1):
    v = np.array(
        [[x, y, z] for z in (z0, z1) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))],
        dtype=float,
    )
    q = [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (0, 3, 2, 1), (4, 5, 6, 7)]
    f = np.array([t for a, b, c, d in q for t in ((a, b, c), (a, c, d))], dtype=int)
    return v, f


def test_plan_cut_of_a_wall_solid_is_its_footprint():
    v, f = _box_mesh(0.0, 0.0, 4.0, 0.2, 0.0, 3.0)
    polys = cut_polygons(section(v, f, 1.2))
    assert len(polys) == 1
    assert abs(polys[0].area - 0.8) < 1e-6
    assert section(v, f, 3.5) == []  # above the wall: nothing cut


def test_one_room_round_trips_through_the_sheet_in_ifc_coordinates():
    # a 5 x 4 m room (inside faces) bounded by 0.2 m walls, far from the origin
    ox, oy = 1000.0, 2000.0
    outer = box(ox - 0.2, oy - 0.2, ox + 5.2, oy + 4.2)
    inner = box(ox, oy, ox + 5.0, oy + 4.0)
    walls = [outer.difference(inner)]
    ring = list(inner.exterior.coords)[:-1]
    sheet, (dx, dy) = sheet_for(walls, [("101", "OFFICE", ring)])
    res = extract_walls(sheet, M_PER_PT)
    assert len(res.rooms) == 1
    assert res.rooms[0].label["number"] == "101"
    pred = [[(x + dx, y + dy) for x, y in res.rooms[0].polygon_m]]
    cmp = compare_rooms(pred, [ring])
    assert cmp["matched"] == 1
    # the room face is drawn on wall centrelines, so it is a little larger than the net room
    assert Polygon(pred[0]).area >= Polygon(ring).area


def test_spaces_join_across_an_edge_no_wall_stands_on():
    from shapely.geometry import box

    rooms = [
        ("101", "CORRIDOR", [(0, 0), (4, 0), (4, 2), (0, 2)]),
        ("102", "CORRIDOR", [(4, 0), (8, 0), (8, 2), (4, 2)]),
        ("103", "OFFICE", [(0, 2), (4, 2), (4, 5), (0, 5)]),
    ]
    # a wall between the corridor and the office, none between the two corridor segments
    walls = [box(-0.1, 1.9, 8.1, 2.1)]
    out = sorted(wall_bounded_truth(rooms, walls), key=lambda g: len(g[0]))
    assert [sorted(n) for n, _ in out] == [["OFFICE"], ["CORRIDOR", "CORRIDOR"]]
    from shapely.geometry import Polygon

    assert abs(Polygon(out[1][1]).area - 16.0) < 1e-6


# ---- window openings drawn as glass lines (#743) ----------------------------


def _shell_with_window(ox=1000.0, oy=2000.0):
    """A 10 x 6 m room in 0.2 m walls with a 2 m window opening in the south wall."""
    outer = box(ox - 0.2, oy - 0.2, ox + 10.2, oy + 6.2)
    inner = box(ox, oy, ox + 10.0, oy + 6.0)
    opening = box(ox + 4.0, oy - 0.2, ox + 6.0, oy)  # wall-deep, window-wide
    walls = [outer.difference(inner).difference(opening)]
    ring = list(inner.exterior.coords)[:-1]
    return walls, ring, opening


def test_glass_line_runs_along_the_wall_through_the_opening():
    walls, _r, opening = _shell_with_window(0.0, 0.0)
    a, b = glazing_line(opening)
    assert sorted([a[0], b[0]]) == pytest.approx([4.0, 6.0])
    assert a[1] == pytest.approx(-0.1) and b[1] == pytest.approx(-0.1)
    # a window narrower than its wall is deep still runs along the wall
    from shapely.geometry import box as bx
    from shapely.ops import unary_union

    deep = [bx(0.0, 0.0, 4.0, 0.5).difference(bx(1.0, 0.0, 1.3, 0.5))]
    a, b = glazing_line(bx(1.0, 0.0, 1.3, 0.5), unary_union(deep))
    assert sorted([a[0], b[0]]) == pytest.approx([1.0, 1.3])
    assert a[1] == pytest.approx(0.25) and b[1] == pytest.approx(0.25)


def test_window_drawn_from_the_ifc_reads_back_as_one_window_detection():
    from detection_provider import window_detections

    walls, ring, opening = _shell_with_window()
    sheet, off = sheet_for(walls, [("101", "OFFICE", ring)], glazing=[opening])
    res = extract_walls(sheet, M_PER_PT).to_dict()
    dets = window_detections(res, sheet["height_pt"], 1.0, "s")
    got = match_windows([opening], dets, sheet["height_pt"], 1.0, off)
    assert got == {"truth": 1, "pred": 1, "recall": 1.0, "precision": 1.0}
    # without the glass line the gap is a plain opening and no window is found
    bare, off = sheet_for(walls, [("101", "OFFICE", ring)])
    res = extract_walls(bare, M_PER_PT).to_dict()
    dets = window_detections(res, bare["height_pt"], 1.0, "s")
    assert match_windows([opening], dets, bare["height_pt"], 1.0, off)["recall"] == 0.0


def test_a_partition_beside_a_window_does_not_turn_its_glass_line():
    # partitions meeting the inside face 0.6 m past each window (#743): the
    # glass still runs along the exterior wall, and every window reads back
    from shapely.ops import unary_union

    from detection_provider import window_detections

    th, L, pt = 0.267, 22.0, 0.133
    xs = [3.0, 7.0, 11.0, 15.0]
    ops = [box(x, -th, x + 1.0, 0.0) for x in xs]
    w = unary_union(
        [box(-th, -th, L + th, 8 + th).difference(box(0, 0, L, 8))]
        + [box(x + 1.6 - pt / 2, 0.0, x + 1.6 + pt / 2, 4.0) for x in xs[:3]]
    )
    for o in ops:
        w = w.difference(o)
    for o in ops:
        a, b = glazing_line(o, w)
        assert a[1] == pytest.approx(-th / 2) and b[1] == pytest.approx(-th / 2)
        assert sorted([a[0], b[0]]) == pytest.approx([o.bounds[0], o.bounds[2]])
    ring = [(0, 0), (L, 0), (L, 8), (0, 8)]
    sheet, off = sheet_for([w], [("101", "OFFICE", ring)], glazing=ops)
    res = extract_walls(sheet, M_PER_PT).to_dict()
    dets = window_detections(res, sheet["height_pt"], 1.0, "s")
    got = match_windows(ops, dets, sheet["height_pt"], 1.0, off)
    assert got == {"truth": 4, "pred": 4, "recall": 1.0, "precision": 1.0}


def test_a_window_near_an_inside_corner_keeps_its_glass_along_the_wall():
    # the end wall is longer than the window's own faces nearby, but it does
    # not lie on the window's side lines, so it cannot turn the glass
    th, L = 0.267, 22.0
    ops = [box(x, -th, x + 1.0, 0.0) for x in (10.0, 13.5, 17.0, 20.5)]
    w = box(-th, -th, L + th, 8 + th).difference(box(0, 0, L, 8))
    for o in ops:
        w = w.difference(o)
    a, b = glazing_line(ops[-1], w)
    assert sorted([a[0], b[0]]) == pytest.approx([20.5, 21.5])
    assert a[1] == pytest.approx(-th / 2)


# ---- doors drawn from IFC openings as swings, read back by door_detect (#743) ----


def _room_with_doors(t=0.2):
    """A 10 x 6 m room: a 0.9 m door in the south wall, a 1.0 m door in a partition."""
    walls = [
        box(0, 0, 2.5, t),
        box(3.4, 0, 10, t),
        box(0, 6 - t, 10, 6),
        box(0, 0, t, 6),
        box(10 - t, 0, 10, 6),
        box(5 - t / 2, t, 5 + t / 2, 2.0),
        box(5 - t / 2, 3.0, 5 + t / 2, 6 - t),
    ]
    doors = [box(2.5, 0, 3.4, t), box(5 - t / 2, 2.0, 5 + t / 2, 3.0)]
    return walls, doors


def test_door_swing_stands_square_to_the_wall_from_a_jamb():
    from shapely.ops import unary_union

    walls, doors = _room_with_doors()
    hinge, tip, jamb, r = door_swing(doors[0], unary_union(walls))
    assert r == pytest.approx(0.9)
    assert hinge[1] == pytest.approx(0.1) and jamb[1] == pytest.approx(0.1)  # on the centre line
    assert abs(tip[0] - hinge[0]) == pytest.approx(0.0, abs=1e-9)  # square to the wall
    assert abs(tip[1] - hinge[1]) == pytest.approx(0.9)


def test_doors_drawn_from_the_ifc_read_back_as_door_detections():
    from door_detect import detect_door_swings

    walls, doors = _room_with_doors()
    img, to_px = door_sheet(walls, doors)
    dets = detect_door_swings(img, DOOR_PPM)
    got = match_doors(doors, dets, to_px)
    assert got == {"truth": 2, "pred": 2, "recall": 1.0, "precision": 1.0}
    widths = sorted(d["width_px"] / DOOR_PPM for d in dets)
    assert widths == pytest.approx([0.9, 1.0], abs=0.08)


def test_a_wall_gap_with_no_swing_is_not_a_door():
    from door_detect import detect_door_swings

    walls, doors = _room_with_doors()
    img, to_px = door_sheet(walls, [])  # the gaps are there, the swings are not
    dets = detect_door_swings(img, DOOR_PPM)
    assert match_doors(doors, dets, to_px) == {
        "truth": 2,
        "pred": 0,
        "recall": 0.0,
        "precision": None,
    }


def test_the_swing_goes_to_the_face_with_no_wall_in_the_way():
    from door_detect import detect_door_swings

    t = 0.2
    # a door in a wall with a parallel wall 0.5 m off its north face: a 0.9 m
    # leaf swung north would cross it, so the swing is drawn south
    walls = [box(0, 2, 2.5, 2 + t), box(3.4, 2, 8, 2 + t), box(0, 2.7, 8, 2.7 + t)]
    door = box(2.5, 2, 3.4, 2 + t)
    img, to_px = door_sheet(walls, [door])
    dets = detect_door_swings(img, DOOR_PPM)
    assert match_doors([door], dets, to_px)["recall"] == 1.0
    (y_px,) = [d["hinge_px"][1] for d in dets]
    assert dets[0]["bbox_px"][3] > to_px(3.0, 2.1)[1] + 0.5 * DOOR_PPM  # reaches south
    assert y_px == pytest.approx(to_px(3.0, 2.1)[1], abs=3)
