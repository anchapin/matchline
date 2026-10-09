"""Walls and rooms from vector plans (#740). Plans are drawn the way CAD
exports them: the outline of the wall mass as stroked lines (or solid poché),
door gaps with a leaf and swing, written to a PDF and read back through
pdf_ingest, so the whole vector path is exercised."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

sys.path.append(str(Path(__file__).parent))

from pdf_fixtures import PageSpec, curve, line, oc, text, write_pdf  # noqa: E402

import pdf_ingest as P  # noqa: E402
import plan_walls as W  # noqa: E402

M_PER_PT = 96 * 0.0254 / 72  # 1/8" = 1'-0"
K = 1 / M_PER_PT
OX, OY = 100.0, 100.0  # sheet offset, pt
PAGE_W, PAGE_H = 1728, 1152


def _pt(x, y):
    return OX + x * K, OY + y * K


def _mass(walls, doors=()):
    """Wall mass in metres: centreline walls (a, b, t) minus door openings."""
    m = unary_union(
        [
            LineString([a, b]).buffer(t / 2, cap_style="square", join_style="mitre")
            for a, b, t in walls
        ]
    )
    for (x, y), (dx, dy), width, t in doors:  # centre, wall direction, width, wall t
        hw, ht = width / 2, t
        corners = [
            (x - dx * hw - dy * ht, y - dy * hw + dx * ht),
            (x + dx * hw - dy * ht, y + dy * hw + dx * ht),
            (x + dx * hw + dy * ht, y + dy * hw - dx * ht),
            (x - dx * hw + dy * ht, y - dy * hw - dx * ht),
        ]
        m = m.difference(Polygon(corners))
    return m


def _outline(mass) -> str:
    out = ""
    polys = getattr(mass, "geoms", [mass])
    for poly in polys:
        for ring in [poly.exterior, *poly.interiors]:
            c = list(ring.coords)
            for a, b in zip(c, c[1:]):
                out += line(*_pt(*a), *_pt(*b), 0.5)
    return out


def _poche(walls) -> str:
    """Solid walls: each wall as its own filled rectangle, overlapping at corners."""
    from pdf_fixtures import rect

    out = ""
    for a, b, t in walls:
        x0, y0, x1, y1 = (
            LineString([a, b]).buffer(t / 2, cap_style="square", join_style="mitre").bounds
        )
        X0, Y0 = _pt(x0, y0)
        out += rect(X0, Y0, (x1 - x0) * K, (y1 - y0) * K, 0.0, fill=True)
    return out


def _door_symbol(hinge, leaf_to, swing_end):
    hx, hy = _pt(*hinge)
    lx, ly = _pt(*leaf_to)
    ex, ey = _pt(*swing_end)
    return line(hx, hy, lx, ly, 0.3) + curve(lx, ly, lx, ly, ex, ey, ex, ey)


def _read(tmp_path, content):
    pdf = write_pdf(tmp_path / "p.pdf", [PageSpec(width=PAGE_W, height=PAGE_H, content=content)])
    sheet = P.ingest_pdf(pdf, out_dir=tmp_path / "o", dpi=18).sheets[0]
    return W.extract_walls(sheet, M_PER_PT), tmp_path / "o"


T_EXT, T_INT = 0.30, 0.15
SHELL = [((0, 0), (10, 0), T_EXT), ((10, 0), (10, 6), T_EXT), ((10, 6), (0, 6), T_EXT),
         ((0, 6), (0, 0), T_EXT)]  # fmt: skip
PARTITION = [((4, 0), (4, 6), T_INT)]
DOOR = ((4, 3), (0, 1), 0.9, T_INT)  # in the partition, centred at (4, 3)


def _areas(res):
    return sorted(round(r.area_m2, 1) for r in res.rooms)


def test_two_rooms_and_a_door(tmp_path):
    mass = _mass(SHELL + PARTITION, [DOOR])
    res, _ = _read(tmp_path, _outline(mass) + _door_symbol((4, 2.55), (4.9, 2.55), (4, 3.45)))
    assert _areas(res) == [24.0, 36.0]  # centreline areas
    ths = sorted({w["thickness_m"] for w in res.walls})
    assert ths == pytest.approx([T_INT, T_EXT], abs=0.01)
    assert len(res.openings) == 1 and res.openings[0]["width_m"] == pytest.approx(0.9, abs=0.02)
    assert not [r for r in res.review if r["kind"] == "unclosed_wall"]
    # net areas sit inside the wall faces
    small = min(res.rooms, key=lambda r: r.area_m2)
    net = (4 - T_EXT / 2 - T_INT / 2) * (6 - T_EXT)
    assert small.net_area_m2 == pytest.approx(net, rel=0.01)


def test_metres_are_y_up(tmp_path):
    res, _ = _read(tmp_path, _outline(_mass(SHELL)))
    (room,) = res.rooms
    xs = [p[0] for p in room.polygon_m]
    ys = [p[1] for p in room.polygon_m]
    assert min(xs) == pytest.approx(OX * M_PER_PT, abs=0.01)
    assert min(ys) == pytest.approx(OY * M_PER_PT, abs=0.01)
    assert max(ys) - min(ys) == pytest.approx(6, abs=0.01)


def test_t_junctions_and_three_rooms(tmp_path):
    walls = SHELL + PARTITION + [((4, 3), (10, 3), T_INT)]  # T into the partition
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    assert _areas(res) == [18.0, 18.0, 24.0]
    assert res.openings == []  # the T gap in the partition face is a junction


def test_exterior_door_is_an_opening_not_an_open_room(tmp_path):
    door = ((7, 0), (1, 0), 1.0, T_EXT)
    res, _ = _read(tmp_path, _outline(_mass(SHELL + PARTITION, [door])))
    assert _areas(res) == [24.0, 36.0]
    assert [o["width_m"] for o in res.openings] == [pytest.approx(1.0, abs=0.02)]
    # the south wall is one run through the partition's T, broken only by the door
    south = [w for w in res.walls if w["thickness_m"] == pytest.approx(T_EXT, abs=0.01)]
    assert len(south) == 5
    assert res.stats["wall_length_m"] == pytest.approx(10 + 10 + 6 + 6 - 1.0 + 6, abs=0.05)


def test_window_glazing_line_stays_one_wall(tmp_path):
    # a window in the south wall: wall faces continue across, plus a glazing line
    gx0, gx1 = _pt(6, 0)[0], _pt(8, 0)[0]
    gy = _pt(0, 0)[1]
    glazing = line(gx0, gy, gx1, gy, 0.3)
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + glazing)
    assert _areas(res) == [60.0]
    assert all(w["thickness_m"] == pytest.approx(T_EXT, abs=0.01) for w in res.walls)


def test_solid_poche_walls(tmp_path):
    res, _ = _read(tmp_path, _poche(SHELL + PARTITION))
    assert _areas(res) == [24.0, 36.0]
    assert {w["source"] for w in res.walls} == {"filled"}


def test_unclosed_room_goes_to_review(tmp_path):
    walls = SHELL[:3] + [((0, 6), (0, 3.2), T_EXT)]  # west wall stops 3.2 m short
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    assert res.rooms == []
    assert any(r["kind"] == "unclosed_wall" for r in res.review)


def test_room_labels_attach(tmp_path):
    lx, ly = _pt(1.5, 3)
    rx, ry = _pt(6.5, 3)
    labels = text(lx, ly, "OFFICE", 8) + text(lx, ly - 10, "101", 8) + text(rx, ry, "LOBBY 102", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL + PARTITION, [DOOR])) + labels)
    got = {r.label["number"]: r.label["name"] for r in res.rooms if r.label}
    assert got == {"101": "OFFICE", "102": "LOBBY"}


def test_two_numbers_in_one_face_go_to_review(tmp_path):
    a, b = _pt(2, 3), _pt(7, 3)
    labels = text(*a, "101", 8) + text(*b, "102", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + labels)
    (room,) = res.rooms
    assert room.needs_review and "101, 102" in room.reasons[0]


def test_level_wing_numbers_count_as_room_numbers(tmp_path):
    a, b = _pt(2, 3), _pt(7, 3)
    labels = text(*a, "OFFICE 1A04", 8) + text(*b, "CORRIDOR 1AC5", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + labels)
    (room,) = res.rooms
    assert room.needs_review and "1A04, 1AC5" in room.reasons[0]


def test_one_level_wing_number_is_not_a_review(tmp_path):
    labels = text(*_pt(4, 3), "TRICARE OFFICE 1A04", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + labels)
    (room,) = res.rooms
    assert not room.needs_review


@pytest.mark.parametrize(
    "txt, want",
    [
        ("OFFICE 101", {"101"}),
        ("STAIR 1BS2", {"1BS2"}),
        ("CORRIDOR 1DC8 CLEAN U. 1D37", {"1DC8", "1D37"}),
        ("ROOM B12", {"B12"}),
        ("LOBBY", set()),
        ("WOOD 2X4 BLOCKING", set()),
    ],
)
def test_room_number_pattern(txt, want):
    assert set(W._ROOM_NO.findall(txt)) == want


def test_no_scale_refuses(tmp_path):
    pdf = write_pdf(
        tmp_path / "p.pdf", [PageSpec(width=PAGE_W, height=PAGE_H, content=_outline(_mass(SHELL)))]
    )
    sheet = P.ingest_pdf(pdf, dpi=18).sheets[0]
    res = W.extract_walls(sheet, None)
    assert res.rooms == [] and res.review[0]["kind"] == "no_scale"


def test_text_only_sheet_has_no_walls(tmp_path):
    res, _ = _read(tmp_path, text(100, 100, "GENERAL NOTES", 12))
    assert res.walls == [] and res.review[0]["kind"] == "no_walls"


def test_compare_rooms():
    truth = [box(0, 0, 4, 6).exterior.coords, box(4, 0, 10, 6).exterior.coords]
    pred = [box(0, 0, 4.1, 6).exterior.coords, box(0, 0, 0.5, 0.5).exterior.coords]
    c = W.compare_rooms([list(p) for p in pred], [list(t) for t in truth])
    assert (c["matched"], c["missed"], c["extra"]) == (1, 1, 1)
    assert c["area_err_max"] == pytest.approx(0.025, abs=1e-3)
    assert W.wall_length_error(52.0, 50.0) == pytest.approx(0.04)


def test_walls_for_sheets_uses_scale_and_index(tmp_path):
    content = _outline(_mass(SHELL + PARTITION, [DOOR]))
    pdf = write_pdf(
        tmp_path / "s.pdf",
        [PageSpec(width=PAGE_W, height=PAGE_H, content=content)] * 2,
    )
    out = tmp_path / "o"
    P.ingest_pdf(pdf, out_dir=out, dpi=18)
    (out / "scale.json").write_text(
        json.dumps(
            {"sheet_001.json": {"m_per_pt": M_PER_PT}, "sheet_002.json": {"m_per_pt": M_PER_PT}}
        )
    )
    (out / "sheet_index.json").write_text(
        json.dumps({"sheets": [{"file": "sheet_001.json", "use_for_takeoff": True},
                               {"file": "sheet_002.json", "use_for_takeoff": False}]})
    )  # fmt: skip
    res = W.walls_for_sheets(out)
    assert list(res) == ["sheet_001.json"]
    data = json.loads((out / "walls_001.json").read_text())
    assert data["schema"] == "matchline.plan_walls/1" and len(data["rooms"]) == 2
    assert not (out / "walls_002.json").exists()


def test_cli_walls(tmp_path, capsys):
    pdf = write_pdf(
        tmp_path / "s.pdf",
        [PageSpec(width=PAGE_W, height=PAGE_H, content=_outline(_mass(SHELL + PARTITION, [DOOR])))],
    )
    out = tmp_path / "o"
    P.ingest_pdf(pdf, out_dir=out, dpi=18)
    (out / "scale.json").write_text(json.dumps({"sheet_001.json": {"m_per_pt": M_PER_PT}}))
    from cli import main

    main(["walls", str(out)])
    printed = capsys.readouterr().out
    assert "sheet_001.json  walls" in printed and "rooms 2" in printed


# ---- review fixes


def test_near_horizontal_faces_either_side_of_the_wrap(tmp_path):
    # CAD float noise: one face of the south wall tilts a hair below 0 deg, the
    # other a hair above, so their directions sit either side of 0/pi
    mass = _mass(SHELL)
    out = ""
    for poly in getattr(mass, "geoms", [mass]):
        for ring in [poly.exterior, *poly.interiors]:
            c = list(ring.coords)
            for a, b in zip(c, c[1:]):
                (x0, y0), (x1, y1) = _pt(*a), _pt(*b)
                if abs(y0 - y1) < 1e-6 and abs(y0 - _pt(0, T_EXT / 2)[1]) < 1e-3:
                    y1 += 0.004 if x1 > x0 else -0.004
                out += line(x0, y0, x1, y1, 0.5)
    res, _ = _read(tmp_path, out)
    assert _areas(res) == [60.0]
    assert len(res.walls) == 4


def test_stair_treads_are_not_walls(tmp_path):
    treads = ""
    for k in range(12):  # 0.28 m going, 1.2 m wide, inside the big room
        x0, y = _pt(5.5, 1.0 + 0.28 * k)
        x1, _ = _pt(6.7, 0)
        treads += line(x0, y, x1, y, 0.3)
    res, _ = _read(tmp_path, _outline(_mass(SHELL + PARTITION, [DOOR])) + treads)
    assert _areas(res) == [24.0, 36.0]
    assert len(res.walls) == 6  # shell 4 + partition either side of the door


def test_chase_between_two_walls_stays_two_walls(tmp_path):
    # two 0.15 m walls with a 0.3 m chase between: the chase is not a 0.6 m wall
    walls = SHELL + [((4, 0), (4, 6), T_INT), ((4.45, 0), (4.45, 6), T_INT)]
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    vert = [w for w in res.walls if abs(w["a_m"][0] - w["b_m"][0]) < 1e-3]
    assert sorted(w["thickness_m"] for w in vert) == pytest.approx([0.15, 0.15, 0.3, 0.3], abs=0.01)
    assert _areas(res) == [2.7, 24.0, 33.3]
    sliver = min(res.rooms, key=lambda r: r.area_m2)
    assert sliver.area_m2 > 1  # the chase face is a real face; classifying it is #740 follow-up


def test_room_number_inside_a_name_span_counts(tmp_path):
    a, b = _pt(2, 3), _pt(7, 3)
    labels = text(*a, "OFFICE 101", 8) + text(*b, "STORAGE 102", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + labels)
    (room,) = res.rooms
    assert room.needs_review and "101, 102" in room.reasons[0]


def test_column_inside_a_room_is_a_hole(tmp_path):
    col = [((6.8, 3), (7.2, 3), 0.4)]  # 0.4 m square column, free-standing
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _poche(col))
    big = max(res.rooms, key=lambda r: r.area_m2)
    assert big.area_m2 == pytest.approx(60.0, abs=0.1)
    assert len(res.rooms) == 1


def test_grid_of_rooms_scales(tmp_path):
    import time

    n = 12  # 144 rooms, 26 wall lines of 36 m
    size = 3.0
    span = n * size
    walls = [((i * size, 0), (i * size, span), T_INT) for i in range(n + 1)]
    walls += [((0, j * size), (span, j * size), T_INT) for j in range(n + 1)]
    t0 = time.perf_counter()
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    assert len(res.rooms) == n * n
    assert {round(r.area_m2, 2) for r in res.rooms} == {9.0}
    assert time.perf_counter() - t0 < 30


def test_wall_that_changes_thickness_still_closes_the_room(tmp_path):
    # south wall steps from 0.30 m to 0.15 m halfway, inside faces flush (#740)
    walls = [
        ((0, 0), (5, 0), T_EXT),
        ((5, 0.075), (10, 0.075), T_INT),
        ((10, 0), (10, 6), T_EXT),
        ((10, 6), (0, 6), T_EXT),
        ((0, 6), (0, 0), T_EXT),
    ]
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    assert len(res.rooms) == 1
    assert not [r for r in res.review if r["kind"] == "unclosed_wall"]


def test_free_end_joins_a_wall_it_stops_against():
    # partition (t 0.15) stops 0.20 short of a 0.30 wall's centreline: within half of both
    pairs = [((0.0, 0.0), (10.0, 0.0)), ((4.0, 0.2), (4.0, 5.0))]
    joins, corners = W._join_free_ends(pairs, [0.30, 0.15], tol=0.01)
    assert ((4.0, 0.2), (4.0, 0.0)) in joins and corners == []
    # 0.60 short is farther than both thicknesses: no join from that end
    far, _ = W._join_free_ends([pairs[0], ((4.0, 0.6), (4.0, 5.0))], [0.30, 0.15], tol=0.01)
    assert all(a != (4.0, 0.6) for a, _ in far)


def test_door_beside_a_corner_closes_the_room(tmp_path):
    # the partition stops 1.0 m short of the south wall: a door whose far side is a
    # crossing wall, not a collinear one (#740)
    walls = SHELL + [((4, 6), (4, 1), T_INT)]
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    assert _areas(res) == [24.0, 36.0]
    corner = [o for o in res.openings if o.get("beside_corner")]
    assert len(corner) == 1 and corner[0]["width_m"] == pytest.approx(1.0, abs=0.05)


def test_unlabeled_sliver_merges_by_the_closet_and_shaft_rule():
    # a wall-jog sliver (1.5 units^2) between two rooms is folded in, never dropped (#740)
    a, b, s = box(0, 0, 10, 10), box(10, 0, 20, 10), box(9, 10, 10.5, 11)
    # shaft rule: most shared boundary (1.0 with a, 0.5 with b)
    faces, merged = W._merge_slivers([a, b, s], [], [], max_area=2.0, tol=0.01)
    assert len(faces) == 2 and merged[0] == [1.5] and merged[1] == []
    assert sum(f.area for f in faces) == pytest.approx(201.5)
    # closet rule: a door on the shared edge wins over the longer edge
    door = LineString([(10, 10), (10.5, 10)])
    faces, merged = W._merge_slivers([a, b, s], [], [door], max_area=2.0, tol=0.01)
    assert merged == [[], [1.5]]
    # a labeled face stays a room however small
    spans = [("CLOSET 105", (9.4, 10.3, 10.1, 10.7))]
    faces, merged = W._merge_slivers([a, b, s], spans, [], max_area=2.0, tol=0.01)
    assert len(faces) == 3 and merged == [[], [], []]


def test_wide_gap_in_a_wall_line_closes_with_an_air_wall(tmp_path):
    # a 3.5 m storefront or open edge: wider than a door, still one wall line
    res, _ = _read(tmp_path, _outline(_mass(SHELL, [((5, 0), (1, 0), 3.5, T_EXT)])))
    assert _areas(res) == [60.0]
    assert [o.get("air_wall") for o in res.openings] == [True]
    assert [i["kind"] for i in res.review if i["kind"] == "air_wall"] == ["air_wall"]
    # wider than WIDE_OPENING_M stays open and goes to review as before
    res, _ = _read(tmp_path, _outline(_mass(SHELL, [((5, 0), (1, 0), 5.0, T_EXT)])))
    assert _areas(res) == []
    assert any(i["kind"] == "unclosed_wall" for i in res.review)


# ---- door swings on the plan mark the gap as a door (#743) ---------------


def _door_ops(res):
    return [o for o in res.openings if o.get("kind") == "door"]


def test_single_door_swing_marks_the_gap_a_door(tmp_path):
    mass = _mass(SHELL + PARTITION, [DOOR])
    res, _ = _read(tmp_path, _outline(mass) + _door_symbol((4, 2.55), (4.9, 2.55), (4, 3.45)))
    (op,) = _door_ops(res)
    assert op["swing"] == "single" and op["door_confidence"] == 0.85
    (hinge,) = op["hinges_m"]
    assert min(math.dist(hinge, op["a_m"]), math.dist(hinge, op["b_m"])) < 1e-3
    assert res.stats["doors"] == 1


def test_double_door_swings_mark_a_pair(tmp_path):
    pair = ((4, 3), (0, 1), 1.6, T_INT)  # 1.6 m gap: two 0.8 m leaves
    mass = _mass(SHELL + PARTITION, [pair])
    sym = _door_symbol((4, 2.2), (4.8, 2.2), (4, 3.0)) + _door_symbol(
        (4, 3.8), (4.8, 3.8), (4, 3.0)
    )
    res, _ = _read(tmp_path, _outline(mass) + sym)
    (op,) = _door_ops(res)
    assert op["swing"] == "double" and len(op["hinges_m"]) == 2


def test_gap_without_a_swing_is_not_called_a_door(tmp_path):
    mass = _mass(SHELL + PARTITION, [DOOR])
    res, _ = _read(tmp_path, _outline(mass))
    assert len(res.openings) == 1 and "kind" not in res.openings[0]
    assert res.stats["doors"] == 0


def test_curve_of_the_wrong_radius_is_not_a_swing(tmp_path):
    mass = _mass(SHELL + PARTITION, [DOOR])
    # a 0.45 m curve hinged on the jamb: half the gap, and no second leaf
    res, _ = _read(tmp_path, _outline(mass) + _door_symbol((4, 2.55), (4.45, 2.55), (4, 3.0)))
    assert not _door_ops(res)


# ---- glazing lines inside a wall make window openings (#743) -------------


def _windows(res):
    return [o for o in res.openings if o.get("kind") == "window"]


def _glazing(x0, x1, y=0.0):
    (gx0, gy), (gx1, _) = _pt(x0, y), _pt(x1, y)
    return line(gx0, gy, gx1, gy, 0.3)


def test_glazing_line_in_a_wall_is_a_window(tmp_path):
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _glazing(6, 8))
    (op,) = _windows(res)
    assert op["width_m"] == pytest.approx(2.0, abs=0.02)
    assert op["source"] == "glazing_line" and op["window_confidence"] == 0.75
    xs = sorted((op["a_m"][0], op["b_m"][0]))
    assert xs[1] - xs[0] == pytest.approx(2.0, abs=0.02)
    assert res.stats["windows"] == 1
    assert _areas(res) == [60.0]  # the wall still closes the room


def test_sill_lines_across_a_gap_make_one_window_not_a_gap(tmp_path):
    win = ((7, 0), (1, 0), 1.2, T_EXT)
    mass = _mass(SHELL, [win])
    h = T_EXT / 2
    sills = _glazing(6.4, 7.6, -h) + _glazing(6.4, 7.6, 0) + _glazing(6.4, 7.6, h)
    res, _ = _read(tmp_path, _outline(mass) + sills)
    (op,) = _windows(res)
    assert op["width_m"] == pytest.approx(1.2, abs=0.03)
    assert not [o for o in res.openings if "kind" not in o]  # the gap closed into the window


def test_middle_line_along_the_whole_wall_is_not_a_window(tmp_path):
    # a cavity or insulation line the length of the south wall
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _glazing(0, 10))
    assert not _windows(res)


def _returns(xs, depth=0.30, t=0.05):
    """Thin reveal returns meeting the south wall's inside face at ``xs``."""
    y0 = T_EXT / 2
    return [((x, y0), (x, y0 + depth), t) for x in xs]


