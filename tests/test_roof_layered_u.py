"""Roof U derived from IfcMaterialLayerSet, ISO 6946 upward heat flow."""

from __future__ import annotations

import pytest

from materials import air_layer_resistance, lookup_conductivity

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.guid  # noqa: E402

from constructions import roof_u_value  # noqa: E402
from ifc_export import _export_ifc  # noqa: E402
from ifc_import import (  # noqa: E402
    RSE_ROOF_M2K_W,
    RSI_ROOF_M2K_W,
    RSI_WALL_M2K_W,
    import_ifc,
)
from tests.model_factory import make_clean_model  # noqa: E402
from tests.test_ifc_import_layered_wall_u import _layer_set  # noqa: E402

# (name, thickness m, conductivity W/mK or None, ventilated)
ROOF = [
    ("Concrete deck", 0.20, 1.4, False),
    ("Roof insulation", 0.10, 0.034, False),
    ("Ceiling", 0.0125, 0.25, False),
]


def _r(layers):
    return sum(t / k for _, t, k, _ in layers)


def _import(tmp_path, *roofs, ifcroof=False, stated=None):
    """Export a clean model, add one IfcSlab ROOF per layer list, import it."""
    p = tmp_path / "r.ifc"
    _export_ifc(make_clean_model(), p)
    f = ifcopenshell.open(str(p))
    if ifcroof:
        f.create_entity("IfcRoof", GlobalId=ifcopenshell.guid.new(), Name="Roof")
    for layers in roofs:
        slab = f.create_entity(
            "IfcSlab", GlobalId=ifcopenshell.guid.new(), Name="Roof", PredefinedType="ROOF"
        )
        f.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=ifcopenshell.guid.new(),
            RelatedObjects=[slab],
            RelatingMaterial=_layer_set(f, layers),
        )
        if stated is not None:
            import ifcopenshell.api.pset as ifc_pset

            ps = ifc_pset.add_pset(f, product=slab, name="Pset_SlabCommon")
            ifc_pset.edit_pset(f, pset=ps, properties={"ThermalTransmittance": stated})
    f.write(str(p))
    return import_ifc(p)


def _roof(back):
    return back.constructions[back.roof_construction_id]


def test_roof_u_matches_iso_6946_upward_hand_calculation(tmp_path):
    back = _import(tmp_path, ROOF)
    c = _roof(back)
    expected = 1.0 / (0.10 + 0.04 + _r(ROOF))
    assert (RSI_ROOF_M2K_W, RSE_ROOF_M2K_W) == (0.10, 0.04)
    assert c.id == f"IFC-RUL{expected:.4f}"
    assert c.u_value_w_m2k == pytest.approx(expected, abs=1e-6)
    assert c.provenance.method == "ifc_import:tier0:roof_u_layers"
    assert c.provenance.confidence == 0.8
    assert roof_u_value(back) == pytest.approx(expected, abs=1e-6)


def test_roof_uses_upward_not_wall_surface_resistance(tmp_path):
    wall_style = 1.0 / (RSI_WALL_M2K_W + 0.04 + _r(ROOF))
    assert _roof(_import(tmp_path, ROOF)).u_value_w_m2k > wall_style


def test_roof_conductivity_lookup_tier(tmp_path):
    layers = [("Concrete", 0.20, None, False), ("XPS", 0.10, None, False)]
    c = _roof(_import(tmp_path, layers))
    k = [lookup_conductivity(n).conductivity_w_mk for n, *_ in layers]
    expected = 1.0 / (0.14 + 0.20 / k[0] + 0.10 / k[1])
    assert c.id.startswith("IFC-RUM")
    assert c.u_value_w_m2k == pytest.approx(expected, abs=1e-6)
    assert c.provenance.method == "ifc_import:tier0:roof_u_lookup"
    assert c.provenance.confidence == 0.6


def test_roof_air_layer_uses_upward_column(tmp_path):
    assert air_layer_resistance(0.05, "upward") == pytest.approx(0.16)
    assert air_layer_resistance(0.05) == pytest.approx(0.18)
    layers = ROOF + [("Air", 0.05, None, "UNKNOWN")]
    c = _roof(_import(tmp_path, layers))
    assert c.u_value_w_m2k == pytest.approx(1.0 / (0.14 + _r(ROOF) + 0.16), abs=1e-6)
    assert "upward heat flow" in c.provenance.note


def test_unknown_direction_raises():
    with pytest.raises(ValueError):
        air_layer_resistance(0.05, "sideways")


def test_stated_roof_u_wins_over_layers(tmp_path):
    c = _roof(_import(tmp_path, ROOF, stated=0.2))
    assert c.id == "IFC-RU0.2000" and c.provenance.confidence == 0.9


def test_underivable_roof_element_leaves_roof_generic(tmp_path):
    back = _import(tmp_path, ROOF, [("Mystery stuff", 0.1, None, False)])
    assert back.roof_construction_id == ""
    assert "not derivable for 1 roof element" in back.revision_log[-1].note


def test_ventilated_roof_layer_gives_no_value(tmp_path):
    back = _import(tmp_path, ROOF + [("Air", 0.05, None, True)])
    assert back.roof_construction_id == ""


def test_disagreeing_roof_layer_sets_stay_generic(tmp_path):
    thinner = [("Concrete deck", 0.15, 1.4, False)] + ROOF[1:]
    back = _import(tmp_path, ROOF, thinner)
    assert back.roof_construction_id == ""
    assert "roof layer sets give different U-values" in back.revision_log[-1].note


def test_ifcroof_without_layers_is_skipped(tmp_path):
    back = _import(tmp_path, ROOF, ifcroof=True)
    assert back.roof_construction_id.startswith("IFC-RUL")
