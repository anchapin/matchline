"""IFC wall type and layer names feed the cited construction library (#747)."""

from __future__ import annotations

from types import SimpleNamespace

from building_model import BuildingModel, EnvelopeWall
from construction_library import apply_construction_library
from ifc_import import _layer_class, _wall_type_construction


def _rel(kind, **kw):
    return SimpleNamespace(is_a=lambda k, _kind=kind: k == _kind, **kw)


def _wall(object_type="", layers=()):
    mats = [SimpleNamespace(Material=SimpleNamespace(Name=n), LayerThickness=0.05) for n in layers]
    lset = _rel("IfcMaterialLayerSet", MaterialLayers=mats)
    assoc = [_rel("IfcRelAssociatesMaterial", RelatingMaterial=lset)] if layers else []
    return SimpleNamespace(ObjectType=object_type, HasAssociations=assoc, IsDefinedBy=[])


def _prov(method, conf, note, gid):
    return SimpleNamespace(method=method, confidence=conf, note=note, source=gid)


def test_type_name_names_the_class():
    m = BuildingModel(name="t")
    w = _wall(
        "Basic Wall:Exterior - Insul Panel on Mtl. Stud:130", ["Insulation - Insulated Panel"]
    )
    cid = _wall_type_construction(m, w, 1.0, _prov, "g1")
    assert cid == "IFC-TYPE-basic-wall-exterior-insul-panel-on-mtl-stud-130"
    c = m.constructions[cid]
    assert c.u_value_w_m2k is None
    assert c.provenance.method == "ifc_import:tier0:wall_type"
    assert "SteelFramed" in c.provenance.note and "type name" in c.provenance.note


def test_layers_name_the_class_when_the_type_does_not():
    m = BuildingModel(name="t")
    w = _wall("Basic Wall:EW-1", ["Plasterboard", "Metal - Stud Layer", "Plasterboard"])
    cid = _wall_type_construction(m, w, 1.0, _prov, "g1")
    assert cid.startswith("IFC-TYPE-")
    assert "metal stud layer" in m.constructions[cid].name
    assert "IFC material layers" in m.constructions[cid].provenance.note


def test_stud_furring_on_mass_is_ambiguous_and_left_unassigned():
    ctype, why = _layer_class(["Concrete Masonry Units", "Metal - Stud Layer", "Plasterboard"])
    assert ctype is None and "more than one class" in why
    m = BuildingModel(name="t")
    w = _wall("Basic Wall:EW-2", ["Concrete Masonry Units", "Metal - Stud Layer"])
    assert _wall_type_construction(m, w, 1.0, _prov, "g1") == ""
    assert not m.constructions


def test_nothing_named_returns_empty():
    m = BuildingModel(name="t")
    assert _wall_type_construction(m, _wall("Generic - 200mm", ["Default"]), 1.0, _prov, "g") == ""


def test_library_resolves_the_type_construction_and_baseline_skips_it():
    m = BuildingModel(name="t")
    cid = _wall_type_construction(
        m, _wall("Basic Wall:Exterior - Brick on Mtl. Stud"), 1.0, _prov, "g1"
    )
    m.envelope.append(EnvelopeWall(id="w1", facade="south", length_m=10.0, construction_id=cid))
    m.envelope.append(EnvelopeWall(id="w2", facade="north", length_m=10.0, construction_id=""))
    s = apply_construction_library(m, "5A")
    assert s.resolved[cid]["construction_type"] == "SteelFramed"
    assert m.constructions[cid].u_value_w_m2k == s.resolved[cid]["u_si"]
    assert "mtl. stud" in s.resolved[cid]["why"]
    assert s.defaulted["t55-wall"]["segments"] == ["w2"]


def test_no_climate_zone_leaves_it_unset_and_reported():
    m = BuildingModel(name="t")
    cid = _wall_type_construction(m, _wall("Exterior - Mtl. Stud"), 1.0, _prov, "g1")
    s = apply_construction_library(m, "")
    assert m.constructions[cid].u_value_w_m2k is None
    assert s.unmatched[cid] == "no climate zone given"
