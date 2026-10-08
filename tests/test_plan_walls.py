"""Walls and rooms from vector plans (#740). Plans are drawn the way CAD
exports them: the outline of the wall mass as stroked lines (or solid poché),
door gaps with a leaf and swing, written to a PDF and read back through
pdf_ingest, so the whole vector path is exercised."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

sys.path.append(str(Path(__file__).parent))

from pdf_fixtures import PageSpec, curve, line, text, write_pdf  # noqa: E402

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
    joins = W._join_free_ends(pairs, [0.30, 0.15], tol=0.01)
    assert ((4.0, 0.2), (4.0, 0.0)) in joins
    # 0.40 short is a real gap: no connector from that end
    far = W._join_free_ends([pairs[0], ((4.0, 0.4), (4.0, 5.0))], [0.30, 0.15], tol=0.01)
    assert all(a != (4.0, 0.4) for a, _ in far)
