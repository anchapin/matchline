"""IfcDoor OperationType and Pset_DoorCommon.GlazingAreaFraction on import (#573)."""

import pytest

ifcopenshell = pytest.importorskip("ifcopenshell")
api = pytest.importorskip("ifcopenshell.api")

from ifc_import import import_ifc  # noqa: E402
from tests.test_ifc_closet_merge import _build  # noqa: E402


def _door_ifc(tmp_path, op=None, frac=None, type_op=None, type_frac=None):
    path = _build(tmp_path / "base.ifc")
    f = ifcopenshell.open(str(path))
    door = f.by_type("IfcDoor")[0]
    if op is not None:
        door.OperationType = op
    if type_op is not None or type_frac is not None:
        typ = f.create_entity("IfcDoorType", GlobalId=ifcopenshell.guid.new(), Name="DT",
                              PredefinedType="DOOR", OperationType=type_op or "NOTDEFINED")
        f.create_entity("IfcRelDefinesByType", GlobalId=ifcopenshell.guid.new(),
                        RelatedObjects=[door], RelatingType=typ)
        if type_frac is not None:
            ps = api.run("pset.add_pset", f, product=typ, name="Pset_DoorCommon")
            api.run("pset.edit_pset", f, pset=ps, properties={"GlazingAreaFraction": type_frac})
    if frac is not None:
        ps = api.run("pset.add_pset", f, product=door, name="Pset_DoorCommon")
        api.run("pset.edit_pset", f, pset=ps, properties={"GlazingAreaFraction": frac})
    out = tmp_path / "door.ifc"
    f.write(str(out))
    return out


def _door(model):
    doors = [o for e in model.bim_elements for o in e.openings if o.category == "door"]
    assert len(doors) == 1
    return doors[0]


@pytest.mark.parametrize(
    "op,leaves,hinge",
    [
        ("SINGLE_SWING_LEFT", 1, "left"),
        ("SINGLE_SWING_RIGHT", 1, "right"),
        ("DOUBLE_DOOR_SINGLE_SWING", 2, None),
        ("DOUBLE_SWING_RIGHT", 1, "right"),
        ("SLIDING_TO_LEFT", 1, None),
        ("DOUBLE_DOOR_SLIDING", 2, None),
        ("REVOLVING", None, None),
        ("NOTDEFINED", None, None),
    ],
)
def test_operation_type(tmp_path, op, leaves, hinge):
    d = _door(import_ifc(_door_ifc(tmp_path, op=op)))
    assert (d.operation_type, d.leaf_count, d.hinge_side) == (op, leaves, hinge)


def test_glazing_fraction_gives_glazed_area(tmp_path):
    d = _door(import_ifc(_door_ifc(tmp_path, op="SINGLE_SWING_LEFT", frac=0.5)))
    assert d.glazing_area_fraction == 0.5
    assert d.glazed_area_m2 == pytest.approx(0.5 * d.width_m * d.height_m, abs=1e-3)
    assert "GlazingAreaFraction 0.5 from Pset_DoorCommon" in d.provenance.note


def test_no_pset_no_glazing(tmp_path):
    d = _door(import_ifc(_door_ifc(tmp_path)))
    assert d.glazing_area_fraction is None and d.glazed_area_m2 is None
    assert d.operation_type is None and d.leaf_count is None and d.hinge_side is None


def test_out_of_range_fraction_ignored(tmp_path):
    d = _door(import_ifc(_door_ifc(tmp_path, frac=1.7)))
    assert d.glazing_area_fraction is None
    assert "out of 0..1" in d.provenance.note


def test_type_supplies_operation_and_glazing(tmp_path):
    d = _door(import_ifc(_door_ifc(tmp_path, type_op="DOUBLE_DOOR_DOUBLE_SWING", type_frac=0.25)))
    assert (d.operation_type, d.leaf_count) == ("DOUBLE_DOOR_DOUBLE_SWING", 2)
    assert d.glazing_area_fraction == 0.25
    assert "from type" in d.provenance.note


def test_occurrence_overrides_type_glazing(tmp_path):
    d = _door(import_ifc(_door_ifc(tmp_path, type_op="SINGLE_SWING_LEFT", type_frac=0.25, frac=0.4)))
    assert d.glazing_area_fraction == 0.4
