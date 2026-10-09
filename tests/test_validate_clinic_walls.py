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


def _shell_with_box(*, window=True, gap=0.175, box_len=2.0):
    """An exterior wall with a small box enclosure on its inside face (#868).

    A 0.168 m box wall stands ``gap`` off the face with a return at each end,
    as the Clinic's first floor has; the window, if any, is further along.
    """
    th, L, et, x0 = 0.267, 22.0, 0.168, 5.0
    ops = [box(14.0, -th, 15.0, 0.0)] if window else []
    shell = box(-th, -th, L + th, 8 + th).difference(box(0, 0, L, 8))
    for o in ops:
        shell = shell.difference(o)
    walls = [
        shell,
        box(x0, gap, x0 + box_len, gap + et),
        box(x0 - et, 0.0, x0, gap + et),
        box(x0 + box_len, 0.0, x0 + box_len + 0.124, gap + et),
    ]
    ring = [(0, 0), (L, 0), (L, 8), (0, 8)]
    return walls, ring, ops


@pytest.mark.parametrize("box_len", [1.0, 2.0])
def test_a_box_against_the_wall_is_not_a_window_and_the_real_one_is_kept(box_len):
    from detection_provider import window_detections

    walls, ring, ops = _shell_with_box(box_len=box_len)
    sheet, off = sheet_for(walls, [("101", "OFFICE", ring)], glazing=ops)
    res = extract_walls(sheet, M_PER_PT).to_dict()
    dets = window_detections(res, sheet["height_pt"], 1.0, "s")
    got = match_windows(ops, dets, sheet["height_pt"], 1.0, off)
    assert got == {"truth": 1, "pred": 1, "recall": 1.0, "precision": 1.0}


def test_a_box_against_a_wall_with_no_window_gives_no_window():
    walls, ring, _ = _shell_with_box(window=False)
    sheet, _ = sheet_for(walls, [("101", "OFFICE", ring)])
    res = extract_walls(sheet, M_PER_PT).to_dict()
    assert [o for o in res["openings"] if o.get("kind") == "window"] == []
    # the box wall is still extracted: only the window reading changed
    assert len(res["walls"]) == 5


def _shell_with_cavity_wall(leaf, cavity, inner=None):
    """A shell whose south wall is two leaves with a cavity, a window through both (#872).

    ``inner`` is the inner leaf's thickness when it differs from the outer one (#877).
    """
    th, L = 0.267, 22.0
    inner = leaf if inner is None else inner
    T = leaf + cavity + inner
    rest = box(-th, -th, L + th, 8 + th).difference(box(0, 0, L, 8))
    rest = rest.difference(box(-th, -th, L + th, 0.0))
    op = box(14.0, -T, 15.0, 0.0)
    leaves = [box(-th, -T, L + th, -T + leaf), box(-th, -inner, L + th, 0.0)]
    walls = [rest] + [lf.difference(op) for lf in leaves]
    ring = [(0, 0), (L, 0), (L, 8), (0, 8)]
    return walls, ring, [op]


@pytest.mark.parametrize("leaf,cavity", [(0.10, 0.10), (0.10, 0.05), (0.15, 0.10), (0.10, 0.15)])
def test_window_through_a_double_leaf_wall_is_found(leaf, cavity):
    from detection_provider import window_detections

    walls, ring, ops = _shell_with_cavity_wall(leaf, cavity)
    sheet, off = sheet_for(walls, [("101", "OFFICE", ring)], glazing=ops)
    res = extract_walls(sheet, M_PER_PT).to_dict()
    dets = window_detections(res, sheet["height_pt"], 1.0, "s")
    got = match_windows(ops, dets, sheet["height_pt"], 1.0, off)
    assert got == {"truth": 1, "pred": 1, "recall": 1.0, "precision": 1.0}


@pytest.mark.parametrize(
    "leaf,cavity,inner",
    [(0.10, 0.10, 0.20), (0.15, 0.10, 0.25), (0.05, 0.10, 0.15), (0.10, 0.10, 0.19)],
)
def test_window_whose_glass_line_lies_on_a_leaf_face_is_found(leaf, cavity, inner):
    # the glass line, drawn on the wall's centre, falls on the inner leaf's
    # cavity face, so the wall splits into two walls meeting on it (#877)
    from detection_provider import window_detections

    walls, ring, ops = _shell_with_cavity_wall(leaf, cavity, inner)
    sheet, off = sheet_for(walls, [("101", "OFFICE", ring)], glazing=ops)
    res = extract_walls(sheet, M_PER_PT).to_dict()
    dets = window_detections(res, sheet["height_pt"], 1.0, "s")
    got = match_windows(ops, dets, sheet["height_pt"], 1.0, off)
    assert got == {"truth": 1, "pred": 1, "recall": 1.0, "precision": 1.0}


