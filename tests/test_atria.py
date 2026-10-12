"""Atria open through several storeys (#640)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from atria import find_atria
from bem_export import write_gbxml
from ifc_export import _bem_from_model
from interstory import match_interstory
from tests.test_multistorey_export import NS, _adj, _model, _surfaces, _write, _zs

# L1 0 m, L2 3 m, L3 6 m (3 m storeys). Atrium A on L1 (0..8 x 0..10), 6 m
# tall: open through L2, roofed by the L3 floor. Rooms beside it on every level.


def _atrium_model(height=6.0, blocker=False, top_room=True):
    spaces = [
        ("L1-ATR", "L1", (0, 0, 8, 10)),
        ("L1-101", "L1", (8, 0, 20, 10)),
        ("L2-201", "L2", (8, 0, 20, 10)),
        ("L3-301", "L3", (8, 0, 20, 10)),
    ]
    if top_room:
        spaces.append(("L3-300", "L3", (0, 0, 8, 10)))
    if blocker:
        spaces.append(("L2-200", "L2", (0, 0, 4, 10)))
    m = _model([("L1", 0.0, None), ("L2", 3.0, None), ("L3", 6.0, None)], spaces, walls=False)
    m.spaces["L1-ATR"].height_m = height
    return m


def test_stated_height_past_next_storey_is_an_atrium():
    res = find_atria(_atrium_model(), flag=False)
    assert res.spans == {"L1-ATR": ["L2"]}
    assert res.top_z["L1-ATR"] == pytest.approx(6.0)
    assert res.findings == []


def test_height_within_storey_is_not_an_atrium():
    m = _atrium_model(height=3.2)  # clears L2 by 0.2 m < tolerance
    assert find_atria(m, flag=False).spans == {}
    m.spaces["L1-ATR"].height_m = None
    assert find_atria(m, flag=False).spans == {}


def test_no_floor_inside_atrium_on_level_it_opens_through():
    ist = match_interstory(_atrium_model(), flag=False)
    assert ist.atria == {"L1-ATR": ["L2"]}
    kinds = {(s.kind, s.lower_space_id, s.upper_space_id) for s in ist.surfaces}
    # its ceiling is the L3 room's floor, not a roof at 3 m
    assert ("interior", "L1-ATR", "L3-300") in kinds
    assert not any(s.lower_space_id == "L1-ATR" and s.kind == "roof" for s in ist.surfaces)
    atr_ceil = [s for s in ist.surfaces if s.lower_space_id == "L1-ATR"]
    assert [round(s.z_m, 6) for s in atr_ceil] == [6.0]
    # L2-201 still sits on L1-101
    assert ("interior", "L1-101", "L2-201") in kinds


def test_atrium_with_nothing_above_has_its_own_roof():
    ist = match_interstory(_atrium_model(top_room=False), flag=False)
    roofs = [s for s in ist.surfaces if s.kind == "roof" and s.lower_space_id == "L1-ATR"]
    assert len(roofs) == 1 and roofs[0].z_m == pytest.approx(6.0)
    assert roofs[0].area_m2 == pytest.approx(80.0)


def test_space_over_the_atrium_stops_it_and_goes_to_review():
    m = _atrium_model(blocker=True)
    res = find_atria(m, flag=True)
    assert res.spans == {}
    assert "L2-200" in res.findings[0]
    assert any(r.kind == "atrium" for r in m.review_queue)
    ist = match_interstory(m, flag=False)
    assert ist.atria == {}


def test_gbxml_atrium_full_height_and_air_walls(tmp_path):
    m = _atrium_model()
    bem = _bem_from_model(m)
    atr = next(sp for sp in bem.spaces if sp.sid == "L1-ATR")
    assert atr.spans == ["L2"] and atr.height_m == pytest.approx(6.0)
    assert atr.volume_m3 == pytest.approx(80.0 * 6.0)
    walls = [aw for aw in bem.air_walls if "L1-ATR" in aw.space_ids]
    assert [aw.space_ids for aw in walls] == [("L2-201", "L1-ATR")]
    _p, root = _write(m, tmp_path)
    air = _surfaces(root, "Air")
    assert len(air) == 1
    assert _adj(air[0]) == ["L2-201", "L1-ATR"]
    assert min(_zs(air[0])) == pytest.approx(3.0) and max(_zs(air[0])) == pytest.approx(6.0)
    # wall line at x = 8, atrium to the west: normal points -x
    xs = {
        round(float(c.findall("g:Coordinate", NS)[0].text), 4)
        for c in air[0].iter(f"{{{NS['g']}}}CartesianPoint")
    }
    assert xs == {8.0}
    # no floor between atrium and anything on L2
    for s in _surfaces(root, "InteriorFloor"):
        assert not ("L1-ATR" in _adj(s) and min(_zs(s)) == pytest.approx(3.0))


def test_air_wall_winding_points_into_atrium():
    bem = _bem_from_model(_atrium_model())
    (aw,) = [aw for aw in bem.air_walls if "L1-ATR" in aw.space_ids]
    dx, dy = aw.p1[0] - aw.p0[0], aw.p1[1] - aw.p0[1]
    # right of travel is (dy, -dx); atrium is west of x = 8
    assert dy < 0 and dx == pytest.approx(0.0)


def test_single_storey_and_plain_models_unchanged(tmp_path):
    a = _atrium_model(height=None)
    b = _atrium_model(height=3.0)
    pa = write_gbxml(_bem_from_model(a), tmp_path / "a.xml")
    pb = write_gbxml(_bem_from_model(b), tmp_path / "b.xml")
    assert pa.read_bytes() == pb.read_bytes()
    assert ET.parse(pa).getroot() is not None


def test_atrium_ifc4_air_walls_on_upper_storey(tmp_path):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell
    import ifcopenshell.util.element as El

    from bem_export import validate_ifc4, write_ifc4

    p = write_ifc4(_bem_from_model(_atrium_model()), tmp_path / "a.ifc")
    ok, errs = validate_ifc4(p)
    assert ok, errs[:5]
    f = ifcopenshell.open(str(p))
    virt = [v for v in f.by_type("IfcVirtualElement") if "L1-ATR" in (v.Name or "")]
    assert len(virt) == 1
    assert El.get_container(virt[0]).Name in ("L2", "Level L2")


# --- stacks of rooms over slab openings (#640) ------------------------------
from atria import merge_atrium_stacks  # noqa: E402


def _stack_model():
    m = _model(
        [("L1", 0.0, None), ("L2", 3.0, None), ("L3", 6.0, None)],
        [
            ("L1-ATR", "L1", (0, 0, 8, 10)),
            ("L2-OTB", "L2", (0, 0, 8, 10)),
            ("L3-OTB", "L3", (0, 0, 8, 10)),
            ("L1-101", "L1", (8, 0, 20, 10)),
            ("L2-201", "L2", (8, 0, 20, 10)),
            ("L3-301", "L3", (8, 0, 20, 10)),
        ],
    )
    for sid in ("L1-ATR", "L2-OTB", "L3-OTB"):
        m.spaces[sid].volume_m3 = 240.0
    return m


def _hole(x0, y0, x1, y1, z):
    return ([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], z - 0.2, z)


def test_stack_over_slab_openings_folds_into_one_atrium():
    m = _stack_model()
    n = merge_atrium_stacks(m, [_hole(0, 0, 8, 10, 3.0), _hole(0, 0, 8, 10, 6.0)])
    assert n == 2
    assert "L2-OTB" not in m.spaces and "L3-OTB" not in m.spaces
    atr = m.spaces["L1-ATR"]
    assert atr.merged_from == ["L2-OTB", "L3-OTB"]
    assert atr.height_m == pytest.approx(9.0)
    assert atr.volume_m3 == pytest.approx(720.0)
    assert {w.space_id for w in m.envelope if w.id.startswith("L2-OTB")} == {"L1-ATR"}
    assert find_atria(m, flag=False).spans == {"L1-ATR": ["L2", "L3"]}
    assert m.review_queue == []


def test_stack_stops_where_the_floor_is_solid():
    m = _stack_model()
    assert merge_atrium_stacks(m, [_hole(0, 0, 8, 10, 3.0)]) == 1
    assert "L3-OTB" in m.spaces
    assert m.spaces["L1-ATR"].height_m == pytest.approx(6.0)
    ist = match_interstory(m, flag=False)
    assert ("interior", "L1-ATR", "L3-OTB") in {
        (s.kind, s.lower_space_id, s.upper_space_id) for s in ist.surfaces
    }


def test_no_openings_no_merge():
    m = _stack_model()
    assert merge_atrium_stacks(m, []) == 0
    assert len(m.spaces) == 6 and m.review_queue == []


def test_partial_opening_goes_to_review():
    m = _stack_model()
    assert merge_atrium_stacks(m, [_hole(0, 0, 2, 2, 3.0)]) == 0  # stair hole, 5 %
    assert "L2-OTB" in m.spaces
    (item,) = [r for r in m.review_queue if r.kind == "atrium"]
    assert "L1-ATR" in item.description and "L2-OTB" in item.description


def test_mismatched_footprints_go_to_review():
    m = _stack_model()
    m.spaces["L2-OTB"].polygon_m = [[0, 0], [4, 0], [4, 10], [0, 10]]
    assert merge_atrium_stacks(m, [_hole(0, 0, 8, 10, 3.0)]) == 0
    assert any(r.kind == "atrium" for r in m.review_queue)


def test_shafts_and_far_openings_ignored():
    m = _stack_model()
    m.spaces["L2-OTB"].poly_type = "shaft"
    assert merge_atrium_stacks(m, [_hole(0, 0, 8, 10, 3.0)]) == 0
    m = _stack_model()
    assert merge_atrium_stacks(m, [_hole(0, 0, 8, 10, 1.5)]) == 0  # mid-storey, no floor
    assert m.review_queue == []


def test_folded_atrium_exports_one_space(tmp_path):
    m = _stack_model()
    merge_atrium_stacks(m, [_hole(0, 0, 8, 10, 3.0), _hole(0, 0, 8, 10, 6.0)])
    _p, root = _write(m, tmp_path)
    ids = {s.get("id") for s in root.iter(f"{{{NS['g']}}}Space")}
    assert "L1-ATR" in ids and "L2-OTB" not in ids
    air = _surfaces(root, "Air")
    assert sorted(_adj(s)[0] for s in air) == ["L2-201", "L3-301"]


def test_ifc_import_folds_open_to_below_rooms(tmp_path):
    pytest.importorskip("ifcopenshell")
    import ifcopenshell.api.aggregate as Ag
    import ifcopenshell.api.root as Root
    import ifcopenshell.guid as guid

    from ifc_import import import_ifc
    from tests.ifc_builder import IfcBuilder

    b = IfcBuilder()
    bldg = b.f.by_type("IfcBuilding")[0]
    rect = [(0, 0), (8, 0), (8, 10), (0, 10)]
    b.space("101", rect, height=3.0, long_name="Lobby")
    for k, z in ((2, 3.0), (3, 6.0)):
        st = Root.create_entity(b.f, ifc_class="IfcBuildingStorey", name=f"Level {k}")
        st.Elevation = z
        Ag.assign_object(b.f, products=[st], relating_object=bldg)
        st.ObjectPlacement = b._placement((0, 0, z), bldg.ObjectPlacement, mirror=False)
        b.storey = st
        b.space(f"{k}01", rect, height=3.0, long_name="Open to below")
        slab = b.slab(f"Floor {k}", rect)
        op = b.f.create_entity("IfcOpeningElement", GlobalId=guid.new(), Name="Atrium void")
        op.ObjectPlacement = b._placement((0, 0, 0), slab.ObjectPlacement)
        b._solid(op, b._outline(rect), 0.2)
        b.f.create_entity(
            "IfcRelVoidsElement",
            GlobalId=guid.new(),
            RelatingBuildingElement=slab,
            RelatedOpeningElement=op,
        )
    path = tmp_path / "atrium.ifc"
    b.write(path)
    model = import_ifc(str(path))
    assert len(model.spaces) == 1
    (atr,) = model.spaces.values()
    assert atr.name == "Lobby" and len(atr.merged_from) == 2
    assert atr.height_m == pytest.approx(9.0)
    assert "atria: 2 room(s)" in model.revision_log[-1].note
