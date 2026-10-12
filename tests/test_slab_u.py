"""Ground slab U-value through IFC import, gbXML and IFC4 export."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from building_model import BuildingModel, Construction
from constructions import roof_u_value, slab_u_value
from tests.model_factory import make_clean_model


def _with_slab(u=0.30, roof=None):
    m = make_clean_model()
    m.constructions["S-1"] = Construction(id="S-1", name="slab", u_value_w_m2k=u)
    m.slab_construction_id = "S-1"
    if roof is not None:
        m.constructions["R-1"] = Construction(id="R-1", name="roof", u_value_w_m2k=roof)
        m.roof_construction_id = "R-1"
    return m


def test_slab_u_value_helper():
    assert slab_u_value(make_clean_model()) is None
    assert slab_u_value(_with_slab(0.3)) == pytest.approx(0.3)
    assert slab_u_value(_with_slab(None)) is None


def test_json_round_trip_keeps_slab_construction():
    back = BuildingModel.from_json(_with_slab().to_json())
    assert back.slab_construction_id == "S-1" and slab_u_value(back) == pytest.approx(0.3)


def _gbxml_slab(tmp_path, model):
    from bem_export import write_gbxml
    from ifc_export import _bem_from_model

    path = write_gbxml(_bem_from_model(model), tmp_path / "m.xml")
    ns = {"g": "http://www.gbxml.org/schema"}
    co = ET.parse(path).getroot().find("g:Construction[@id='const-slab']", ns)
    return co.find("g:Name", ns).text, float(co.find("g:U-value", ns).text)


def test_gbxml_slab_uses_model_u(tmp_path):
    name, u = _gbxml_slab(tmp_path, _with_slab(0.3))
    assert u == pytest.approx(0.3) and "from model" in name


def test_gbxml_slab_generic_without_model_u(tmp_path):
    assert _gbxml_slab(tmp_path, make_clean_model()) == (
        "Generic slab on grade",
        pytest.approx(0.40),
    )


ifcopenshell = pytest.importorskip("ifcopenshell")

import ifcopenshell.api.pset as ifc_pset  # noqa: E402
import ifcopenshell.api.root as ifc_root  # noqa: E402
import ifcopenshell.util.element as ifc_el  # noqa: E402

from bem_ifc4 import validate_ifc4  # noqa: E402
from ifc_export import _export_ifc  # noqa: E402
from ifc_import import import_ifc  # noqa: E402


def _baseslabs(path):
    f = ifcopenshell.open(str(path))  # returned too: entities die with the file
    return f, [s for s in f.by_type("IfcSlab") if s.PredefinedType == "BASESLAB"]


def test_ifc4_writes_baseslab_with_u(tmp_path):
    p = _export_ifc(_with_slab(0.3), tmp_path / "s.ifc")
    _f, (slab,) = _baseslabs(p)
    ps = ifc_el.get_psets(slab)["Pset_SlabCommon"]
    assert ps["ThermalTransmittance"] == pytest.approx(0.3) and ps["IsExternal"] is True
    assert validate_ifc4(p)[0]


def test_no_slab_u_writes_no_baseslab(tmp_path):
    assert _baseslabs(_export_ifc(make_clean_model(), tmp_path / "n.ifc"))[1] == []


def test_round_trip_slab_and_roof_independent(tmp_path):
    back = import_ifc(_export_ifc(_with_slab(0.3, roof=0.2), tmp_path / "b.ifc"))
    c = back.constructions[back.slab_construction_id]
    assert c.id == "IFC-SU0.3000" and c.u_value_w_m2k == pytest.approx(0.3)
    assert c.provenance.method == "ifc_import:tier0:slab_u" and c.provenance.confidence == 0.9
    assert slab_u_value(back) == pytest.approx(0.3)
    assert roof_u_value(back) == pytest.approx(0.2)


def _add_slab(f, ptype, u):
    el = ifc_root.create_entity(f, ifc_class="IfcSlab", name="S", predefined_type=ptype)
    ps = ifc_pset.add_pset(f, product=el, name="Pset_SlabCommon")
    ifc_pset.edit_pset(f, pset=ps, properties={"ThermalTransmittance": u})


def test_disagreeing_ground_slabs_stay_generic(tmp_path):
    p = _export_ifc(_with_slab(0.3), tmp_path / "d.ifc")
    f = ifcopenshell.open(str(p))
    _add_slab(f, "BASESLAB", 0.5)
    f.write(str(p))
    back = import_ifc(p)
    assert back.slab_construction_id == ""
    assert "ground slabs state different U-values" in back.revision_log[-1].note


def test_floor_slab_is_not_taken_as_ground(tmp_path):
    p = _export_ifc(make_clean_model(), tmp_path / "fl.ifc")
    f = ifcopenshell.open(str(p))
    _add_slab(f, "FLOOR", 0.5)
    f.write(str(p))
    assert import_ifc(p).slab_construction_id == ""
