"""End-to-end provenance tracking through the full transformation pipeline.

Tests that every extracted fact at each pipeline stage carries the correct
provenance: sheet, revision, method, and confidence.

Pipeline stages:
    1. Drawing   → generate_building output (sheets with meta)
    2. Symbol    → Space / Opening entities from arch_plan_parse
    3. Schedule  → Lighting fixtures (SpaceLighting provenance from schedule_join,
                   individual fixtures from point_in_polygon)
    4. Zone      → Zone entities from duct_tracing
    5. HVAC      → Diffusers / sensors from duct_tracing
    6. BEM export → BEMModel created from linked model
"""

from __future__ import annotations

import pytest

from geometry_simplify import footprint_from_regions, simplify_ring
from link import build_model
from synth.multidiscipline import generate_building


class TestProvenanceFieldsNonNone:
    """Every sampled entity carries non-None provenance at each stage."""

    @pytest.mark.parametrize("seed", [101, 102])
    @pytest.mark.parametrize("open_office_span", [False, True])
    def test_spaces_carry_provenance(self, seed, open_office_span):
        bldg = generate_building(seed, open_office_span=open_office_span)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        assert model.spaces, "pipeline must produce at least one space"
        for sid, sp in model.spaces.items():
            assert sp.core_provenance is not None, f"space={sid}: core_provenance is None"
            p = sp.core_provenance
            assert hasattr(p, "sheet_id") and p.sheet_id, f"space={sid}: missing sheet_id"
            assert hasattr(p, "revision") and p.revision, f"space={sid}: missing revision"
            assert hasattr(p, "method") and p.method, f"space={sid}: missing method"
            assert hasattr(p, "confidence"), f"space={sid}: missing confidence"

    @pytest.mark.parametrize("seed", [101, 102])
    def test_openings_carry_provenance(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        opening_found = False
        for sid, sp in model.spaces.items():
            for o in sp.openings:
                opening_found = True
                assert o.provenance is not None, f"opening={o.id} space={sid}: provenance is None"
                p = o.provenance
                assert p.sheet_id, f"opening={o.id}: missing sheet_id"
                assert p.revision, f"opening={o.id}: missing revision"
                assert p.method, f"opening={o.id}: missing method"
                assert hasattr(p, "confidence"), f"opening={o.id}: missing confidence"
        assert opening_found, "pipeline must produce at least one opening"

    @pytest.mark.parametrize("seed", [101, 102])
    def test_lighting_fixtures_carry_provenance(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        fixture_found = False
        for sid, sp in model.spaces.items():
            for f in sp.lighting.fixtures:
                fixture_found = True
                assert f.provenance is not None, f"fixture={f.id} space={sid}: provenance is None"
                p = f.provenance
                assert p.sheet_id, f"fixture={f.id}: missing sheet_id"
                assert p.revision, f"fixture={f.id}: missing revision"
                assert p.method, f"fixture={f.id}: missing method"
                assert hasattr(p, "confidence"), f"fixture={f.id}: missing confidence"
        assert fixture_found, "pipeline must produce at least one lighting fixture"

    @pytest.mark.parametrize("seed", [101, 102])
    def test_zones_carry_provenance(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        assert model.zones, "pipeline must produce at least one zone"
        for zid, zone in model.zones.items():
            assert zone.provenance is not None, f"zone={zid}: provenance is None"
            p = zone.provenance
            assert p.sheet_id, f"zone={zid}: missing sheet_id"
            assert p.revision, f"zone={zid}: missing revision"
            assert p.method, f"zone={zid}: missing method"
            assert hasattr(p, "confidence"), f"zone={zid}: missing confidence"

    @pytest.mark.parametrize("seed", [101, 102])
    def test_hvac_diffusers_carry_provenance(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        diffuser_found = False
        for sid, sp in model.spaces.items():
            for d in sp.hvac.diffusers:
                diffuser_found = True
                assert d.provenance is not None, f"diffuser={d.id} space={sid}: provenance is None"
                p = d.provenance
                assert p.sheet_id, f"diffuser={d.id}: missing sheet_id"
                assert p.revision, f"diffuser={d.id}: missing revision"
                assert p.method, f"diffuser={d.id}: missing method"
                assert hasattr(p, "confidence"), f"diffuser={d.id}: missing confidence"
        assert diffuser_found, "pipeline must produce at least one diffuser"

    @pytest.mark.parametrize("seed", [101, 102])
    def test_hvac_sensors_carry_provenance(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        sensor_found = False
        for sid, sp in model.spaces.items():
            for s in sp.hvac.sensors:
                sensor_found = True
                assert s.provenance is not None, f"sensor={s.id} space={sid}: provenance is None"
                p = s.provenance
                assert p.sheet_id, f"sensor={s.id}: missing sheet_id"
                assert p.revision, f"sensor={s.id}: missing revision"
                assert p.method, f"sensor={s.id}: missing method"
                assert hasattr(p, "confidence"), f"sensor={s.id}: missing confidence"
        assert sensor_found, "pipeline must produce at least one sensor"


class TestProvenanceMethodAssignment:
    """Provenance method field matches the transformation method used."""

    @pytest.mark.parametrize("seed", [101, 102])
    def test_space_provenance_method_arch_plan_parse(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            assert sp.core_provenance.method == "arch_plan_parse", (
                f"space={sid}: expected method='arch_plan_parse', got '{sp.core_provenance.method}'"
            )

    @pytest.mark.parametrize("seed", [101, 102])
    def test_zone_provenance_method_duct_tracing(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for zid, zone in model.zones.items():
            assert zone.provenance.method == "duct_tracing", (
                f"zone={zid}: expected method='duct_tracing', got '{zone.provenance.method}'"
            )

    @pytest.mark.parametrize("seed", [101, 102])
    def test_hvac_diffuser_provenance_method_symbol_detection(self, seed):
        """Assigned diffusers (found in a space) use symbol_detection."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            for d in sp.hvac.diffusers:
                assert d.provenance.method == "symbol_detection", (
                    f"diffuser={d.id} space={sid}: expected method='symbol_detection', "
                    f"got '{d.provenance.method}'"
                )

    @pytest.mark.parametrize("seed", [101, 102])
    def test_hvac_sensor_provenance_method_symbol_detection(self, seed):
        """Assigned sensors (found in a space) use symbol_detection."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            for s in sp.hvac.sensors:
                assert s.provenance.method == "symbol_detection", (
                    f"sensor={s.id} space={sid}: expected method='symbol_detection', "
                    f"got '{s.provenance.method}'"
                )

    @pytest.mark.parametrize("seed", [101, 102])
    def test_opening_provenance_method_valid(self, seed):
        """Opening provenance method is one of the valid window detection/dedup methods."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        valid_methods = {"grid_registration", "geometric_registration", "window_dedup"}
        for sid, sp in model.spaces.items():
            for o in sp.openings:
                assert o.provenance.method in valid_methods, (
                    f"opening={o.id} space={sid}: method='{o.provenance.method}' "
                    f"not in expected set {valid_methods}"
                )

    @pytest.mark.parametrize("seed", [101, 102])
    def test_assigned_fixture_provenance_method_point_in_polygon(self, seed):
        """Assigned fixtures (in a space) are located via point_in_polygon centroid check."""
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            for f in sp.lighting.fixtures:
                assert f.provenance.method == "point_in_polygon", (
                    f"fixture={f.id} space={sid}: expected method='point_in_polygon', "
                    f"got '{f.provenance.method}'"
                )


class TestProvenanceSheetAndRevision:
    """Provenance sheet_id and revision match the originating sheet."""

    @pytest.mark.parametrize("seed", [101])
    def test_space_sheet_id_matches_arch_sheet(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        arch_sheet_id = bldg["sheets"]["arch"]["meta"]["sheet_id"]
        arch_revision = bldg["sheets"]["arch"]["meta"]["revision"]
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            assert sp.core_provenance.sheet_id == arch_sheet_id, (
                f"space={sid}: expected sheet_id='{arch_sheet_id}', "
                f"got '{sp.core_provenance.sheet_id}'"
            )
            assert sp.core_provenance.revision == arch_revision, (
                f"space={sid}: expected revision={arch_revision}, got {sp.core_provenance.revision}"
            )

    @pytest.mark.parametrize("seed", [101])
    def test_zone_sheet_id_from_mech_sheet(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        mech_sheet_id = bldg["sheets"]["mech"]["meta"]["sheet_id"]
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for zid, zone in model.zones.items():
            assert zone.provenance.sheet_id == mech_sheet_id, (
                f"zone={zid}: expected sheet_id='{mech_sheet_id}', got '{zone.provenance.sheet_id}'"
            )

    @pytest.mark.parametrize("seed", [101])
    def test_hvac_diffuser_sheet_id_from_mech_sheet(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        mech_sheet_id = bldg["sheets"]["mech"]["meta"]["sheet_id"]
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            for d in sp.hvac.diffusers:
                assert d.provenance.sheet_id == mech_sheet_id, (
                    f"diffuser={d.id}: expected sheet_id='{mech_sheet_id}', "
                    f"got '{d.provenance.sheet_id}'"
                )

    @pytest.mark.parametrize("seed", [101])
    def test_assigned_fixture_sheet_id_from_lighting_sheet(self, seed):
        """Fixtures are extracted from the lighting plan sheet, so provenance uses that sheet."""
        bldg = generate_building(seed, open_office_span=False)
        light_sheet_id = bldg["sheets"]["lighting"]["meta"]["sheet_id"]
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            for f in sp.lighting.fixtures:
                assert f.provenance.sheet_id == light_sheet_id, (
                    f"fixture={f.id}: expected sheet_id='{light_sheet_id}', "
                    f"got '{f.provenance.sheet_id}'"
                )


class TestProvenanceConfidence:
    """Confidence is a float in [0, 1] and is meaningful per method."""

    @pytest.mark.parametrize("seed", [101, 102])
    def test_all_confidences_in_valid_range(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            p = sp.core_provenance
            assert 0.0 <= p.confidence <= 1.0, (
                f"space={sid}: confidence out of range: {p.confidence}"
            )
            for o in sp.openings:
                op = o.provenance
                assert 0.0 <= op.confidence <= 1.0, f"opening={o.id}: confidence out of range"
            for f in sp.lighting.fixtures:
                fp = f.provenance
                assert 0.0 <= fp.confidence <= 1.0, f"fixture={f.id}: confidence out of range"
            for d in sp.hvac.diffusers:
                dp = d.provenance
                assert 0.0 <= dp.confidence <= 1.0, f"diffuser={d.id}: confidence out of range"
            for s in sp.hvac.sensors:
                sp2 = s.provenance
                assert 0.0 <= sp2.confidence <= 1.0, f"sensor={s.id}: confidence out of range"
        for zid, zone in model.zones.items():
            zp = zone.provenance
            assert 0.0 <= zp.confidence <= 1.0, (
                f"zone={zid}: confidence out of range: {zp.confidence}"
            )

    @pytest.mark.parametrize("seed", [101])
    def test_assigned_fixture_confidence_lower_than_space(self, seed):
        """Fixture confidence (point_in_polygon via centroid) should be <= space confidence.

        Space is extracted with full polygon confidence; fixtures are a sub-location
        check with potential for centroid being near a boundary.
        """
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        for sid, sp in model.spaces.items():
            space_conf = sp.core_provenance.confidence
            for f in sp.lighting.fixtures:
                fixture_conf = f.provenance.confidence
                assert fixture_conf <= space_conf + 1e-6, (
                    f"fixture={f.id} confidence ({fixture_conf}) should be <= "
                    f"space confidence ({space_conf})"
                )


class TestProvenanceLightingRollup:
    """SpaceLighting has rollup provenance from schedule_join."""

    @pytest.mark.parametrize("seed", [101])
    def test_space_lighting_provenance_method_schedule_join(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        light_sheet_id = bldg["sheets"]["lighting"]["meta"]["sheet_id"]
        for sid, sp in model.spaces.items():
            p = sp.lighting.provenance
            assert p is not None, f"space={sid}: SpaceLighting.provenance is None"
            assert p.method == "schedule_join", (
                f"space={sid}: expected SpaceLighting.provenance method='schedule_join', "
                f"got '{p.method}'"
            )
            assert p.sheet_id == light_sheet_id, (
                f"space={sid}: expected sheet_id='{light_sheet_id}', got '{p.sheet_id}'"
            )


class TestProvenanceBEMExport:
    """BEM export creates a model from the linked model provenance chain."""

    @pytest.mark.parametrize("seed", [101])
    def test_bem_model_space_count_matches_linked_model(self, seed):
        bldg = generate_building(seed, open_office_span=False)
        model, _ = build_model(bldg, elevation_key="elev_grid", building_name=bldg["building_id"])
        from run_pipeline import model_from_linked_model

        sres = simplify_ring(
            footprint_from_regions([sp.polygon_m for sp in model.spaces.values()]),
            tol=0.02,
            wall_height=3.0,
        )
        bem = model_from_linked_model(
            model, simplified_ring=sres.ring, wall_height_m=3.0, simplify_tolerance=0.02
        )
        assert bem.building_name == model.name or bem.building_name == bldg["building_id"]
        assert len(bem.spaces) == len(model.spaces), (
            f"BEM spaces ({len(bem.spaces)}) != model spaces ({len(model.spaces)})"
        )