def test_reveal_returns_at_both_jambs_keep_the_window(tmp_path):
    # #743: Clinic first floor, a window with a return just past each jamb
    res, _ = _read(tmp_path, _outline(_mass(SHELL + _returns([3.85, 5.15]))) + _glazing(4, 5))
    (op,) = _windows(res)
    assert op["width_m"] == pytest.approx(1.0, abs=0.02)


def test_reveal_return_inside_a_jamb_keeps_the_window(tmp_path):
    # the return meets the face 0.08 m inside the glazing, cutting one half short
    res, _ = _read(tmp_path, _outline(_mass(SHELL + _returns([4.92]))) + _glazing(4, 5))
    (op,) = _windows(res)
    assert op["width_m"] == pytest.approx(1.0, abs=0.02)


def test_cavity_line_past_reveal_returns_is_not_a_window(tmp_path):
    # returns along a wall do not turn its full-length cavity line into glazing
    mass = _mass(SHELL + _returns([3, 6]))
    res, _ = _read(tmp_path, _outline(mass) + _glazing(0, 10))
    assert not _windows(res)


def test_middle_line_continues_only_end_to_end_on_the_same_line():
    g = W.Band(0.0, 10.0, 0.0, 50.0, 5.0, "line_pair", (), True)

    def other(u0, u1, rho=10.0, glazed=True):
        return W.Band(0.0, rho, u0, u1, 5.0, "line_pair", (), glazed)

    assert W._middle_line_continues(g, [g, other(52, 90)], 1.0)  # carries on: cavity
    assert W._middle_line_continues(g, [g, other(-40, -3)], 1.0)
    assert not W._middle_line_continues(g, [g], 1.0)  # lone band: glazing
    assert not W._middle_line_continues(g, [g, other(70, 90)], 1.0)  # too far
    assert not W._middle_line_continues(g, [g, other(52, 90, rho=14.0)], 1.0)  # other line
    assert not W._middle_line_continues(g, [g, other(52, 90, glazed=False)], 1.0)


