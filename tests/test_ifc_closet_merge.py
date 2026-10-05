"""IFC closets/shafts: name-only classification and IFC doors driving the merge (#574).

Closets and shafts in an IFC model come only from the IfcSpace Name/LongName
(Alex, 2026-10-05, option A); IfcDoor openings give space_merge the door that
settles the closet rule.
"""

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.api.spatial as _Sp  # noqa: E402
import ifcopenshell.guid as _guid  # noqa: E402

from bem_export import BEMModel, BEMSpace, write_ifc4  # noqa: E402
from ifc_import import _ifc_space_class, import_ifc  # noqa: E402
from tests.test_ifc_import import H, _add_solid, _rect_profile  # noqa: E402


class _Named:
    def __init__(self, name=None, long_name=None):
        self.Name, self.LongName = name, long_name


def _place(f, xyz, parent, refdir=None):
    kw = {
        "Location": f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in xyz))
    }
    if refdir:
        kw["Axis"] = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
        kw["RefDirection"] = f.create_entity(
            "IfcDirection", DirectionRatios=tuple(float(v) for v in refdir)
        )
    ax = f.create_entity("IfcAxis2Placement3D", **kw)
    return f.create_entity("IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=ax)


def _build(path, closet_name="CLOSET 102", closet_long=None, with_door=True, extra=()):
    """Office (0,0)-(4,3), closet (4.2,0)-(6,1.5), 0.2 m partition on x=4.1."""
    spaces = [
        BEMSpace(
            sid="a",
            name="OFFICE 101",
            number="101",
            polygon_m=[(0, 0), (4, 0), (4, 3), (0, 3)],
            area_m2=12.0,
            volume_m3=36.0,
        ),
        BEMSpace(
            sid="b",
            name=closet_name,
            number="102",
            polygon_m=[(4.2, 0), (6, 0), (6, 1.5), (4.2, 1.5)],
            area_m2=2.7,
            volume_m3=8.1,
        ),
        *extra,
    ]
    bem = BEMModel(
        building_name="T",
        spaces=spaces,
        openings=[],
        ring_m=[(0, 0), (6, 0), (6, 3), (0, 3)],
        wall_height_m=H,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
    )
    write_ifc4(bem, path)
    f = ifcopenshell.open(str(path))
    body = [
        c
        for c in f.by_type("IfcGeometricRepresentationSubContext")
        if c.ContextIdentifier == "Body"
    ][0]
    storey = f.by_type("IfcBuildingStorey")[0]
    by = {s.Name: s for s in f.by_type("IfcSpace")}
    if closet_long:
        by[closet_name].LongName = closet_long
    wall = f.create_entity("IfcWall", GlobalId=_guid.new(), Name="Partition")
    wall.ObjectPlacement = _place(f, (4.1, 0.0, 0.0), storey.ObjectPlacement, refdir=(0, 1, 0))
    _add_solid(f, wall, _rect_profile(f, 3.0, 0.2, 1.5, 0.0), H, body)
    prods = [wall]
    if with_door:
        op = f.create_entity("IfcOpeningElement", GlobalId=_guid.new(), Name="Op")
        op.ObjectPlacement = _place(f, (0.75, 0.0, 0.0), wall.ObjectPlacement)
        _add_solid(f, op, _rect_profile(f, 0.9, 0.2), 2.1, body)
        f.create_entity(
            "IfcRelVoidsElement",
            GlobalId=_guid.new(),
            RelatingBuildingElement=wall,
            RelatedOpeningElement=op,
        )
        door = f.create_entity(
            "IfcDoor", GlobalId=_guid.new(), Name="D1", OverallWidth=0.9, OverallHeight=2.1
        )
        door.ObjectPlacement = _place(f, (0, 0, 0), op.ObjectPlacement)
        _add_solid(f, door, _rect_profile(f, 0.9, 0.04), 2.1, body)
        f.create_entity(
            "IfcRelFillsElement",
            GlobalId=_guid.new(),
            RelatingOpeningElement=op,
            RelatedBuildingElement=door,
        )
        prods.append(door)
    _Sp.assign_container(f, products=prods, relating_structure=storey)
    f.write(str(path))
    return path


def _merge_reviews(model):
    return [it.description for it in model.review_queue if it.kind == "space_merge"]


@pytest.mark.parametrize(
    "name,long_name,expected",
    [
        ("CLOSET 102", None, "closet"),
        ("102", "Storage", "closet"),
        ("SHAFT", None, "shaft"),
        ("103", "Mech Chase", "shaft"),
        ("Storage Shaft 9", None, "shaft"),  # shaft wins
        ("OFFICE 101", None, None),
        ("PURCHASING 105", None, None),  # word match, not substring
        ("UTILITY 106", None, None),  # option A covers closet/storage/shaft/chase only
        (None, None, None),
    ],
)
def test_ifc_space_class_is_name_only(name, long_name, expected):
    got = _ifc_space_class(_Named(name, long_name))
    assert (got[0] if got else None) == expected


def test_ifc_door_merges_closet_into_room(tmp_path):
    model = import_ifc(_build(tmp_path / "t.ifc"))
    assert sorted(model.spaces) == ["L1-101"]
    office = model.spaces["L1-101"]
    assert office.merged_from == ["L1-102"]
    assert _merge_reviews(model) == []
    assert "space_merge: 1 merged, 0 kept for review" in model.revision_log[-1].note


def test_ifc_door_plan_centre_is_world_frame(tmp_path):
    model = import_ifc(_build(tmp_path / "t.ifc"))
    doors = [o for el in model.bim_elements for o in el.openings if o.category == "door"]
    assert len(doors) == 1
    assert doors[0].plan_center_m == pytest.approx([4.1, -0.75], abs=1e-3)


def test_closet_without_door_is_kept_for_review(tmp_path):
    model = import_ifc(_build(tmp_path / "t.ifc", with_door=False))
    assert sorted(model.spaces) == ["L1-101", "L1-102"]
    assert model.spaces["L1-102"].poly_type == "closet"
    assert model.spaces["L1-101"].merged_from == []
    reviews = _merge_reviews(model)
    assert len(reviews) == 1 and "no door" in reviews[0]


def test_storage_long_name_merges(tmp_path):
    model = import_ifc(_build(tmp_path / "t.ifc", closet_name="RM 102", closet_long="Storage"))
    assert model.spaces["L1-101"].merged_from == ["L1-102"]


def test_small_unnamed_space_is_never_merged_by_size(tmp_path):
    model = import_ifc(_build(tmp_path / "t.ifc", closet_name="RM 102"))
    assert sorted(model.spaces) == ["L1-101", "L1-102"]
    assert all(s.poly_type == "room" for s in model.spaces.values())
    assert _merge_reviews(model) == []
