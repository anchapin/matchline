"""Closed wall loops that no IfcSpace claims become review items (#581)."""

import pytest

from ifc_wall_loops import unclaimed_loops

RING = [((0, 0), (6, 0)), ((6, 0), (6, 3)), ((6, 3), (0, 3)), ((0, 3), (0, 0))]
PART = [((4, 0), (4, 3))]
SHAFT = [((5, 0), (5, 1.5)), ((5, 1.5), (6, 1.5))]
A = [(0, 0), (4, 0), (4, 3), (0, 3)]
B_L = [(4, 0), (5, 0), (5, 1.5), (6, 1.5), (6, 3), (4, 3)]


def test_unmodelled_shaft_is_the_one_loop():
    out = unclaimed_loops(RING + PART + SHAFT, [A, B_L], half_thickness=0.1)
    assert len(out) == 1
    r = out[0]
    assert r["area_m2"] == pytest.approx(1.5, rel=0.01)
    assert r["inner_area_m2"] == pytest.approx(0.8 * 1.3, rel=0.01)
    assert r["candidate"] == "unknown"
    assert sorted(r["polygon_m"]) == [(5, 0), (5, 1.5), (6, 0), (6, 1.5)]


def test_slab_opening_inside_makes_it_a_shaft_candidate():
    void = [(5.2, 0.4), (5.8, 0.4), (5.8, 1.0), (5.2, 1.0)]
    out = unclaimed_loops(RING + PART + SHAFT, [A, B_L], [void], 0.1)
    assert [r["candidate"] for r in out] == ["shaft"]


def test_slab_opening_elsewhere_does_not():
    void = [(1, 1), (1.6, 1), (1.6, 1.6), (1, 1.6)]
    out = unclaimed_loops(RING + PART + SHAFT, [A, B_L], [void], 0.1)
    assert [r["candidate"] for r in out] == ["unknown"]


def test_every_loop_claimed_gives_nothing():
    shaft_space = [(5, 0), (6, 0), (6, 1.5), (5, 1.5)]
    assert unclaimed_loops(RING + PART + SHAFT, [A, B_L, shaft_space], half_thickness=0.1) == []


def test_partly_covered_loop_is_not_unclaimed():
    # a space over a third of the right-hand loop: a coverage question, not an unclaimed loop
    right = [(4, 0), (6, 0), (6, 1.0), (4, 1.0)]
    assert unclaimed_loops(RING + PART, [A, right], half_thickness=0.1) == []


def test_open_loop_is_not_reported():
    gappy = [((5, 0), (5, 1.2)), ((5, 1.5), (6, 1.5))]
    assert unclaimed_loops(RING + PART + gappy, [A, B_L], half_thickness=0.1) == []


def test_tiny_faces_are_ignored():
    tiny = [((5.6, 0), (5.6, 0.3)), ((5.6, 0.3), (6, 0.3))]
    assert unclaimed_loops(RING + PART + tiny, [A, B_L + []], half_thickness=0.1) == []


def test_t_end_a_hair_off_the_run_still_closes():
    off = [((5, 0.0005), (5, 1.5)), ((5, 1.5), (5.9995, 1.5))]
    assert len(unclaimed_loops(RING + PART + off, [A, B_L], half_thickness=0.1)) == 1


def test_no_spaces_flags_every_loop():
    assert len(unclaimed_loops(RING + PART, [], half_thickness=0.1)) == 2