def _mullion_ticks(xs, y=0.0, t=T_EXT):
    out = ""
    for x in xs:
        out += line(*_pt(x, y - t / 2), *_pt(x, y + t / 2), 0.3)
    return out


def test_full_length_storefront_with_mullions_is_a_window(tmp_path):
    # #793: glazing the whole length of the south wall, broken by mullions
    res, _ = _read(
        tmp_path, _outline(_mass(SHELL)) + _glazing(0, 10) + _mullion_ticks([2, 4, 6, 8])
    )
    (op,) = _windows(res)
    assert op["source"] == "glazing_mullions"
    assert op["window_confidence"] == W.STOREFRONT_CONFIDENCE < W.WINDOW_CONFIDENCE
    assert op["width_m"] > W.WIDE_OPENING_M  # wider than a punched window is fine here
    assert len(op["walls"]) == 1
    assert _areas(res) == [60.0]  # the wall still closes the room


def test_one_tick_does_not_make_a_cavity_line_a_storefront(tmp_path):
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _glazing(0, 10) + _mullion_ticks([5]))
    assert not _windows(res)


def test_ticks_at_the_wall_ends_are_not_mullions(tmp_path):
    # ticks at the corners (frame or a return), none inside the run
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _glazing(0, 10) + _mullion_ticks([0.1, 9.9]))
    assert not _windows(res)


