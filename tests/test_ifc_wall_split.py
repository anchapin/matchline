"""Junction splits and flush in-line continuations of IFC walls (#576)."""

import math

import pytest

from building_model import EnvelopeWall, Provenance
from ifc_wall_split import join_inline_ends, split_at_junctions

H = 3.0


def _seg(sid, a, b, gid):
    return EnvelopeWall(
        id=sid,
        facade="",
        from_m=list(a),
        to_m=list(b),
        length_m=math.dist(a, b),
        height_m=H,
        area_m2=math.dist(a, b) * H,
        provenance=Provenance(
            sheet_id="t.ifc",
            revision=1,
            method="ifc_import:tier0:envelope",
            confidence=0.95,
            note=f"GlobalId={gid} wall centerline segment",
        ),
    )


def _len(w):
    return round(math.dist(w.from_m, w.to_m), 4)


def test_x_crossing_splits_both_walls():
    # corridor wall along x crossed mid-span by a partition along y
    env = [
        _seg("L1-EW1", (0, 0), (10, 0), "CORR"),
        _seg("L1-EW2", (4, -3), (4, 3), "PART"),
    ]
    n = split_at_junctions(env, {"CORR": 0.2, "PART": 0.1})
    assert n == 2
    assert sorted(w.id for w in env) == ["L1-EW1.1", "L1-EW1.2", "L1-EW2.1", "L1-EW2.2"]
    by = {w.id: w for w in env}
    assert [_len(by["L1-EW1.1"]), _len(by["L1-EW1.2"])] == [4.0, 6.0]
    assert [_len(by["L1-EW2.1"]), _len(by["L1-EW2.2"])] == [3.0, 3.0]
    assert sum(w.area_m2 for w in env) == pytest.approx((10 + 6) * H)
    for w in env:
        assert w.provenance.note.startswith("GlobalId=")
        assert "crossing with" in w.provenance.note
    # pieces carry their own provenance objects
    assert by["L1-EW1.1"].provenance is not by["L1-EW1.2"].provenance


def test_t_junction_splits_the_through_wall_only():
    env = [
        _seg("L1-EW1", (0, 0), (10, 0), "CORR"),
        _seg("L1-EW2", (6, 0), (6, 4), "PART"),  # end sits on the corridor centreline
    ]
    assert split_at_junctions(env, {"CORR": 0.2, "PART": 0.1}) == 1
    assert sorted((w.id, _len(w)) for w in env) == [
        ("L1-EW1.1", 6.0),
        ("L1-EW1.2", 4.0),
        ("L1-EW2", 4.0),
    ]
    assert "T junction with PART" in env[0].provenance.note


def test_junction_near_an_end_is_not_split():
    # partition 0.1 m from the corridor end: inside the margin, an L not a T
    env = [
        _seg("L1-EW1", (0, 0), (10, 0), "CORR"),
        _seg("L1-EW2", (0.1, 0), (0.1, 4), "PART"),
    ]
    assert split_at_junctions(env, {"CORR": 0.2, "PART": 0.1}) == 0
    assert [w.id for w in env] == ["L1-EW1", "L1-EW2"]


def test_other_levels_and_non_ifc_segments_ignored():
    env = [
        _seg("L1-EW1", (0, 0), (10, 0), "A"),
        _seg("L2-EW1", (4, -3), (4, 3), "B"),
    ]
    drawn = _seg("L1-EW2", (4, -3), (4, 3), "C")
    drawn.provenance.method = "arch_plan:wall_run"
    env.append(drawn)
    assert split_at_junctions(env, {"A": 0.2, "B": 0.2, "C": 0.2}) == 0


