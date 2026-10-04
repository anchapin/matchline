"""Roof U-value carried through IFC import, gbXML and IFC4 export."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from building_model import BuildingModel, Construction
from constructions import roof_u_value
from tests.model_factory import make_clean_model


def _with_roof(u=0.25):
    m = make_clean_model()
    m.constructions["R-1"] = Construction(id="R-1", name="roof", u_value_w_m2k=u)
    m.roof_construction_id = "R-1"
    return m


def test_roof_u_value_helper():
    assert roof_u_value(make_clean_model()) is None
    assert roof_u_value(_with_roof(0.25)) == pytest.approx(0.25)
    assert roof_u_value(_with_roof(None)) is None
    m = make_clean_model()
    m.roof_construction_id = "missing"
    assert roof_u_value(m) is None


def test_json_round_trip_keeps_roof_construction():
    back = BuildingModel.from_json(_with_roof().to_json())
    assert back.roof_construction_id == "R-1" and roof_u_value(back) == pytest.approx(0.25)


def _gbxml_roof(tmp_path, model):
    from bem_export import write_gbxml
    from ifc_export import _bem_from_model

    path = write_gbxml(_bem_from_model(model), tmp_path / "m.xml")
    ns = {"g": "http://www.gbxml.org/schema"}
    co = ET.parse(path).getroot().find("g:Construction[@id='const-roof']", ns)
    return co.find("g:Name", ns).text, float(co.find("g:U-value", ns).text)


def test_gbxml_roof_uses_model_u(tmp_path):
    name, u = _gbxml_roof(tmp_path, _with_roof(0.25))
    assert u == pytest.approx(0.25) and "from model" in name


def test_gbxml_roof_generic_without_model_u(tmp_path):
    assert _gbxml_roof(tmp_path, make_clean_model()) == ("Generic roof", pytest.approx(0.30))


ifcopenshell = pytest.importorskip("ifcopenshell")

import ifcopenshell.api.pset as ifc_pset  # noqa: E402
import ifcopenshell.api.root as ifc_root  # noqa: E402
import ifcopenshell.util.element as ifc_el  # noqa: E402

from ifc_export import _export_ifc  # noqa: E402
from ifc_import import import_ifc  # noqa: E402


def _roof_slabs(path):
    # Return the file too: entities die with it if it is garbage collected.
    f = ifcopenshell.open(str(path))
    return f, [s for s in f.by_type("IfcSlab") if s.PredefinedType == "ROOF"]


def test_ifc4_roof_slab_carries_u(tmp_path):
    p = _export_ifc(_with_roof(0.25), tmp_path / "r.ifc")
    _f, (slab,) = _roof_slabs(p)
    psets = ifc_el.get_psets(slab)
    assert psets["Pset_SlabCommon"]["ThermalTransmittance"] == pytest.approx(0.25)
    assert psets["Pset_SlabCommon"]["IsExternal"] is True


def test_no_roof_u_no_skylights_writes_no_roof_slab(tmp_path):
    assert _roof_slabs(_export_ifc(make_clean_model(), tmp_path / "n.ifc"))[1] == []


def test_round_trip_roof_u(tmp_path):
    back = import_ifc(_export_ifc(_with_roof(0.25), tmp_path / "r.ifc"))
    c = back.constructions[back.roof_construction_id]
    assert c.id == "IFC-RU0.2500" and c.u_value_w_m2k == pytest.approx(0.25)
    assert c.provenance.method == "ifc_import:tier0:roof_u" and c.provenance.confidence == 0.9
    assert roof_u_value(back) == pytest.approx(0.25)


def _add_roof(f, cls, u, pset):
    kw = {"predefined_type": "ROOF"} if cls == "IfcSlab" else {}
    el = ifc_root.create_entity(f, ifc_class=cls, name="R", **kw)
    ps = ifc_pset.add_pset(f, product=el, name=pset)
    ifc_pset.edit_pset(f, pset=ps, properties={"ThermalTransmittance": u})
    return el


def test_ifcroof_pset_roofcommon_is_read(tmp_path):
    p = _export_ifc(make_clean_model(), tmp_path / "a.ifc")
    f = ifcopenshell.open(str(p))
    _add_roof(f, "IfcRoof", 0.18, "Pset_RoofCommon")
    f.write(str(p))
    back = import_ifc(p)
    assert back.roof_construction_id == "IFC-RU0.1800"


def test_disagreeing_roofs_stay_generic_and_say_why(tmp_path):
    p = _export_ifc(_with_roof(0.25), tmp_path / "d.ifc")
    f = ifcopenshell.open(str(p))
    _add_roof(f, "IfcSlab", 0.40, "Pset_SlabCommon")
    f.write(str(p))
    back = import_ifc(p)
    assert back.roof_construction_id == ""
    assert not any(cid.startswith("IFC-RU") for cid in back.constructions)
    assert "roof elements state different U-values" in back.revision_log[-1].note