def _ifc_with_shaft(tmp_path, with_void=False):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.api.spatial as sp
    import ifcopenshell.guid as guid

    from bem_export import BEMModel, BEMSpace, write_ifc4
    from tests.test_ifc_closet_merge import _place
    from tests.test_ifc_import import H, _add_solid, _rect_profile

    bem = BEMModel(
        building_name="T",
        spaces=[
            BEMSpace(
                sid="a",
                name="OFFICE 101",
                number="101",
                polygon_m=A,
                area_m2=12.0,
                volume_m3=12.0 * H,
            ),
            BEMSpace(
                sid="b",
                name="OPEN OFFICE 102",
                number="102",
                polygon_m=B_L,
                area_m2=4.5,
                volume_m3=4.5 * H,
            ),
        ],
        openings=[],
        ring_m=[(0, 0), (6, 0), (6, 3), (0, 3)],
        wall_height_m=H,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
    )
    path = tmp_path / "base.ifc"
    write_ifc4(bem, path)
    f = ifcopenshell.open(str(path))
    storey = f.by_type("IfcBuildingStorey")[0]
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    prods = []
    # partition stops at the ring faces; shaft walls meet the ring and each other.
    # Ring bodies sit inward of the exporter's axis, so the shaft loop closes
    # on centrelines x 5..5.9, y 0.1..1.5: 0.9 x 1.4 = 1.26 m^2 (#579)
    for name, start, ref, length in (
        ("P1", (4, 0.1, 0), (0, 1, 0), 2.8),
        ("P2", (5, 0.05, 0), (0, 1, 0), 1.45),
        ("P3", (5, 1.5, 0), (1, 0, 0), 1.0),
    ):
        w = f.create_entity("IfcWall", GlobalId=guid.new(), Name=name)
        w.ObjectPlacement = _place(f, start, storey.ObjectPlacement, refdir=ref)
        _add_solid(f, w, _rect_profile(f, length, 0.2, ox=length / 2), H, body)
        prods.append(w)
    if with_void:
        slab = f.create_entity("IfcSlab", GlobalId=guid.new(), Name="Floor", PredefinedType="FLOOR")
        slab.ObjectPlacement = _place(f, (0, 0, -0.2), storey.ObjectPlacement)
        _add_solid(f, slab, _rect_profile(f, 6, 3, ox=3, oy=1.5), 0.2, body)
        op = f.create_entity("IfcOpeningElement", GlobalId=guid.new(), Name="Shaft void")
        op.ObjectPlacement = _place(f, (5.5, 0.75, 0), slab.ObjectPlacement)
        _add_solid(f, op, _rect_profile(f, 0.6, 0.6), 0.2, body)
        f.create_entity(
            "IfcRelVoidsElement",
            GlobalId=guid.new(),
            RelatingBuildingElement=slab,
            RelatedOpeningElement=op,
        )
        prods.append(slab)
    sp.assign_container(f, products=prods, relating_structure=storey)
    out = tmp_path / "shaft.ifc"
    f.write(str(out))
    return out


def _loop_items(model):
    return [it for it in model.review_queue if it.kind == "unclaimed_wall_loop"]


def test_ifc_unmodelled_shaft_flagged_not_created(tmp_path):
    from ifc_import import import_ifc

    model = import_ifc(_ifc_with_shaft(tmp_path))
    items = _loop_items(model)
    assert len(items) == 1
    assert "1.26 m^2 at wall centrelines" in items[0].description
    assert "candidate=unknown" in items[0].provenance.note
    assert sorted(model.spaces) == ["L1-101", "L1-102"]  # nothing invented
    assert "unclaimed wall loops: 1 (1.26 m^2)" in model.revision_log[-1].note


def test_ifc_slab_void_marks_shaft_candidate(tmp_path):
    from ifc_import import import_ifc

    model = import_ifc(_ifc_with_shaft(tmp_path, with_void=True))
    items = _loop_items(model)
    assert len(items) == 1
    assert "candidate=shaft" in items[0].provenance.note
    assert "likely an unmodelled shaft" in items[0].description


def test_area_closure_reports_unclaimed_area(tmp_path):
    from ifc_import import import_ifc
    from validate.conservation import area_closure

    model = import_ifc(_ifc_with_shaft(tmp_path))
    res = area_closure(model)
    assert "1 unclaimed wall loop(s), 1.26 m^2" in res.message


def test_exporter_round_trip_has_no_loops(tmp_path):
    pytest.importorskip("ifcopenshell")
    from ifc_import import import_ifc
    from tests.test_ifc_import import make_ifc_fixture

    make_ifc_fixture(tmp_path / "t.ifc")
    model = import_ifc(tmp_path / "t.ifc")
    assert _loop_items(model) == []