def test_asymmetric_double_leaf_wall_with_no_glass_is_not_a_window():
    walls, ring, _ = _shell_with_cavity_wall(0.10, 0.10, 0.20)
    sheet, _ = sheet_for(walls, [("101", "OFFICE", ring)])  # no glass drawn
    res = extract_walls(sheet, M_PER_PT).to_dict()
    assert [o for o in res["openings"] if o.get("kind") == "window"] == []


def test_double_leaf_wall_with_a_plain_gap_is_not_a_window():
    walls, ring, ops = _shell_with_cavity_wall(0.10, 0.10)
    sheet, _ = sheet_for(walls, [("101", "OFFICE", ring)])  # no glass drawn
    res = extract_walls(sheet, M_PER_PT).to_dict()
    assert [o for o in res["openings"] if o.get("kind") == "window"] == []


def _pier_with_lining():
    """Two windows either side of a pier, a 0.054 m lining with a return behind it (#873).

    The Clinic's second floor at IFC (-7.27, 50.2), moved to the origin: a
    0.267 m exterior wall, a 0.124 m partition meeting it between the
    windows, and a 0.054 m lining running off along the inside face. Before
    #868 the lining's face line folded wall, gap and lining into one 0.576 m
    band beside the pier, which was called a 0.57 m window.
    """
    dx, dy = 12.0, -50.203

    def b(x0, y0, x1, y1):
        return box(x0 + dx, y0 + dy, x1 + dx, y1 + dy)

    def ring(x0, y0, x1, y1):
        return [(x0 + dx, y0 + dy), (x1 + dx, y0 + dy), (x1 + dx, y1 + dy), (x0 + dx, y1 + dy)]

    ops = [b(-9.68, 50.203, -8.68, 50.47), b(-6.69, 50.203, -5.69, 50.47)]
    ext = b(-11.5, 50.203, 0.113, 50.47)
    for o in ops:
        ext = ext.difference(o)
    walls = [
        ext,
        b(-11.114, 48.6, -10.99, 50.203),
        b(-7.68, 48.6, -7.556, 50.203),  # partition meeting the wall at the pier
        b(-4.27, 48.6, -4.146, 49.894),
        b(-7.556, 49.894, -6.931, 49.948),  # lining
        b(-6.985, 49.948, -6.931, 50.203),  # its return to the wall
        b(-5.136, 49.894, 0.113, 49.948),
        b(-5.19, 49.894, -5.136, 50.203),
        b(0.113, 48.6, 0.38, 50.47),
    ]
    walls = [g for w in walls for g in getattr(w, "geoms", [w])]
    rooms = [
        ("2A06", "OFFICE", ring(-10.99, 48.6, -7.68, 50.203)),
        ("2A05", "OFFICE", ring(-7.556, 48.6, -4.27, 50.203)),
        ("2A04", "OFFICE", ring(-4.146, 48.6, 0.113, 49.894)),
    ]
    return walls, rooms, ops


def test_pier_with_a_lining_is_not_a_window_and_both_real_ones_are_kept():
    from detection_provider import window_detections

    walls, rooms, ops = _pier_with_lining()
    sheet, off = sheet_for(walls, rooms, glazing=ops)
    res = extract_walls(sheet, M_PER_PT).to_dict()
    dets = window_detections(res, sheet["height_pt"], 1.0, "s")
    got = match_windows(ops, dets, sheet["height_pt"], 1.0, off)
    assert got == {"truth": 2, "pred": 2, "recall": 1.0, "precision": 1.0}
    widths = sorted(round(o["width_m"], 2) for o in res["openings"] if o.get("kind") == "window")
    assert widths == [1.0, 1.0]


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


def test_a_double_door_is_drawn_as_two_leaves_and_reads_back_as_found():
    from door_detect import detect_door_swings

    t = 0.2
    walls = [box(0, 0, 2.0, t), box(3.8, 0, 8, t), box(0, 0, t, 4), box(8 - t, 0, 8, 4)]
    door = box(2.0, 0, 3.8, t)  # 1.8 m, wider than one leaf door_detect reads
    img, to_px = door_sheet(walls, [door])
    dets = detect_door_swings(img, DOOR_PPM)
    assert match_doors([door], dets, to_px)["recall"] == 1.0
    assert sorted(round(d["width_px"] / DOOR_PPM, 1) for d in dets) == [0.9, 0.9]
