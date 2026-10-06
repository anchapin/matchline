"""IFC plan handedness and mirrored plans (#587).

matchline's canonical frame is y-down: an IFC plan point (X, Y) is (X, -Y).
Idea from Pascal's tests/handedness.test.ts (MIT, Copyright (c) 2026 Pascal
Group Inc., commit 67f8041).
"""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

from ifc_import import import_ifc  # noqa: E402
from tests.ifc_builder import IfcBuilder, box_plan  # noqa: E402

L_SHAPE = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 2), (0, 2)]


def _plan(x, y):
    return [x, -y]


def _import(tmp_path, mirror=False, build=box_plan):
    b = IfcBuilder(mirror_x=mirror)
    build(b)
    return import_ifc(b.write(tmp_path / ("m.ifc" if mirror else "p.ifc")))


def _by_facade(m):
    return {w.facade: w for w in m.envelope}


def _openings(m):
    return {o.category: o for sp in m.spaces.values() for o in sp.openings}


def _signed_area(poly):
    n = len(poly)
    return (
        sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
        / 2
    )


def test_walls_keep_the_ifc_plan_orientation(tmp_path):
    m = _import(tmp_path)
    w = _by_facade(m)
    assert w["south"].from_m == pytest.approx(_plan(0, 0))
    assert w["south"].to_m == pytest.approx(_plan(10, 0))
    assert w["east"].from_m == pytest.approx(_plan(10, 0))
    assert w["east"].to_m == pytest.approx(_plan(10, 6))
    # south -> east turns counterclockwise seen from above in IFC (cross > 0);
    # the y-down canonical frame flips the sign, never the plan
    a = [w["south"].to_m[i] - w["south"].from_m[i] for i in (0, 1)]
    b = [w["east"].to_m[i] - w["east"].from_m[i] for i in (0, 1)]
    assert a[0] * b[1] - a[1] * b[0] < 0


def test_openings_keep_their_distance_from_the_wall_start(tmp_path):
    ops = _openings(_import(tmp_path))
    assert ops["door"].host_facade == "south" and ops["door"].s_center_m == pytest.approx(1.0)
    # an opening placed off the storey (not relative to its wall) on a
    # rotated wall: was landing off its wall before #587
    assert ops["window"].host_facade == "east" and ops["window"].s_center_m == pytest.approx(3.0)
    assert ops["window"].width_m == pytest.approx(2.0)
    assert "outside wall extent" not in ops["window"].provenance.note


def test_space_polygon_keeps_the_ifc_plan_orientation(tmp_path):
    def build(b):
        b.space("L 101", L_SHAPE)

    (sp,) = _import(tmp_path, build=build).spaces.values()
    want = [_plan(x, y) for x, y in L_SHAPE]
    got = [list(p) for p in sp.polygon_m]
    assert sorted(map(tuple, got)) == pytest.approx(sorted(map(tuple, want)))
    assert abs(_signed_area(got)) == pytest.approx(5.0)


def test_mirrored_plan_imports_mirrored(tmp_path):
    plain = _import(tmp_path)
    mirror = _import(tmp_path, mirror=True)
    (p,) = plain.spaces.values()
    (q,) = mirror.spaces.values()
    assert abs(_signed_area(q.polygon_m)) == pytest.approx(abs(_signed_area(p.polygon_m)))
    assert sorted((-x, y) for x, y in q.polygon_m) == pytest.approx(
        sorted(tuple(v) for v in p.polygon_m)
    )
    wp, wm = _by_facade(plain), _by_facade(mirror)
    assert set(wp) == set(wm) == {"north", "south", "east", "west"}
    for a, b in (("east", "west"), ("west", "east"), ("north", "north"), ("south", "south")):
        assert wm[b].area_m2 == pytest.approx(wp[a].area_m2)
    assert sum(w.area_m2 for w in wm.values()) == pytest.approx(sum(w.area_m2 for w in wp.values()))


def test_mirrored_plan_keeps_every_opening_on_its_mirrored_facade(tmp_path):
    op, om = _openings(_import(tmp_path)), _openings(_import(tmp_path, mirror=True))
    assert om["door"].host_facade == "south" and om["door"].s_center_m == pytest.approx(1.0)
    assert om["window"].host_facade == "west" and op["window"].host_facade == "east"
    assert om["window"].s_center_m == pytest.approx(op["window"].s_center_m)
    assert om["window"].width_m == pytest.approx(op["window"].width_m)


def test_door_plan_centre_is_mirrored(tmp_path):
    dp = next(o for e in _import(tmp_path).bim_elements for o in e.openings if o.category == "door")
    dm = next(
        o
        for e in _import(tmp_path, mirror=True).bim_elements
        for o in e.openings
        if o.category == "door"
    )
    assert dp.plan_center_m[:2] == pytest.approx(_plan(1, 0), abs=1e-6)
    assert dm.plan_center_m[:2] == pytest.approx(_plan(-1, 0), abs=1e-6)


def test_builder_ceilings_and_slabs_import(tmp_path):
    def build(b):
        box_plan(b, east_window=False)
        b.slab("Deck", [(0, 0), (10, 0), (10, 6), (0, 6)], z=3.4, thickness=0.2)
        b.covering("Ceiling", [(0, 0), (10, 0), (10, 6), (0, 6)], z=2.7)

    (sp,) = _import(tmp_path, build=build).spaces.values()
    assert sp.ceiling_height_m == pytest.approx(2.7)
    assert sp.plenum_depth_m == pytest.approx(3.4 - 2.72)
