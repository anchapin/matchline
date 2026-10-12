"""IFC wall ends join onto neighbour centrelines (#575)."""

import math

import pytest

from building_model import EnvelopeWall, Provenance
from ifc_wall_joins import join_wall_ends

T = 0.2  # wall thickness in these fixtures


def _w(n, a, b, level="L1", method="ifc_import:tier0:envelope", h=3.0):
    L = math.dist(a, b)
    return EnvelopeWall(
        id=f"{level}-EW{n}",
        facade="",
        from_m=list(a),
        to_m=list(b),
        length_m=L,
        height_m=h,
        area_m2=L * h,
        provenance=Provenance(
            sheet_id="s",
            revision="r",
            method=method,
            confidence=0.95,
            note=f"GlobalId=W{n} wall centerline segment",
        ),
    )


def _thick(*walls, t=T):
    return {w.id.split("-EW")[1] and f"W{w.id.split('-EW')[1]}": t for w in walls}


def test_face_stopped_l_corner_closes():
    a, b = _w(1, (0.1, 0), (5, 0)), _w(2, (0, 0.1), (0, 4))
    c = join_wall_ends([a, b], _thick(a, b))
    assert c == {"moved": 2, "ambiguous": 0}
    assert a.from_m == [0, 0] and b.from_m == [0, 0]
    assert a.length_m == pytest.approx(5.0, abs=1e-3)
    assert b.length_m == pytest.approx(4.0, abs=1e-3)
    assert a.area_m2 == pytest.approx(15.0, abs=1e-3)
    assert "start extended 0.100 m onto centreline of W2" in a.provenance.note


def test_overshooting_l_corner_trims_to_centreline():
    a, b = _w(1, (-0.1, 0), (5, 0)), _w(2, (0, 0.1), (0, 4))
    join_wall_ends([a, b], _thick(a, b))
    assert a.from_m == [0, 0] and b.from_m == [0, 0]
    assert a.length_m == pytest.approx(5.0, abs=1e-3)
    assert "start trimmed 0.100 m" in a.provenance.note


def test_t_join_moves_only_the_stem():
    a, s = _w(1, (0, 0), (6, 0)), _w(3, (3, 0.1), (3, 3))
    join_wall_ends([a, s], _thick(a, s))
    assert s.from_m == [3, 0] and s.length_m == pytest.approx(3.0, abs=1e-3)
    assert a.from_m == [0, 0] and a.to_m == [6, 0]
    assert "onto centreline" not in a.provenance.note


def test_skewed_join_reaches_along_the_moving_wall():
    # stem at 60 deg to the run: half-thickness along the stem is 0.1/sin60
    d = 0.1 / math.sin(math.radians(60))
    ux, uy = math.cos(math.radians(60)), math.sin(math.radians(60))
    a = _w(1, (0, 0), (6, 0))
    s = _w(2, (3 + ux * d, uy * d), (3 + ux * 3, uy * 3))
    join_wall_ends([a, s], _thick(a, s))
    assert s.from_m == pytest.approx([3, 0], abs=1e-3)


def test_near_parallel_walls_never_join():
    ang = math.radians(10)
    a = _w(1, (0, 0), (5, 0))
    b = _w(2, (5.05, 0.0), (5.05 + 3 * math.cos(ang), 3 * math.sin(ang)))
    c = join_wall_ends([a, b], _thick(a, b))
    assert c["moved"] == 0


def test_gap_beyond_reach_stays_open_unless_connected():
    # 0.4 m gap: past a's reach, and the corner point is off the end of a's run
    a, b = _w(1, (0.4, 0), (5, 0)), _w(2, (0, 0.1), (0, 4))
    assert join_wall_ends([a, b], _thick(a, b))["moved"] == 0
    assert a.from_m == [0.4, 0] and b.from_m == [0, 0.1]
    # IfcRelConnectsPathElements says they meet: reach widens to 0.6 m
    a2, b2 = _w(1, (0.4, 0), (5, 0)), _w(2, (0, 0.1), (0, 4))
    c = join_wall_ends([a2, b2], _thick(a2, b2), frozenset({frozenset({"W1", "W2"})}))
    assert c["moved"] == 2 and a2.from_m == [0, 0] and b2.from_m == [0, 0]


def test_already_joined_is_left_alone():
    a, b = _w(1, (0, 0), (5, 0)), _w(2, (0, 0), (0, 4))
    note = a.provenance.note
    assert join_wall_ends([a, b], _thick(a, b))["moved"] == 0
    assert a.provenance.note == note