def test_wide_glazing_without_mullions_is_still_not_a_window(tmp_path):
    # 6 m of middle line in a 10 m wall: wider than any punched window
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _glazing(2, 8))
    assert not _windows(res)
    (tmp_path / "m").mkdir()
    res, _ = _read(tmp_path / "m", _outline(_mass(SHELL)) + _glazing(2, 8) + _mullion_ticks([4, 6]))
    (op,) = _windows(res)
    assert op["source"] == "glazing_mullions" and op["width_m"] == pytest.approx(6.0, abs=0.05)


# ---- door jambs inside full-length glazing are not mullions (#793) --------

# a 0.9 m door in the south wall drawn without a gap: jamb ticks at x=4.55
# and x=5.45, hinged on the first jamb, the swing closing on the second
_JAMBS = [4.55, 5.45]
_SWING = _door_symbol((4.55, 0), (4.55, 0.9), (5.45, 0))


def test_door_jambs_in_a_cavity_line_are_not_mullions(tmp_path):
    body = _outline(_mass(SHELL)) + _glazing(0, 10) + _mullion_ticks(_JAMBS) + _SWING
    res, _ = _read(tmp_path, body)
    assert not _windows(res)  # was a false 9.7 m storefront


def test_storefront_window_keeps_its_run_and_lists_the_door(tmp_path):
    body = _outline(_mass(SHELL)) + _glazing(0, 10) + _mullion_ticks([2, *_JAMBS, 8]) + _SWING
    res, _ = _read(tmp_path, body)
    (op,) = _windows(res)
    # the whole run, as before: whether the schedule width includes the door
    # is for the tag match downstream to decide
    assert op["source"] == "glazing_mullions" and op["width_m"] == pytest.approx(9.7, abs=0.05)
    (door,) = op["doors_in_glazing"]
    assert door["width_m"] == pytest.approx(0.9, abs=0.03)
    x0 = OX * M_PER_PT  # sheet offset in metres
    xs = sorted((door["a_m"][0] - x0, door["b_m"][0] - x0))
    assert xs == [pytest.approx(4.55, abs=0.03), pytest.approx(5.45, abs=0.03)]


