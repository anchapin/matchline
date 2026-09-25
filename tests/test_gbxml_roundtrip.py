"""Roundtrip test for gbXML export: conservation-law preservation.

Tests that the gbXML export preserves the geometric data needed for
BEM conservation laws:
1. Space areas are preserved in the gbXML <Area> element
2. Space volumes are preserved in the gbXML <Volume> element
3. validate_bem_conservation passes on the exported model

The roundtrip pattern:
    BEMModel → write_gbxml → XML file → parse area/volume → verify conservation
"""

from __future__ import annotations

from pathlib import Path

from bem_export import write_gbxml
from geometry_simplify import footprint_from_regions, simplify_ring
from link import build_model
from run_pipeline import model_from_linked_model
from synth.multidiscipline import generate_building
from validate import validate_bem_conservation

GBXML_NS = "http://www.gbxml.org/schema"


def _linked(seed: int, open_office_span: bool = False, elevation_key: str = "elev_grid"):
    """Build linked BuildingModel from synthetic building."""
    bldg = generate_building(seed, open_office_span=open_office_span)
    model, report = build_model(
        bldg, elevation_key=elevation_key, building_name=bldg["building_id"]
    )
    return bldg, model, report


def _build_bem(seed: int = 101, simplify_tolerance: float = 0.5):
    """Build a BEMModel from the 3-room building."""
    _bldg, model, _report = _linked(seed)
    sres = simplify_ring(
        footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
        tol=0.02,  # 2% simplification tolerance
        wall_height=model.levels[0].wall_height_m,
    )
    bem = model_from_linked_model(
        model=model,
        simplified_ring=sres.ring,
        wall_height_m=model.levels[0].wall_height_m,
        simplify_tolerance=simplify_tolerance,
    )
    return bem


def _parse_gbxml_areas_volumes(xml_path: Path):
    """Parse gbXML file and extract space areas and volumes.

    Returns dict mapping space sid -> {name, area_m2, volume_m3}
    """
    from safe_xml import safe_xml_parse

    tree = safe_xml_parse(xml_path)
    root = tree.getroot()

    # gbXML 6.01 uses namespace
    ns = {"g": GBXML_NS}

    spaces = {}
    for sp_el in root.findall(".//g:Space", ns):
        sid = sp_el.get("id")
        name = sp_el.find("g:Name", ns)
        area_el = sp_el.find("g:Area", ns)
        vol_el = sp_el.find("g:Volume", ns)

        spaces[sid] = {
            "name": name.text if name is not None else sid,
            "area_m2": float(area_el.text) if area_el is not None else None,
            "volume_m3": float(vol_el.text) if vol_el is not None else None,
        }

    return spaces


