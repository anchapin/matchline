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
from ifc.ifc_export import _export_ifc, export_ifc
from tests.conftest import _linked
from tests.model_factory import make_clean_model
from validate import _build_ctx, _check_ifc_counts, run_checks

_ensure_ifc = __import__("ifc.ifc_import", fromlist=["_ensure_ifc"])._ensure_ifc

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


def test_skylights_export_as_ifcwindow_skylight_on_a_roof_slab(tmp_path):
    # Roadmap item 3, wave 2b: a skylight leaves as IfcWindow
    # PredefinedType=SKYLIGHT voiding a roof IfcSlab, never as a wall door.
    from building_model import SpaceOpening
    from ifc.ifc_export import _bem_from_model

    m = _simple_model(n_spaces=1)
    before = _export_ifc(m, tmp_path / "before.ifc")
    fb = ifcopenshell.open(str(before))
    doors_before = len(fb.by_type("IfcDoor"))
    assert not fb.by_type("IfcSlab")  # no skylights, no roof slab: unchanged output
    sid = next(iter(m.spaces))
    m.spaces[sid].openings.append(
        SpaceOpening(
            id="roof-SK-1",
            tag="SK-1",
            category="skylight",
            width_m=1.2,
            height_m=1.2,
            host_facade="roof",
            area_m2=1.44,
            tilt_deg=0.0,
        )
    )
    bem = _bem_from_model(m)
    sky = [u for u in bem.openings if u.category == "skylight"]
    assert [(u.tag, u.space_sid) for u in sky] == [("SK-1", sid)]
    after = _export_ifc(m, tmp_path / "after.ifc")
    f = ifcopenshell.open(str(after))
    assert len(f.by_type("IfcDoor")) == doors_before
    assert not any("SK-1" in (d.Name or "") for d in f.by_type("IfcDoor"))
    slabs = f.by_type("IfcSlab")
    assert len(slabs) == 1 and slabs[0].PredefinedType == "ROOF"
    skys = [w for w in f.by_type("IfcWindow") if w.PredefinedType == "SKYLIGHT"]
    assert len(skys) == 1 and "SK-1" in skys[0].Name
    (fill_rel,) = skys[0].FillsVoids
    (void_rel,) = fill_rel.RelatingOpeningElement.VoidsElements
    assert void_rel.RelatingBuildingElement == slabs[0]


def test_export_ifc_writes_convention_report(tmp_path):
    import json

    from review.convention_report import SCHEMA

    model = make_clean_model()
    model_path = tmp_path / "model.json"
    model_path.write_text(model.to_json())
    out_path = tmp_path / "model.ifc"
    export_ifc(str(model_path), str(out_path))
    report_path = tmp_path / "convention_report.json"
    assert report_path.exists()
    report = json.loads(report_path.read_text())
    assert report["schema"] == SCHEMA
    assert report["building"] == model.name


def _representations(element):
    """RepresentationIdentifier -> shape representation for an IFC product."""
    reps = element.Representation.Representations if element.Representation else []
    return {r.RepresentationIdentifier: r for r in reps}


def _element_quantities(ifc_file, element):
    """Name -> value for the IfcElementQuantity sets defining `element`."""
    out = {}
    for rel in ifc_file.by_type("IfcRelDefinesByProperties"):
        definition = rel.RelatingPropertyDefinition
        if element in rel.RelatedObjects and definition.is_a("IfcElementQuantity"):
            for quantity in definition.Quantities:
                value = getattr(quantity, "AreaValue", None)
                if value is None:
                    value = getattr(quantity, "LengthValue", None)
                out[quantity.Name] = float(value)
    return out


def _shoelace_area(points):
    """Polygon area for (x, y) points (absolute value, closing edge included)."""
    total = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def test_spaces_carry_solid_body_extruded_from_footprint(tmp_path):
    """#1332: every IfcSpace carries a Body solid over its footprint polygon.

    An independent reader that does not know the FootPrint convention must
    still recover the room (and its volume) from geometry alone.
    """
    model = make_clean_model()
    ifc_path = tmp_path / "space_solids.ifc"
    _export_ifc(model, str(ifc_path))

    f = ifcopenshell.open(str(ifc_path))
    spaces = f.by_type("IfcSpace")
    assert spaces
    for space in spaces:
        reps = _representations(space)
        footprint = reps["FootPrint"]
        (curve_set,) = footprint.Items
        (polyline,) = curve_set.Elements
        footprint_points = [
            (float(p.Coordinates[0]), float(p.Coordinates[1])) for p in polyline.Points
        ]
        body = reps["Body"]
        assert body.RepresentationType == "SweptSolid"
        (solid,) = body.Items
        assert solid.is_a("IfcExtrudedAreaSolid")
        assert solid.SweptArea.is_a("IfcArbitraryClosedProfileDef")
        profile_points = [
            (float(p.Coordinates[0]), float(p.Coordinates[1]))
            for p in solid.SweptArea.OuterCurve.Points
        ]
        if len(profile_points) > 1 and profile_points[0] == profile_points[-1]:
            profile_points = profile_points[:-1]  # closing point of the profile
        assert profile_points == footprint_points
        assert float(solid.Depth) == pytest.approx(3.0)
        quantities = _element_quantities(f, space)
        assert _shoelace_area(profile_points) == pytest.approx(quantities["GrossFloorArea"])


def test_wall_fills_carry_overall_width_and_height(tmp_path):
    """#1332: wall-hosted fills carry their size as attributes, not only in the name."""
    model = make_clean_model()
    ifc_path = tmp_path / "fill_dimensions.ifc"
    _export_ifc(model, str(ifc_path))

    f = ifcopenshell.open(str(ifc_path))
    wall_fills = []
    for fill in list(f.by_type("IfcWindow")) + list(f.by_type("IfcDoor")):
        for fill_rel in fill.FillsVoids:
            opening = fill_rel.RelatingOpeningElement
            (void_rel,) = opening.VoidsElements
            if void_rel.RelatingBuildingElement.is_a("IfcWall"):
                wall_fills.append(fill)
    assert len(wall_fills) == 2  # the two tag-A windows of make_clean_model
    schedule = model.schedules["A"]
    for fill in wall_fills:
        assert fill.OverallWidth == pytest.approx(schedule["width_m"])
        assert fill.OverallHeight == pytest.approx(schedule["height_m"])


def test_storey_carries_gross_height(tmp_path):
    """#1332: the storey carries GrossHeight so a reader recovers the level height."""
    model = make_clean_model()
    ifc_path = tmp_path / "storey_height.ifc"
    _export_ifc(model, str(ifc_path))

    f = ifcopenshell.open(str(ifc_path))
    (storey,) = f.by_type("IfcBuildingStorey")
    quantities = _element_quantities(f, storey)
    assert quantities["GrossHeight"] == pytest.approx(model.levels[0].wall_height_m)