def test_double_door_jambs_in_a_storefront_are_one_door(tmp_path):
    pair = _door_symbol((4.2, 0), (4.2, 0.8), (5.0, 0)) + _door_symbol(
        (5.8, 0), (5.8, 0.8), (5.0, 0)
    )
    jambs = _mullion_ticks([4.2, 5.8])
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _glazing(0, 10) + jambs + pair)
    assert not _windows(res)  # a cavity line with a pair of doors is not glass
    (tmp_path / "s").mkdir()
    body = _outline(_mass(SHELL)) + _glazing(0, 10) + _mullion_ticks([2, 8]) + jambs + pair
    res, _ = _read(tmp_path / "s", body)
    (op,) = _windows(res)
    (door,) = op["doors_in_glazing"]
    assert door["width_m"] == pytest.approx(1.6, abs=0.03)


def test_jamb_like_ticks_without_a_swing_are_still_mullions(tmp_path):
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + _glazing(0, 10) + _mullion_ticks(_JAMBS))
    (op,) = _windows(res)
    assert op["source"] == "glazing_mullions"


def test_door_jambs_on_a_glazing_layer_are_listed_on_the_window(tmp_path):
    body = _outline(_mass(SHELL)) + oc(0, _glazing(0, 10)) + _mullion_ticks(_JAMBS) + _SWING
    res = _read_layers(tmp_path, body, ["A-GLAZ"])
    (op,) = _windows(res)
    assert op["source"] == "glazing_layer" and len(op["doors_in_glazing"]) == 1


# ---- CAD layers decide full-length glazing when the PDF keeps them (#793) --


def _read_layers(tmp_path, content, layers):
    spec = PageSpec(width=PAGE_W, height=PAGE_H, content=content, layers=layers)
    pdf = write_pdf(tmp_path / "p.pdf", [spec])
    sheet = P.ingest_pdf(pdf, out_dir=tmp_path / "o", dpi=18).sheets[0]
    return W.extract_walls(sheet, M_PER_PT)


def test_full_length_glazing_on_a_glazing_layer_is_a_window(tmp_path):
    # storefront the whole south wall, no mullion ticks drawn: the layer says glass
    res = _read_layers(tmp_path, _outline(_mass(SHELL)) + oc(0, _glazing(0, 10)), ["A-GLAZ"])
    (op,) = _windows(res)
    assert op["source"] == "glazing_layer"
    assert op["window_confidence"] == W.LAYER_GLAZING_CONFIDENCE
    assert op["width_m"] > W.WIDE_OPENING_M
    assert res.stats["layers"] == ["A-GLAZ"]
    assert _areas(res) == [60.0]


def test_storefront_layer_names_count_as_glazing():
    for name in ("A-GLAZ", "A-WALL-GLAZ", "A-STOREFRONT", "A-CURT-WALL", "A-WIND", "Windows"):
        assert W._layer_kind(name) == "glazing", name
    for name in ("A-WALL-PATT", "A-GLAZ-PATT", "INSULATION", "A-WALL-HATCH"):
        assert W._layer_kind(name) == "pattern", name
    for name in ("A-WALL", "A-DOOR", "0", "", None, "WINDING-STAIR"):
        assert W._layer_kind(name) is None, name


def test_middle_line_on_a_pattern_layer_is_never_a_window(tmp_path):
    # insulation line with ticks that would otherwise read as mullions
    content = _outline(_mass(SHELL)) + oc(0, _glazing(0, 10)) + _mullion_ticks([2, 4, 6, 8])
    res = _read_layers(tmp_path, content, ["A-WALL-PATT"])
    assert not _windows(res)
    (tmp_path / "s").mkdir()
    res = _read_layers(tmp_path / "s", _outline(_mass(SHELL)) + oc(0, _glazing(6, 8)), ["INSUL"])
    assert not _windows(res)  # short pieces too: the layer says it is not glass


def test_unrelated_layers_leave_the_geometry_rules_in_charge(tmp_path):
    # walls on A-WALL, glazing line on an unnamed layer: no layer evidence
    content = oc(0, _outline(_mass(SHELL))) + oc(1, _glazing(0, 10))
    res = _read_layers(tmp_path, content, ["A-WALL", "0"])
    assert not _windows(res)  # full-length line without ticks stays a cavity line
    (tmp_path / "w").mkdir()
    content = oc(0, _outline(_mass(SHELL))) + oc(1, _glazing(6, 8))
    (op,) = _windows(_read_layers(tmp_path / "w", content, ["A-WALL", "0"]))
    assert op["source"] == "glazing_line"


def test_wall_faces_on_a_layer_do_not_judge_the_middle_line(tmp_path):
    # wall outline on a hatch layer, plain punched window line: still a window
    content = oc(0, _outline(_mass(SHELL))) + _glazing(6, 8)
    (op,) = _windows(_read_layers(tmp_path, content, ["A-WALL-PATT"]))
    assert op["source"] == "glazing_line"
    # wall outline on a glazing layer, full-length middle line on no layer: no evidence
    (tmp_path / "g").mkdir()
    content = oc(0, _outline(_mass(SHELL))) + _glazing(0, 10)
    assert not _windows(_read_layers(tmp_path / "g", content, ["A-GLAZ"]))


def _split_outline(mass, on_layer):
    """Outline edges split in two: those ``on_layer(a, b)`` picks, and the rest."""
    picked = rest = ""
    for poly in getattr(mass, "geoms", [mass]):
        for ring in [poly.exterior, *poly.interiors]:
            c = list(ring.coords)
            for a, b in zip(c, c[1:]):
                seg = line(*_pt(*a), *_pt(*b), 0.5)
                if on_layer(a, b):
                    picked += seg
                else:
                    rest += seg
    return picked, rest


def _band_faces(x0, x1):
    def pick(a, b):
        return all(x0 - 1e-6 <= p[0] <= x1 + 1e-6 and abs(p[1]) < T_EXT / 2 - 1e-6 for p in (a, b))

    return pick