def test_unknown_thickness_reaches_only_the_gap_tolerance():
    a, b = _w(1, (0.04, 0), (5, 0)), _w(2, (0, 0.04), (0, 4))
    assert join_wall_ends([a, b], {})["moved"] == 2
    assert a.from_m == [0, 0] and b.from_m == [0, 0]
    c, d = _w(1, (0.1, 0), (5, 0)), _w(2, (0, 0.1), (0, 4))
    assert join_wall_ends([c, d], {})["moved"] == 0


def test_order_does_not_change_the_result():
    def walls():
        return [
            _w(1, (0.1, 0), (5, 0)),
            _w(2, (0, 0.1), (0, 4)),
            _w(3, (2.5, 0.1), (2.5, 4)),
            _w(4, (0.1, 4), (4.9, 4)),
            _w(5, (5, 0.1), (5, 3.9)),
        ]

    x, y = walls(), list(reversed(walls()))
    join_wall_ends(x, _thick(*x))
    join_wall_ends(y, _thick(*y))
    got = {w.id: (w.from_m, w.to_m) for w in x}
    assert got == {w.id: (w.from_m, w.to_m) for w in y}


def test_other_levels_and_non_ifc_segments_are_untouched():
    a, b = _w(1, (0.1, 0), (5, 0)), _w(2, (0, 0.1), (0, 4), level="L2")
    assert join_wall_ends([a, b], _thick(a, b))["moved"] == 0
    c, d = _w(1, (0.1, 0), (5, 0), method="drawing"), _w(2, (0, 0.1), (0, 4))
    join_wall_ends([c, d], _thick(c, d))
    assert c.from_m == [0.1, 0]


def test_two_tied_neighbours_that_disagree_leave_the_end():
    # end exactly midway between two parallel cross walls, 0.1 m each side
    a = _w(1, (0, 0.0), (5, 0.0))
    b = _w(2, (-0.1, -2), (-0.1, 2))
    c = _w(3, (0.1, -2), (0.1, 2))
    c_counts = join_wall_ends([a, b, c], _thick(a, b, c))
    assert a.from_m == [0, 0]
    assert c_counts["ambiguous"] >= 1
    assert "two neighbour centrelines tie" in a.provenance.note


def test_ifc_face_stopped_walls_join_on_import(tmp_path):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.api.spatial as sp
    import ifcopenshell.guid as guid

    from ifc_import import import_ifc
    from tests.test_ifc_closet_merge import _place
    from tests.test_ifc_import import _add_solid, _rect_profile, make_ifc_fixture

    path = tmp_path / "base.ifc"
    make_ifc_fixture(path)
    f = ifcopenshell.open(str(path))
    storey = f.by_type("IfcBuildingStorey")[0]
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    walls = []
    # Revit-style open L far from the spaces: x-run stops at the y-run's face
    for name, start, ref, length in (
        ("JX", (30.1, 0, 0), (1, 0, 0), 4.9),
        ("JY", (30, 0.1, 0), (0, 1, 0), 3.9),
    ):
        w = f.create_entity("IfcWall", GlobalId=guid.new(), Name=name)
        w.ObjectPlacement = _place(f, start, storey.ObjectPlacement, refdir=ref)
        _add_solid(f, w, _rect_profile(f, length, T, ox=length / 2), 3.0, body)
        walls.append(w)
    sp.assign_container(f, products=walls, relating_structure=storey)
    out = tmp_path / "joined.ifc"
    f.write(str(out))

    model = import_ifc(out)
    mine = [
        e
        for e in model.envelope
        if any(f"GlobalId={w.GlobalId}" in e.provenance.note for w in walls)
    ]
    assert len(mine) == 2
    assert sorted(round(e.length_m, 3) for e in mine) == [4.0, 5.0]
    # 2 for the L, plus the base fixture's 8 corner ends closing on centrelines
    assert "wall joins: 10 ends moved" in model.revision_log[-1].note


def test_exporter_corners_close_on_centrelines(tmp_path):
    """Exporter walls sit on the exterior face; after the centreline move (#579)
    their corners no longer meet, and the joins close every one (#575)."""
    pytest.importorskip("ifcopenshell")
    from ifc_import import import_ifc
    from tests.test_ifc_import import make_ifc_fixture

    make_ifc_fixture(tmp_path / "t.ifc")
    model = import_ifc(tmp_path / "t.ifc")
    note = model.revision_log[-1].note
    assert "wall centrelines: 4 segments moved off the reference line" in note
    assert "wall joins: 8 ends moved" in note
    ext = [e for e in model.envelope if e.facade]
    ends = [tuple(e.from_m) for e in ext] + [tuple(e.to_m) for e in ext]
    for p in ends:
        assert sum(math.dist(p, q) < 1e-6 for q in ends) == 2, f"open corner at {p}"
