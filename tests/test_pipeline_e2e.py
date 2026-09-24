"""End-to-end pipeline test: generate + build_model + simplify + validate.

Verifies that for seeds 101, 102, 103 the full pipeline produces a valid
model with correct areas and passes all validation checks.

Uses the same fixture infrastructure as tests/conftest.py (bldg_3room etc.).
"""

from pathlib import Path

import pytest

import run_pipeline
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
    """Pipeline -> BEMModel -> write_gbxml -> validate_gbxml -> run_checks passes BATTERY."""
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
        simplify_tolerance=2.0,
    )

    gbxml_path = tmp_path / f"seed_{seed}.xml"
    write_gbxml(bem_model, gbxml_path)

    ok, errors = validate_gbxml(gbxml_path)
    assert ok, f"seed={seed}: gbXML validation errors: {errors}"
    assert errors == [], f"seed={seed}: expected no errors, got {errors}"

    check_report = run_checks(bem_model, sres=sres)
    assert check_report.ok, (
        f"seed={seed}: BEM validation errors: {[e.message for e in check_report.errors]}"
    )
    assert export_gate(check_report), f"seed={seed}: export gate closed"


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
        simplify_tolerance=2.0,
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


def test_pipeline_e2e_output_correctness(tmp_path: Path):
    """Run matchline run on bldg_3room fixture and assert output correctness invariants.

    Verifies:
    - Floor areas sum correctly (area conservation)
    - Zones are non-overlapping (no polygon intersections)
    - Window-wall ratios are within bounds (0.1-0.9 for each space)
    - Provenance is present on every extracted fact
    """
    import argparse
    import json as _json

    from shapely.geometry import MultiPolygon, Polygon
    from shapely.validation import make_valid

    from building_model import BuildingModel

    out_dir = tmp_path / "run_output"
    out_dir.mkdir()
    weights = Path(__file__).parent.parent / "weights.pt"

    args = argparse.Namespace(
        seed=101,
        image=None,
        aec_bench=None,
        detections=None,
        schedule_csv=None,
        weights=weights,
        out_dir=out_dir,
        open_office_span=False,
        elevation_key="elev_grid",
        simplify_tol=0.01,
    )
    run_pipeline.main(args, config=None)

    model_path = out_dir / "stage_02_model.json"
    assert model_path.exists(), f"stage_02_model.json not found in {out_dir}"
    d = _json.loads(model_path.read_text())
    m = BuildingModel.from_dict(d)

    run_checks(m)

    for space in m.spaces.values():
        assert space.core_provenance is not None, f"Space {space.id} missing core_provenance"
        for opening in space.openings:
            assert opening.provenance is not None, (
                f"Space {space.id} opening {opening.id} missing provenance"
            )

    for zone in m.zones.values():
        assert zone.provenance is not None, f"Zone {zone.id} missing provenance"

    total_floor_area = sum(s.floor_area for s in m.spaces.values())
    zone_area_sum = sum(z.floor_area for z in m.zones.values())
    assert abs(total_floor_area - zone_area_sum) < 1e-6, (
        f"zone area sum ({zone_area_sum:.4f}) must equal total floor area ({total_floor_area:.4f})"
    )

    zones_by_level: dict[str, list[tuple]] = {}
    for z in m.zones.values():
        level_id = z.level_id
        if level_id not in zones_by_level:
            zones_by_level[level_id] = []
        poly = make_valid(Polygon(z.polygon.exterior.coords))
        zones_by_level[level_id].append((z, poly))

    for level_id, zones in zones_by_level.items():
        for i, (z1, p1) in enumerate(zones):
            for z2, p2 in zones[i + 1 :]:
                if p1.intersects(p2) and not p1.touches(p2):
                    inter = p1.intersection(p2)
                    area = inter.area if isinstance(inter, (Polygon, MultiPolygon)) else 0
                    assert area < 1e-6, (
                        f"zones {z1.id} and {z2.id} on level {level_id} overlap "
                        f"with area {area:.6f}"
                    )

    for s in m.spaces.values():
        if s.window_area is not None and s.wall_area is not None and s.wall_area > 0:
            wwr = s.window_area / s.wall_area
            assert 0.1 <= wwr <= 0.9, (
                f"space {s.id} has WWR {wwr:.3f} outside bounds [0.1, 0.9] "
                f"(window={s.window_area:.4f}, wall={s.wall_area:.4f})"
            )