def test_thin_band_on_a_glazing_layer_is_a_window(tmp_path):
    glass, rest = _split_outline(_mass(_thin_bay(3, 7)), _band_faces(3, 7))
    assert glass  # the band's faces really are split out
    res = _read_layers(tmp_path, rest + oc(0, glass), ["A-GLAZ"])
    (op,) = _windows(res)
    assert op["source"] == "glazing_layer_band"
    assert op["window_confidence"] == W.LAYER_GLAZING_CONFIDENCE
    assert op["width_m"] == pytest.approx(3.7, abs=0.05)
    (w,) = [w for w in res.walls if w["id"] in op["walls"]]
    assert w["thickness_m"] == pytest.approx(T_GLZ, abs=0.01) and not w.get("maybe_glazing")
    assert not [r for r in res.review if r["kind"] == "maybe_glazing"]
    assert res.stats["windows"] == 1 and res.stats["maybe_glazing"] == 0
    assert _areas(res) == [60.0]  # the band still closes the room


def test_tag_beside_a_glazing_layer_band_labels_its_window_not_the_wall(tmp_path):
    glass, rest = _split_outline(_mass(_thin_bay(3, 7)), _band_faces(3, 7))
    tag = text(*_pt(5, -0.8), "SF-1", 6)
    res = _read_layers(tmp_path, rest + oc(0, glass) + tag, ["A-GLAZ"])
    (op,) = _windows(res)
    assert op.get("tag_text") == "SF-1"
    assert not res.wall_tags  # not also kept against the band as a wall tag


def test_thin_band_on_other_layers_stays_a_question(tmp_path):
    glass, rest = _split_outline(_mass(_thin_bay(3, 7)), _band_faces(3, 7))
    res = _read_layers(tmp_path, rest + oc(0, glass), ["A-WALL"])
    assert not _windows(res) and res.stats["maybe_glazing"] == 1
    (tmp_path / "p").mkdir()
    res = _read_layers(tmp_path / "p", rest + oc(0, glass), ["A-WALL-PATT"])
    assert not _windows(res) and res.stats["maybe_glazing"] == 1


def test_glazing_layer_band_rules():
    k = 10.0
    band = _w(2, (30, 0), (70, 0), 1.2)
    tol = 0.2

    def seg(x0, x1, y, layer):
        return W.Seg((x0, y), (x1, y), 0, layer)

    faces = [seg(30, 70, 0.6, "A-GLAZ"), seg(30, 70, -0.6, "A-GLAZ")]
    assert W._glazing_layer_band(band, faces, tol)
    # glazing covers only half the band: not enough
    assert not W._glazing_layer_band(band, [seg(30, 50, 0.6, "A-GLAZ")], tol)
    # two pieces that together cover it count
    assert W._glazing_layer_band(
        band, [seg(30, 52, 0.6, "A-GLAZ"), seg(50, 70, -0.6, "A-GLAZ")], tol
    )
    # a hatch line on the band vetoes it
    assert not W._glazing_layer_band(band, faces + [seg(30, 70, 0.0, "A-WALL-PATT")], tol)
    # glazing lines off to the side (another wall) say nothing about this band
    assert not W._glazing_layer_band(band, [seg(30, 70, 0.6 + 2 * k, "A-GLAZ")], tol)
    # perpendicular lines say nothing either
    assert not W._glazing_layer_band(band, [W.Seg((40, -0.6), (40, 0.6), 0, "A-GLAZ")], tol)


def test_door_and_window_on_one_plan(tmp_path):
    mass = _mass(SHELL + PARTITION, [DOOR])
    sym = _door_symbol((4, 2.55), (4.9, 2.55), (4, 3.45))
    res, _ = _read(tmp_path, _outline(mass) + sym + _glazing(6, 8))
    assert sorted(o.get("kind") for o in res.openings) == ["door", "window"]


# ---- #793: a schedule tag on a wall with no opening drawn ------------------


def test_tag_on_a_plain_wall_is_kept_against_the_wall(tmp_path):
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + text(*_pt(5, -0.6), "SF-1", 8))
    (wt,) = res.wall_tags
    assert wt["tag_text"] == "SF-1" and wt["tag_dist_m"] == pytest.approx(0.6, abs=0.1)
    south = next(w for w in res.walls if w["id"] == wt["wall"])
    assert south["a_m"][1] == pytest.approx(south["b_m"][1], abs=0.01)  # the south wall
    assert south["length_m"] == pytest.approx(10.0, abs=0.35)
    assert wt["point_m"][1] < min(w["a_m"][1] for w in res.walls) + 0.01  # tag below it
    assert res.stats["wall_tags"] == 1 and res.to_dict()["wall_tags"] == res.wall_tags


def test_tag_beside_an_opening_is_not_a_wall_tag(tmp_path):
    win = ((7, 0), (1, 0), 1.2, T_EXT)
    res, _ = _read(tmp_path, _outline(_mass(SHELL, [win])) + text(*_pt(7, -0.6), "W1", 8))
    assert not res.wall_tags
    (op,) = res.openings
    assert op["tag_text"] == "W1"


def test_text_that_is_not_a_tag_or_far_from_walls_is_ignored(tmp_path):
    body = text(*_pt(5, -0.6), "OFFICE", 8) + text(*_pt(5, 3.0), "SF-1", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL)) + body)
    assert not res.wall_tags


def test_opening_keeps_every_tag_nearest_first():
    import plan_walls as PW

    ops = [{"a_m": (0.0, 0.0), "b_m": (1.2, 0.0)}, {"a_m": (5.0, 0.0), "b_m": (6.0, 0.0)}]
    spans = [
        ("W3", (0.5, -1.0, 0.7, -1.0)),
        ("EW1", (0.6, -0.4, 0.8, -0.4)),
        ("D1", (5.5, 0.3, 5.6, 0.3)),
    ]
    assert PW._attach_tags(ops, spans, lambda p: p) == 2
    assert ops[0]["tag_text"] == "EW1"
    assert [t["tag"] for t in ops[0]["tags_near"]] == ["EW1", "W3"]
    assert [t["tag"] for t in ops[1]["tags_near"]] == ["D1"]


# ---- a wall that stops short of the wall ahead (#740 open plan) ----------

HALF_PARTITION = [((4, 0), (4, 3), T_INT)]  # stops 3 m short of the far wall


def test_wall_stopping_short_between_two_labelled_rooms_closes_with_an_air_wall(tmp_path):
    labels = text(*_pt(2, 3), "OFFICE 101", 8) + text(*_pt(7, 3), "WAITING 102", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL + HALF_PARTITION)) + labels)
    assert _areas(res) == [24.0, 36.0]
    air = [o for o in res.openings if o.get("air_wall")]
    assert len(air) == 1 and air[0]["beside_corner"]
    assert air[0]["width_m"] == pytest.approx(3.0, abs=0.2)
    assert [i["kind"] for i in res.review if i["kind"] == "air_wall"] == ["air_wall"]
    assert not [i for i in res.review if i["kind"] == "unclosed_wall"]


