"""import_ifc derives wall U from IfcMaterialLayerSet + Pset_MaterialThermal (roadmap item 7)."""

from __future__ import annotations

import pytest

pytest.importorskip("ifcopenshell")

import ifcopenshell  # noqa: E402
import ifcopenshell.guid  # noqa: E402

from constructions import apply_wall_u_rollup  # noqa: E402
from ifc_export import _export_ifc  # noqa: E402
from ifc_import import RSE_WALL_M2K_W, RSI_WALL_M2K_W, import_ifc  # noqa: E402
from tests.model_factory import add_wall_constructions, make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402

# (name, thickness m, conductivity W/mK or None, ventilated)
WALL = [
    ("Brick", 0.10, 0.77, False),
    ("Mineral wool", 0.10, 0.035, False),
    ("Gypsum", 0.0125, 0.25, False),
]


def _no_u(model):
    """No U-value anywhere: at most an unset-U wall-type construction (#747)."""
    return all(
        c.u_value_w_m2k is None and c.provenance.method == "ifc_import:tier0:wall_type"
        for c in model.constructions.values()
    )


def _u(layers):
    return 1.0 / (RSI_WALL_M2K_W + RSE_WALL_M2K_W + sum(t / k for _, t, k, _ in layers))


def _layer_set(f, layers):
    out = []
    for name, t, k, vent in layers:
        mat = f.create_entity("IfcMaterial", Name=name)
        if k is not None:
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
    return f.create_entity("IfcMaterialLayerSet", MaterialLayers=out, LayerSetName="wall")


def _import(tmp_path, layers=WALL, model=None, sets=1):
    p = tmp_path / "b.ifc"
    _export_ifc(model or make_clean_model(), p)
    f = ifcopenshell.open(str(p))
    walls = f.by_type("IfcWall")
    for _ in range(sets):
        f.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=ifcopenshell.guid.new(),
            RelatedObjects=walls,
            RelatingMaterial=_layer_set(f, layers),
        )
    f.write(str(p))
    return import_ifc(p)


def test_u_matches_iso_6946_hand_calculation(tmp_path):
    back = _import(tmp_path)
    (c,) = back.constructions.values()
    assert c.id.startswith("IFC-UL")
    assert c.u_value_w_m2k == pytest.approx(_u(WALL), abs=1e-6)
    assert c.u_value_w_m2k == pytest.approx(0.312, abs=0.001)


def test_every_exterior_wall_and_space_gets_it(tmp_path):
    back = _import(tmp_path)
    assert back.envelope
    assert {w.construction_id for w in back.envelope} == set(back.constructions)
    for sp in back.spaces.values():
        assert sp.wall_u_value_w_m2k == pytest.approx(_u(WALL), abs=1e-6)


def test_provenance_names_the_derivation(tmp_path):
    (c,) = _import(tmp_path).constructions.values()
    assert c.provenance.method == "ifc_import:tier0:wall_u_layers"
    assert c.provenance.confidence == pytest.approx(0.8)
    assert "GlobalId=" in c.provenance.note and "ISO 6946" in c.provenance.note


def test_coverage_check_passes(tmp_path):
    back = _import(tmp_path)
    res = next(r for r in run_checks(back).results if r.check_id == "wall_construction_coverage")
    assert res.severity == "pass"


def test_one_layer_without_conductivity_means_no_value(tmp_path):
    layers = WALL[:1] + [("Mystery board", 0.05, None, False)] + WALL[1:]
    back = _import(tmp_path, layers)
    assert _no_u(back)
    assert all(
        w.construction_id == "" or w.construction_id.startswith("IFC-TYPE-") for w in back.envelope
    )


def test_ventilated_layer_means_no_value(tmp_path):
    layers = WALL[:1] + [("Air", 0.05, 0.026, True)] + WALL[1:]
    assert _no_u(_import(tmp_path, layers))


def test_two_layer_sets_are_ambiguous(tmp_path):
    assert _no_u(_import(tmp_path, sets=2))


def test_stated_thermal_transmittance_wins(tmp_path):
    m = make_clean_model()
    add_wall_constructions(m)
    apply_wall_u_rollup(m)
    back = _import(tmp_path, model=m)
    assert back.constructions
    assert all(
        c.id.startswith("IFC-U") and not c.id.startswith("IFC-UL")
        for c in back.constructions.values()
    )