class TestGBXMLRoundtripConservation:
    """Verify gbXML export preserves data needed for BEM conservation laws."""

    def test_gbxml_roundtrip_preserves_space_areas(self, tmp_path):
        """Space areas written to gbXML match the original BEMModel."""
        bem = _build_bem()

        # Export to gbXML
        gbxml_path = tmp_path / "roundtrip.xml"
        write_gbxml(bem, gbxml_path)

        # Parse back
        parsed = _parse_gbxml_areas_volumes(gbxml_path)

        # Compare each space (tolerance: 0.01% for XML roundtrip)
        TOL = 1e-4
        for sp in bem.spaces:
            assert sp.sid in parsed, f"Space {sp.sid} not found in gbXML output"
            parsed_sp = parsed[sp.sid]
            assert parsed_sp["area_m2"] is not None, f"Area missing for space {sp.sid}"
            rel_err = abs(parsed_sp["area_m2"] - sp.area_m2) / sp.area_m2
            assert rel_err < TOL, (
                f"Area mismatch for space {sp.sid}: "
                f"got {parsed_sp['area_m2']}, expected {sp.area_m2} "
                f"(relative error {rel_err:.2e})"
            )

    def test_gbxml_roundtrip_preserves_space_volumes(self, tmp_path):
        """Space volumes written to gbXML match the original BEMModel."""
        bem = _build_bem()

        # Export to gbXML
        gbxml_path = tmp_path / "roundtrip.xml"
        write_gbxml(bem, gbxml_path)

        # Parse back
        parsed = _parse_gbxml_areas_volumes(gbxml_path)

        # Compare each space (tolerance: 0.01% for XML roundtrip)
        TOL = 1e-4
        for sp in bem.spaces:
            assert sp.sid in parsed, f"Space {sp.sid} not found in gbXML output"
            parsed_sp = parsed[sp.sid]
            assert parsed_sp["volume_m3"] is not None, f"Volume missing for space {sp.sid}"
            rel_err = abs(parsed_sp["volume_m3"] - sp.volume_m3) / sp.volume_m3
            assert rel_err < TOL, (
                f"Volume mismatch for space {sp.sid}: "
                f"got {parsed_sp['volume_m3']}, expected {sp.volume_m3} "
                f"(relative error {rel_err:.2e})"
            )

    def test_gbxml_roundtrip_conservation_passes(self, tmp_path):
        """validate_bem_conservation passes on BEMModel after gbXML export.

        This is the primary roundtrip test: we export a BEMModel to gbXML
        and verify that the model's conservation laws are satisfied.
        Since write_gbxml preserves area/volume exactly, the conservation
        checks should pass.
        """
        bem = _build_bem()

        # First verify the model passes conservation before export
        results_before = validate_bem_conservation(bem)
        for r in results_before:
            assert r.severity == "pass", (
                f"BEMModel failed conservation check before export: {r.name} - {r.message}"
            )

        # Export to gbXML (verifies the write path works)
        gbxml_path = tmp_path / "roundtrip.xml"
        write_gbxml(bem, gbxml_path)

        # Verify file was created and is non-empty
        assert gbxml_path.exists(), "gbXML file was not created"
        assert gbxml_path.stat().st_size > 0, "gbXML file is empty"

        # Parse the exported file to verify data integrity
        parsed = _parse_gbxml_areas_volumes(gbxml_path)

        # Verify all spaces were exported
        assert len(parsed) == len(bem.spaces), (
            f"Number of spaces mismatch: got {len(parsed)}, expected {len(bem.spaces)}"
        )

        # Verify conservation-critical data is preserved
        # Tolerance: 0.01% for XML roundtrip (float serialization)
        TOL = 1e-4
        total_area_before = sum(sp.area_m2 for sp in bem.spaces)
        total_area_after = sum(sp["area_m2"] for sp in parsed.values() if sp["area_m2"] is not None)
        rel_err_area = abs(total_area_before - total_area_after) / total_area_before
        assert rel_err_area < TOL, (
            f"Total area mismatch: got {total_area_after}, expected {total_area_before} "
            f"(relative error {rel_err_area:.2e})"
        )

        total_vol_before = sum(sp.volume_m3 for sp in bem.spaces)
        total_vol_after = sum(
            sp["volume_m3"] for sp in parsed.values() if sp["volume_m3"] is not None
        )
        rel_err_vol = abs(total_vol_before - total_vol_after) / total_vol_before
        assert rel_err_vol < TOL, (
            f"Total volume mismatch: got {total_vol_after}, expected {total_vol_before} "
            f"(relative error {rel_err_vol:.2e})"
        )


class TestGBXMLRoundtripProvenance:
    """Verify provenance tracking is preserved in gbXML export."""

    def test_gbxml_export_includes_provenance_comments(self, tmp_path):
        """The gbXML export includes provenance information in description."""
        bem = _build_bem()

        gbxml_path = tmp_path / "provenance.xml"
        write_gbxml(bem, gbxml_path)

        # Read the file and check for provenance elements
        content = gbxml_path.read_text()

        # Should mention simplification delta and tolerance in the description
        assert "area delta" in content, "Provenance area_delta missing from gbXML description"
        assert "tolerance" in content, "Provenance tolerance missing from gbXML description"
        assert "Jesse-Vision" in content or "Exported by" in content, (
            "Export provenance comment missing"
        )
