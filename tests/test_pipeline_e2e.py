"""End-to-end pipeline test: generate + build_model + simplify + validate.

Verifies that for seeds 101, 102, 103 the full pipeline produces a valid
model with correct areas and passes all validation checks.

Uses the same fixture infrastructure as tests/conftest.py (bldg_3room etc.).
"""

import pytest

from geometry_simplify import footprint_from_regions, simplify_ring
from link import build_model
from run_pipeline import model_from_linked_model
from synth.multidiscipline import generate_building
from validate import export_gate, run_checks

TOL = 0.01  # 1% area tolerance


@pytest.mark.parametrize("seed", [101, 102, 103])
def test_pipeline_e2e_area_tolerance(seed):
    """Full pipeline: generate + build + simplify + validate, asserting 1% area tolerance."""
    bldg = generate_building(seed, open_office_span=False)
    model, report = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])

    sres = simplify_ring(
        footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
        tol=0.02,
        wall_height=model.levels[0].wall_height_m,
    )
    assert abs(sres.area_delta_pct) / 100.0 <= TOL, (
        f"seed={seed}: simplification area delta {sres.area_delta_pct:.3f}% exceeds 1%"
    )

    check_report = run_checks(model, sres=sres)
    assert check_report.ok, (
        f"seed={seed}: validation errors: {[e.message for e in check_report.errors]}"
    )
    assert export_gate(check_report), f"seed={seed}: export gate closed"


@pytest.mark.parametrize("seed", [101, 102, 103])
def test_pipeline_e2e_bem_export_roundtrip(seed, tmp_path):
    """Pipeline -> BEMModel -> write_gbxml -> validate_gbxml returns (True, [])."""
    from bem_export import validate_gbxml, write_gbxml

    bldg = generate_building(seed, open_office_span=False)
    model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
    sres = simplify_ring(
        footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
        tol=0.02,
        wall_height=model.levels[0].wall_height_m,
    )

    bem_model = model_from_linked_model(
        model=model,
        simplified_ring=sres.ring,
        wall_height_m=model.levels[0].wall_height_m,
        simplify_tol_pct=2.0,
    )

    gbxml_path = tmp_path / f"seed_{seed}.xml"
    write_gbxml(bem_model, gbxml_path)

    ok, errors = validate_gbxml(gbxml_path)
    assert ok, f"seed={seed}: gbXML validation errors: {errors}"
    assert errors == [], f"seed={seed}: expected no errors, got {errors}"


def test_pipeline_e2e_bldg_3room_full(bldg_3room, tmp_path):
    """Full pipeline on bldg_3room fixture: gbXML output, validate exits 0, provenance attached."""
    bldg, model, link_report = bldg_3room

    sres = simplify_ring(
        footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
        tol=0.02,
        wall_height=model.levels[0].wall_height_m,
    )

    bem_model = model_from_linked_model(
        model=model,
        simplified_ring=sres.ring,
        wall_height_m=model.levels[0].wall_height_m,
        simplify_tol_pct=2.0,
    )

    gbxml_path = tmp_path / "bldg_3room.xml"
    from bem_export import write_gbxml

    write_gbxml(bem_model, gbxml_path)

    assert gbxml_path.exists(), "gbXML file was not produced"
    assert gbxml_path.stat().st_size > 0, "gbXML file is empty"

    check_report = run_checks(model, sres=sres)
    assert check_report.ok, f"validation errors: {[e.message for e in check_report.errors]}"
    assert export_gate(check_report), "export gate closed"

    for space in model.spaces.values():
        assert space.core_provenance is not None, f"Space {space.id} missing core_provenance"
        for opening in space.openings:
            assert opening.provenance is not None, (
                f"Space {space.id} opening {opening.id} missing provenance"
            )

    for zone in model.zones.values():
        assert zone.provenance is not None, f"Zone {zone.id} missing provenance"
