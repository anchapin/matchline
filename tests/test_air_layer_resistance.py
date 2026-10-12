"""Unventilated air layers get ISO 6946 Table 2 resistance on IFC import (roadmap item 7)."""

from __future__ import annotations

import pytest

from materials import air_layer_resistance, is_air_name


def _no_u(model):
    """No U-value anywhere: at most an unset-U wall-type construction (#747)."""
    return all(
        c.u_value_w_m2k is None and c.provenance.method == "ifc_import:tier0:wall_type"
        for c in model.constructions.values()
    )


@pytest.mark.parametrize(
    "t_m, r",
    [(0.005, 0.11), (0.010, 0.15), (0.020, 0.175), (0.025, 0.18), (0.050, 0.18), (0.300, 0.18)],
)
def test_iso_6946_table_2_horizontal(t_m, r):
    assert air_layer_resistance(t_m) == pytest.approx(r)


@pytest.mark.parametrize("t_m", [0.0, -0.01, 0.31, float("nan"), "x"])
def test_outside_the_table_gives_none(t_m):
    assert air_layer_resistance(t_m) is None


def test_air_names():
    assert is_air_name("Air gap") and is_air_name("Cavity 50mm") and not is_air_name("Brick")


# --- import integration -----------------------------------------------------

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.guid  # noqa: E402

from ifc_export import _export_ifc  # noqa: E402
from ifc_import import RSE_WALL_M2K_W, RSI_WALL_M2K_W, import_ifc  # noqa: E402
from tests.model_factory import make_clean_model  # noqa: E402

BRICK = ("Brick", 0.10, None, False)


def _import(tmp_path, layers):
    """layers: (material name or None, thickness m, conductivity or None, IsVentilated)."""
    p = tmp_path / "b.ifc"
    _export_ifc(make_clean_model(), p)
    f = ifcopenshell.open(str(p))
    out = []
    for name, t, k, vent in layers:
        mat = f.create_entity("IfcMaterial", Name=name) if name is not None else None
        if mat is not None and k is not None:
            f.create_entity(
                "IfcMaterialProperties",
                Name="Pset_MaterialThermal",
                Material=mat,
                Properties=[
                    f.create_entity(
                        "IfcPropertySingleValue",
                        Name="ThermalConductivity",
                        NominalValue=f.create_entity("IfcThermalConductivityMeasure", k),
                    )
                ],
            )
        out.append(
            f.create_entity("IfcMaterialLayer", Material=mat, LayerThickness=t, IsVentilated=vent)
        )
    f.create_entity(
        "IfcRelAssociatesMaterial",
        GlobalId=ifcopenshell.guid.new(),
        RelatedObjects=f.by_type("IfcWall"),
        RelatingMaterial=f.create_entity("IfcMaterialLayerSet", MaterialLayers=out),
    )
    f.write(str(p))
    return import_ifc(p)


def _u(r_layers):
    return 1.0 / (RSI_WALL_M2K_W + RSE_WALL_M2K_W + r_layers)


def test_unknown_ventilation_gap_is_an_unventilated_air_layer(tmp_path):
    back = _import(tmp_path, [BRICK, (None, 0.05, None, "UNKNOWN"), BRICK])
    (c,) = back.constructions.values()
    assert c.id.startswith("IFC-UM")
    assert c.u_value_w_m2k == pytest.approx(_u(2 * 0.10 / 0.89 + 0.18), abs=1e-6)
    assert all(w.construction_id == c.id for w in back.envelope)


def test_air_named_layer_counts_as_air(tmp_path):
    back = _import(tmp_path, [BRICK, ("Air cavity", 0.010, None, False), BRICK])
    (c,) = back.constructions.values()
    assert c.u_value_w_m2k == pytest.approx(_u(2 * 0.10 / 0.89 + 0.15), abs=1e-6)


def test_provenance_cites_iso_6946_for_air(tmp_path):
    (c,) = _import(tmp_path, [BRICK, (None, 0.05, None, "UNKNOWN"), BRICK]).constructions.values()
    assert c.provenance.method == "ifc_import:tier0:wall_u_lookup"
    assert "air_iso6946" in c.provenance.note and "ISO 6946:2007 Table 2" in c.provenance.note


def test_ventilated_gap_still_gives_no_value(tmp_path):
    assert _no_u(_import(tmp_path, [BRICK, (None, 0.05, None, True), BRICK]))


def test_air_layer_thicker_than_0_3_m_gives_no_value(tmp_path):
    assert _no_u(_import(tmp_path, [BRICK, (None, 0.35, None, "UNKNOWN"), BRICK]))


def test_stated_conductivity_on_an_air_material_wins(tmp_path):
    layers = [
        ("Brick", 0.10, 0.89, False),
        ("Air", 0.05, 0.25, False),
        ("Brick", 0.10, 0.89, False),
    ]
    (c,) = _import(tmp_path, layers).constructions.values()
    assert c.id.startswith("IFC-UL")
    assert c.u_value_w_m2k == pytest.approx(_u(2 * 0.10 / 0.89 + 0.05 / 0.25), abs=1e-6)
