"""IFC export tests: entity-count contract verified by _check_ifc_counts.

Tests the export path in isolation (without the import half of the round-trip),
focusing on the invariant that _check_ifc_counts enforces:
  - IFC file has n IfcSpace when the model has n spaces
  - IFC file has at least one IfcWall

Happy path: export produces a valid IFC with correct entity counts.
Defect injection: model mismatch triggers _check_ifc_counts error.
"""

import pytest

from building_model import (
    BuildingModel,
    EnvelopeWall,
    Level,
    Provenance,
    Space,
    SpaceLighting,
)
from datasets_adapter import polygon_area_px2
from ifc_export import _export_ifc
from tests.conftest import _linked
from validate import _build_ctx, _check_ifc_counts, run_checks

_ensure_ifc = __import__("ifc_import", fromlist=["_ensure_ifc"])._ensure_ifc

_ensure_ifc()
import ifcopenshell  # noqa: E402


def _simple_model(name: str = "Test Building", n_spaces: int = 3) -> BuildingModel:
    """Build a minimal but well-formed model with n spaces."""
    model = BuildingModel(name=name)
    model.levels = [Level(id="L1", name="Level 1", elevation_z_m=0.0, wall_height_m=3.0)]
    prov = Provenance(sheet_id="synth", revision=1, method="synthetic", confidence=1.0)

    polygons = [
        [[0.0, 0.0], [10.0, 0.0], [10.0, 8.0], [0.0, 8.0]],
        [[10.0, 0.0], [20.0, 0.0], [20.0, 8.0], [10.0, 8.0]],
        [[0.0, 8.0], [20.0, 8.0], [20.0, 14.0], [0.0, 14.0]],
    ]
    areas = [80.0, 80.0, 120.0]
    vols = [240.0, 240.0, 360.0]

    for i in range(n_spaces):
        sid = f"L1-{100 + i}"
        sp = Space(
            id=sid,
            level_id="L1",
            name=f"Room {i + 1}",
            number=str(100 + i),
            polygon_m=polygons[i],
            area_m2=areas[i],
            volume_m3=vols[i],
            lighting=SpaceLighting(fixtures=[], total_w=100.0),
            core_provenance=prov,
        )
        model.spaces[sid] = sp

    model.envelope = [
        EnvelopeWall(
            id="L1-W1",
            facade="south",
            from_m=(0.0, 0.0),
            to_m=(20.0, 0.0),
            area_m2=20.0 * 3.0,
            provenance=prov,
        ),
    ]
    return model


def _run_ifc_counts_check(model: BuildingModel, ifc_path: str):
    """Run _check_ifc_counts against the given model and IFC path."""
    ctx = _build_ctx(model, ifc_path=ifc_path)
    return _check_ifc_counts(ctx)


def test_export_ifc_entity_counts_match(tmp_path):
    """Happy path: exported IFC has n IfcSpace and >=1 IfcWall."""
    model = _simple_model(n_spaces=3)
    ifc_path = tmp_path / "entity_counts.ifc"

    _export_ifc(model, str(ifc_path))

    result = _run_ifc_counts_check(model, str(ifc_path))
    assert result.severity == "pass", f"expected pass, got {result.severity}: {result.message}"
    assert "3 IfcSpace" in result.message
    assert "IfcWall" in result.message

    f = ifcopenshell.open(str(ifc_path))
    assert len(f.by_type("IfcSpace")) == 3
    assert len(f.by_type("IfcWall")) >= 1


def test_export_single_space_ifc(tmp_path):
    """Edge case: model with exactly 1 space still produces valid IFC."""
    model = _simple_model(n_spaces=1)
    ifc_path = tmp_path / "single_space.ifc"

    _export_ifc(model, str(ifc_path))

    result = _run_ifc_counts_check(model, str(ifc_path))
    assert result.severity == "pass"
    assert "1 IfcSpace" in result.message


def test_check_fires_when_space_count_mismatch(tmp_path):
    """Defect injection: model has 3 spaces but IFC has 2.

    Export a 3-space model, then overwrite with a 2-space model at the same
    path.  _check_ifc_counts must catch the mismatch.
    """
    model_3 = _simple_model(n_spaces=3)
    model_2 = _simple_model(n_spaces=2)
    ifc_path = tmp_path / "mismatch.ifc"

    _export_ifc(model_3, str(ifc_path))
    _export_ifc(model_2, str(ifc_path))

    result = _run_ifc_counts_check(model_3, str(ifc_path))
    assert result.severity == "error", (
        f"expected error for space count mismatch, got {result.severity}: {result.message}"
    )
    assert "IfcSpace" in result.message
    assert "3" in str(result.expected)


def test_check_fires_when_ifc_has_no_walls(tmp_path):
    """Defect injection: IFC STEP text edited to remove all IfcWall lines.

    _check_ifc_counts must catch missing IfcWall.
    """
    model = _simple_model(n_spaces=2)
    ifc_path = tmp_path / "no_walls.ifc"

    _export_ifc(model, str(ifc_path))

    with open(ifc_path, "r") as fh:
        lines = fh.readlines()
    wall_lines = [ln for ln in lines if "IFCWALL(" in ln]
    other_lines = [ln for ln in lines if "IFCWALL(" not in ln]
    assert len(wall_lines) > 0, "test needs at least one wall to remove"
    with open(ifc_path, "w") as fh:
        fh.writelines(other_lines)

    result = _run_ifc_counts_check(model, str(ifc_path))
    assert result.severity == "error", (
        f"expected error for missing walls, got {result.severity}: {result.message}"
    )
    assert "no IfcWall" in result.message


def test_export_runs_validation_with_ifc_path(tmp_path):
    """Integration: run_checks (full battery) with ifc_path exercises _check_ifc_counts."""
    model = _simple_model(n_spaces=2)
    ifc_path = tmp_path / "full_validation.ifc"

    _export_ifc(model, str(ifc_path))

    report = run_checks(model, ifc_path=str(ifc_path), tol_envelope=1.0)
    assert report.ok, f"expected no errors, got: {report.compact()}"

    ifc_result = next(r for r in report.results if r.check_id == "ifc_entity_counts")
    assert ifc_result.severity == "pass"


def test_ifc_export_blocks_invalid_bem_conservation(tmp_path):
    """Regression: ifc-export must block invalid BEM data per conservation laws.

    See: https://github.com/.../issues/478
    """
    # Create a valid model, then violate space-area conservation
    # by setting space.area_m2 15% above its true polygon area.
    bldg, model, _report = _linked(101, False, "elev_grid")
    sp = model.spaces["L1-101"]
    true_area = polygon_area_px2(sp.polygon_m)
    sp.area_m2 = true_area * 1.15  # violate conservation

    ifc_path = tmp_path / "invalid_conservation.ifc"

    # Must raise ValueError before writing any IFC
    with pytest.raises(ValueError, match="Conservation-law validation failed"):
        _export_ifc(model, str(ifc_path))

    # File must not be created
    assert not ifc_path.exists()