def test_flush_thinner_continuation_joins_into_one_run():
    # 200 mm wall on centreline y=0, 100 mm continuation flush with its
    # y=-0.1 face: centreline y=-0.05, a 0.05 m sideways step at x=5
    env = [
        _seg("L1-EW1", (0, 0), (5, 0), "THICK"),
        _seg("L1-EW2", (5, -0.05), (9, -0.05), "THIN"),
    ]
    counts = join_inline_ends(env, {"THICK": 0.2, "THIN": 0.1})
    assert counts == {"joined": 1, "ambiguous": 0}
    thick, thin = env
    assert thin.from_m == [5, 0]  # onto the thick wall's end: one continuous run
    assert thick.to_m == [5, 0]
    assert thin.length_m == pytest.approx(math.dist((5, 0), (9, -0.05)), abs=1e-4)
    assert "sideways onto in-line wall THICK" in thin.provenance.note


def test_inline_needs_known_thickness_and_near_parallel():
    env = [
        _seg("L1-EW1", (0, 0), (5, 0), "THICK"),
        _seg("L1-EW2", (5, -0.05), (9, -0.05), "THIN"),
    ]
    assert join_inline_ends(env, {"THICK": 0.2}) == {"joined": 0, "ambiguous": 0}
    env2 = [
        _seg("L1-EW1", (0, 0), (5, 0), "A"),
        _seg("L1-EW2", (5, -0.05), (9, 1.0), "B"),  # ~15 degrees off: not in-line
    ]
    assert join_inline_ends(env2, {"A": 0.2, "B": 0.1}) == {"joined": 0, "ambiguous": 0}


def test_inline_tie_left_as_is():
    # two thick ends equally near the thin wall's start, either side of it
    env = [
        _seg("L1-EW1", (0, 0.05), (5, 0.05), "UP"),
        _seg("L1-EW2", (0, -0.05), (5, -0.05), "DOWN"),
        _seg("L1-EW3", (5, 0), (9, 0), "THIN"),
    ]
    counts = join_inline_ends(env, {"UP": 0.2, "DOWN": 0.2, "THIN": 0.1})
    assert counts["ambiguous"] >= 1
    assert env[2].from_m == [5, 0]
    assert "two equally near continuations" in env[2].provenance.note


def _ifc_with(tmp_path, extra):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.api.spatial as sp
    import ifcopenshell.guid as guid

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
    for name, start, ref, length, t in extra:
        w = f.create_entity("IfcWall", GlobalId=guid.new(), Name=name)
        w.ObjectPlacement = _place(f, start, storey.ObjectPlacement, refdir=ref)
        _add_solid(f, w, _rect_profile(f, length, t, ox=length / 2), H, body)
        walls.append(w)
    sp.assign_container(f, products=walls, relating_structure=storey)
    out = tmp_path / "split.ifc"
    f.write(str(out))
    return out


def test_ifc_partition_t_splits_exterior_wall_per_room(tmp_path):
    """The fixture's 20 m south wall runs behind L1-101 (x 0..10) and L1-102
    (x 10..20); a partition T-ing into it at x=10 gives each room its own piece."""
    from ifc_import import import_ifc

    # IFC y grows north; south wall centreline sits at IFC y=0.1 after #579
    model = import_ifc(_ifc_with(tmp_path, [("Part", (10, 0.1, 0), (0, 1, 0), 7.9, 0.1)]))
    south = sorted(
        (w for w in model.envelope if w.facade == "south"),
        key=lambda w: min(w.from_m[0], w.to_m[0]),
    )
    assert [w.space_id for w in south] == ["L1-101", "L1-102"]
    assert [round(w.length_m, 3) for w in south] == [9.9, 9.9]
    assert "wall splits: 1 at X/T junctions" in model.revision_log[-1].note


def test_exporter_round_trip_has_no_splits(tmp_path):
    pytest.importorskip("ifcopenshell")
    from ifc_import import import_ifc
    from tests.test_ifc_import import make_ifc_fixture

    make_ifc_fixture(tmp_path / "t.ifc")
    model = import_ifc(tmp_path / "t.ifc")
    note = model.revision_log[-1].note
    assert "wall splits" not in note and "in-line walls" not in note
    assert sorted(w.id for w in model.envelope) == ["L1-EW1", "L1-EW2", "L1-EW3", "L1-EW4"]