def test_wall_stopping_short_inside_one_labelled_room_stays_open(tmp_path):
    # a counter or half wall inside one named space: no second label, no split
    labels = text(*_pt(7, 3), "WAITING 102", 8)
    res, _ = _read(tmp_path, _outline(_mass(SHELL + HALF_PARTITION)) + labels)
    assert _areas(res) == [60.0]
    assert not [o for o in res.openings if o.get("air_wall")]
    assert any(i["kind"] == "unclosed_wall" for i in res.review)


def test_wall_stopping_beyond_the_air_wall_limit_stays_open(tmp_path):
    # 5 m short of the far wall: wider than WIDE_OPENING_M, so no closure at all
    shell = [((0, 0), (10, 0), T_EXT), ((10, 0), (10, 8), T_EXT), ((10, 8), (0, 8), T_EXT),
             ((0, 8), (0, 0), T_EXT)]  # fmt: skip
    labels = text(*_pt(2, 4), "OFFICE 101", 8) + text(*_pt(7, 4), "WAITING 102", 8)
    res, _ = _read(tmp_path, _outline(_mass(shell + HALF_PARTITION)) + labels)
    assert _areas(res) == [80.0]
    assert not [o for o in res.openings if o.get("air_wall")]


# ---- #793: storefront drawn as a thin wall band of its own -----------------

T_GLZ = 0.12


def _thin_bay(x0, x1):
    return [
        ((0, 0), (x0, 0), T_EXT),
        ((x0, 0), (x1, 0), T_GLZ),
        ((x1, 0), (10, 0), T_EXT),
    ] + SHELL[1:]


def test_thin_band_between_thicker_walls_may_be_glazing(tmp_path):
    res, _ = _read(tmp_path, _outline(_mass(_thin_bay(3, 7))))
    (w,) = [w for w in res.walls if w.get("maybe_glazing")]
    assert w["thickness_m"] == pytest.approx(T_GLZ, abs=0.01)
    assert w["length_m"] == pytest.approx(3.7, abs=0.05)  # between the thick wall faces
    (rv,) = [r for r in res.review if r["kind"] == "maybe_glazing"]
    assert rv["wall"] == w["id"] and "storefront glazing" in rv["reason"]
    assert res.stats["maybe_glazing"] == 1
    assert not _windows(res)  # flagged only, never modelled from the band alone
    assert _areas(res) == [60.0]  # the band still closes the room


def test_thin_band_running_into_a_corner_is_not_flagged(tmp_path):
    # thick wall on one side only: a change of wall type, not a glazed bay
    walls = [((0, 0), (4, 0), T_EXT), ((4, 0), (10, 0), T_GLZ)] + SHELL[1:]
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    assert not [w for w in res.walls if w.get("maybe_glazing")]
    assert res.stats["maybe_glazing"] == 0


def test_plain_shell_and_partitions_are_not_flagged(tmp_path):
    res, _ = _read(tmp_path, _outline(_mass(SHELL + PARTITION, [DOOR])))
    assert res.stats["maybe_glazing"] == 0
    assert not [r for r in res.review if r["kind"] == "maybe_glazing"]


def _w(i, a, b, t):
    return W.Wall(f"W{i}", a, b, t, "line_pair")


def test_thin_band_rules():
    k = 10.0  # pt per metre for this unit test
    left, right = _w(1, (0, 0), (30, 0), 3.0), _w(3, (70, 0), (100, 0), 3.0)
    thin = _w(2, (30, 0), (70, 0), 1.2)
    assert W._thin_bands([left, thin, right], [], 0.5, 1.0 * k) == ["W2"]
    # an opening already on it
    assert W._thin_bands([left, thin, right], [{"walls": ["W2"]}], 0.5, 1.0 * k) == []
    # too short to be a glazed bay
    short = [
        _w(1, (0, 0), (30, 0), 3.0),
        _w(2, (30, 0), (38, 0), 1.2),
        _w(3, (38, 0), (60, 0), 3.0),
    ]
    assert W._thin_bands(short, [], 0.5, 1.0 * k) == []
    # neighbours barely thicker: one wall type with drafting noise
    same = [left, _w(2, (30, 0), (70, 0), 2.5), right]
    assert W._thin_bands(same, [], 0.5, 1.0 * k) == []
    # glazing on the outer face of the thick wall still counts; a parallel wall
    # a room's depth away does not
    face = _w(2, (30, 1.2), (70, 1.2), 1.2)
    assert W._thin_bands([left, face, right], [], 0.5, 1.0 * k) == ["W2"]
    far = _w(2, (30, 40), (70, 40), 1.2)
    assert W._thin_bands([left, far, right], [], 0.5, 1.0 * k) == []
    # a perpendicular wall at the end is a corner, not a neighbour on the line
    corner = [left, thin, _w(3, (70, 0), (70, 50), 3.0)]
    assert W._thin_bands(corner, [], 0.5, 1.0 * k) == []
    # a thick wall overlapping the thin one is not meeting it end to end
    lap = [_w(1, (0, 0), (50, 0), 3.0), thin, right]
    assert W._thin_bands(lap, [], 0.5, 1.0 * k) == []


def test_long_neighbour_at_a_slight_angle_still_meets_the_band():
    # 300 pt neighbour 0.9 deg off the band's line: its far end drifts ~4.7 pt,
    # more than the reach, but it meets the band exactly at the joint
    import math as _m

    k = 10.0
    dy = 300 * _m.tan(_m.radians(0.9))
    left = _w(1, (-300, -dy), (0, 0), 3.0)
    thin, right = _w(2, (0, 0), (40, 0), 1.2), _w(3, (40, 0), (70, 0), 3.0)
    assert W._thin_bands([left, thin, right], [], 0.5, 1.0 * k) == ["W2"]


def test_short_thin_bay_stops_the_thick_run(tmp_path):
    # a 2 m bay is narrower than MAX_OPENING_M: the thick run used to carry
    # straight through it (one 10 m wall), so the band was never flagged
    res, _ = _read(tmp_path, _outline(_mass(_thin_bay(4, 6))))
    (w,) = [w for w in res.walls if w.get("maybe_glazing")]
    assert w["thickness_m"] == pytest.approx(T_GLZ, abs=0.01)
    assert w["length_m"] == pytest.approx(1.7, abs=0.05)
    thick = sorted(
        round(x["length_m"], 1)
        for x in res.walls
        if x["thickness_m"] > 0.2 and abs(x["a_m"][1] - x["b_m"][1]) < 0.01
    )
    assert thick.count(4.0) == 2  # the run stops at the bay on both sides
    assert not _windows(res)
    assert _areas(res) == [60.0]


