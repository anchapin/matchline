"""Unit tests for write_gbxml: create BEMModel, write gbXML, validate."""

from bem_export import BEMModel, BEMOpeningUnit, BEMSpace, validate_gbxml, write_gbxml


def _minimal_bem_model() -> BEMModel:
    """Create a minimal but structurally valid BEMModel for testing."""
    return BEMModel(
        building_name="Test Building",
        spaces=[
            BEMSpace(
                sid="sp-001",
                name="Office 101",
                number="101",
                polygon_m=[(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)],
                area_m2=80.0,
                volume_m3=240.0,
            ),
        ],
        openings=[
            BEMOpeningUnit(category="window", tag="A", width_m=1.2, height_m=1.5),
            BEMOpeningUnit(category="window", tag="B", width_m=1.8, height_m=1.5),
            BEMOpeningUnit(category="door", tag="D1", width_m=0.9, height_m=2.1),
        ],
        ring_m=[(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)],
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=2.0,
    )


def test_write_gbxml_validate_returns_true_no_errors(tmp_path):
    """write_gbxml produces a gbXML file that passes validate_gbxml."""
    model = _minimal_bem_model()
    gbxml_path = tmp_path / "test_building.xml"

    result_path = write_gbxml(model, gbxml_path)
    assert result_path == gbxml_path
    assert gbxml_path.exists()

    ok, errors = validate_gbxml(gbxml_path)
    assert ok is True, f"Expected validation ok=True, got errors: {errors}"
    assert errors == [], f"Expected no errors, got: {errors}"


def test_write_gbxml_multiple_spaces(tmp_path):
    """write_gbxml handles multiple spaces correctly."""
    model = BEMModel(
        building_name="Multi-Space Building",
        spaces=[
            BEMSpace(
                sid="sp-001",
                name="Room A",
                number="1",
                polygon_m=[(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0)],
                area_m2=25.0,
                volume_m3=75.0,
            ),
            BEMSpace(
                sid="sp-002",
                name="Room B",
                number="2",
                polygon_m=[(5.0, 0.0), (10.0, 0.0), (10.0, 5.0), (5.0, 5.0)],
                area_m2=25.0,
                volume_m3=75.0,
            ),
        ],
        openings=[
            BEMOpeningUnit(category="window", tag="A", width_m=1.0, height_m=1.2),
        ],
        ring_m=[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)],
        wall_height_m=3.0,
        area_delta_pct=0.1,
        simplify_tolerance=2.0,
    )

    gbxml_path = tmp_path / "multi_space.xml"
    write_gbxml(model, gbxml_path)

    ok, errors = validate_gbxml(gbxml_path)
    assert ok is True, f"Multi-space validation failed: {errors}"
    assert errors == []