def test_thin_fill_rules():
    t = 0.3
    a, b = (4.0, 0.0), (6.0, 0.0)
    assert W._thin_fill([_w(1, (4.15, 0.0), (5.85, 0.0), 0.12)], a, b, t, 0.01)
    # on one face of the thick wall still counts (within half its thickness)
    assert W._thin_fill([_w(1, (4.0, 0.1), (6.0, 0.1), 0.12)], a, b, t, 0.01)
    # barely thinner: same wall type, not a thin bay
    assert not W._thin_fill([_w(1, (4.0, 0.0), (6.0, 0.0), 0.25)], a, b, t, 0.01)
    # covers too little of the gap
    assert not W._thin_fill([_w(1, (4.0, 0.0), (5.0, 0.0), 0.12)], a, b, t, 0.01)
    # parallel but off the wall line
    assert not W._thin_fill([_w(1, (4.0, 0.5), (6.0, 0.5), 0.12)], a, b, t, 0.01)
    # perpendicular partition ending in the gap
    assert not W._thin_fill([_w(1, (5.0, 0.0), (5.0, 3.0), 0.12)], a, b, t, 0.01)
    assert not W._thin_fill([], a, a, t, 0.01)


def test_one_metre_bay_is_a_frame_not_a_flagged_band(tmp_path):
    # the band covers gap - t (0.7 of 1.0 m), under THIN_FILL_SHARE: the thick
    # run carries through and nothing is flagged, as for any band under
    # THIN_BAND_MIN_M
    res, _ = _read(tmp_path, _outline(_mass(_thin_bay(4.5, 5.5))))
    assert res.stats["maybe_glazing"] == 0
    assert _areas(res) == [60.0]


# ---- #740: a partition ending on a door opening, not on a wall --------------

SHELL_6X10 = [((0, 0), (6, 0), T_EXT), ((6, 0), (6, 10), T_EXT), ((6, 10), (0, 10), T_EXT),
              ((0, 10), (0, 0), T_EXT)]  # fmt: skip
CORRIDOR_WALL = [((0, 4), (6, 4), T_INT)]
# the corridor wall stops at 4.1 m: a 1.9 m opening to the east wall, bridged beside the corner
CORRIDOR_DOORS = ((4.975, 4), (1, 0), 1.75, 0.08)


def test_partition_ending_on_a_door_opening_splits_the_rooms(tmp_path):
    # a partition between two doors meets the opening line, not a drawn wall: the
    # Clinic soiled-utility / clean-supply pair (#740). Its end stops 0.08 m short.
    walls = SHELL_6X10 + CORRIDOR_WALL + [((5, 0), (5, 4), T_INT)]
    res, _ = _read(tmp_path, _outline(_mass(walls, [CORRIDOR_DOORS])))
    assert _areas(res) == [4.0, 20.0, 36.0]
    assert [o for o in res.openings if o.get("beside_corner")]
    assert not [r for r in res.review if r["kind"] == "unclosed_wall"]


def test_partition_stopping_well_short_of_a_door_opening_stays_open(tmp_path):
    # 0.5 m short is past a wall thickness of reach: an open end, not a junction
    walls = SHELL_6X10 + CORRIDOR_WALL + [((5, 0), (5, 3.5), T_INT)]
    res, _ = _read(tmp_path, _outline(_mass(walls, [CORRIDOR_DOORS])))
    assert _areas(res) == [24.0, 36.0]
    assert [r for r in res.review if r["kind"] == "unclosed_wall"]


def test_free_end_carry_skips_parallel_behind_and_past_openings():
    # a wall end at (0, 0) pointing +y; reach is 0.75 x 0.2 + 0.01 = 0.16
    wall, t, tol = [((0, -2), (0, 0))], [0.2], 0.01
    ahead = ((-1, 0.1), (1, 0.1), 0.2)
    assert W._ends_to_openings(wall, t, [], [ahead], tol) == [((0, 0), (0.0, 0.1))]
    parallel = ((0.05, 0.05), (0.05, 1.0), 0.2)
    behind = ((-1, -0.1), (1, -0.1), 0.2)
    past_end = ((0.2, 0.1), (1, 0.1), 0.2)
    too_far = ((-1, 0.3), (1, 0.3), 0.2)
    for op in (parallel, behind, past_end, too_far):
        assert W._ends_to_openings(wall, t, [], [op], tol) == []
    # an end that already meets another line is left alone
    assert W._ends_to_openings(wall, t, [((-1, 0), (1, 0))], [ahead], tol) == []


# ---- #740: a chase drawn against a wall reads as a second, thicker band -----


def test_short_band_inside_a_longer_walls_band_is_dropped():
    wall = W.Wall("W1", (0, 0), (10, 0), 0.124, "line_pair")
    chase = W.Wall("W2", (4, 0.06), (4.6, 0.06), 0.248, "line_pair")  # in W1's band
    off_band = W.Wall("W3", (4, 0.2), (4.6, 0.2), 0.248, "line_pair")  # beside it
    past_end = W.Wall("W4", (9.6, 0.06), (10.6, 0.06), 0.248, "line_pair")
    crossing = W.Wall("W5", (5, -0.05), (5, 0.05), 0.124, "line_pair")
    tol = 0.02
    kept = W._drop_thickenings([wall, chase, off_band, past_end, crossing], set(), tol)
    assert [w.id for w in kept] == ["W1", "W3", "W4", "W5"]
    # a wall bridged across an opening is never dropped
    assert [w.id for w in W._drop_thickenings([wall, chase], {"W2"}, tol)] == ["W1", "W2"]


def test_chase_against_a_wall_does_not_shift_the_rooms_it_bounds(tmp_path):
    # a 0.25 m chase drawn against a 0.15 m corridor wall, with a partition meeting
    # it: kept, the partition ends on the chase's centreline and both rooms lose
    # area to it (17.9 / 17.9 / 24.1 on develop). Dropped, they close on the wall.
    walls = (
        SHELL_6X10 + CORRIDOR_WALL + [((2.0, 4.06), (4.0, 4.06), 0.25), ((3, 4), (3, 10), T_INT)]
    )
    res, _ = _read(tmp_path, _outline(_mass(walls)))
    assert _areas(res) == [18.0, 18.0, 24.0]
    assert not [r for r in res.review if r["kind"] == "unclosed_wall"]


def _inside_band(other_t, other_off, band_t=12.0):
    """Glazed band along x (centre y = 0) and one parallel wall beside it (#868)."""
    g = W.Band(theta=0.0, rho=0.0, u0=0.0, u1=40.0, t=band_t, source="face", glazed=True)
    w = W.Wall("w", (0.0, 0.0), (40.0, 0.0), 5.0, "face")
    o = W.Wall("o", (0.0, other_off), (40.0, other_off), other_t, "face")
    return W._wall_inside_band(g, w, [w, o], atol=0.05, tol=0.5)


def test_thinner_wall_inside_glazed_band_is_not_a_window():
    assert _inside_band(other_t=3.0, other_off=4.0)


def test_wall_as_thick_as_the_band_never_lies_inside_it():
    # a thickened pier or lined wall beside a real window (Clinic SF, #868)
    assert not _inside_band(other_t=12.0, other_off=3.0)
    assert not _inside_band(other_t=20.0, other_off=3.0)


def test_collinear_wall_piece_is_not_inside_the_band():
    assert not _inside_band(other_t=3.0, other_off=0.5)
