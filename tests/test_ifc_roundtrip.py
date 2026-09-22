"""IFC round-trip tests: BuildingModel → IFC4 → BuildingModel.

Verifies IFC-02 (zones, lighting), IFC-03 (round-trip fidelity), and
IFC-04 (cross-sheet window dedup via the IFC round-trip).

Fixture strategy: build a BuildingModel with known zones + lighting + openings,
export via ifc_export, re-import via ifc_import, assert zone memberships and
lighting watt totals are preserved within 1% tolerance.
"""

from building_model import (
    BuildingModel,
    Level,
    Provenance,
    Space,
    SpaceLighting,
    SpaceOpening,
    Zone,
)
from ifc_export import _export_ifc

_ensure_ifc = __import__("ifc_import", fromlist=["_ensure_ifc"])._ensure_ifc
_ensure_ifc()
import ifcopenshell  # noqa: E402


def _model_with_zones_and_lighting() -> BuildingModel:
    """Build a model matching the test_ifc_import fixture but with explicit zones and lighting."""
    model = BuildingModel(name="Fixture Building")
    model.levels = [Level(id="L1", name="Level 1", elevation_z_m=0.0, wall_height_m=3.0)]
    model.spaces = {}

    spaces_data = [
        ("L1-101", "OPEN OFFICE", "101", [[0, 0], [10, 0], [10, 8], [0, 8]], 80.0, 240.0, 500.0),
        ("L1-102", "CONF", "102", [[10, 0], [20, 0], [20, 8], [10, 8]], 80.0, 240.0, 300.0),
        ("L1-103", "LOBBY", "103", [[0, 8], [20, 8], [20, 12], [0, 12]], 80.0, 240.0, 200.0),
    ]
    for sid, name, number, polygon, area, vol, watts in spaces_data:
        sp = Space(
            id=sid,
            level_id="L1",
            name=name,
            number=number,
            polygon_m=polygon,
            area_m2=area,
            volume_m3=vol,
            lighting=SpaceLighting(fixtures=[], total_w=watts),
        )
        model.spaces[sid] = sp

    # Zone A: office + conf; Zone B: lobby
    model.zones["ZONE-A"] = Zone(
        id="ZONE-A",
        level_id="L1",
        space_ids=["L1-101", "L1-102"],
        provenance=Provenance(sheet_id="synth", revision=1, method="synthetic", confidence=1.0),
    )
    model.zones["ZONE-B"] = Zone(
        id="ZONE-B",
        level_id="L1",
        space_ids=["L1-103"],
        provenance=Provenance(sheet_id="synth", revision=1, method="synthetic", confidence=1.0),
    )
    return model


def test_zone_roundtrip(tmp_path):
    """IFC-03: zone-to-space membership preserved after import→export→import."""
    model = _model_with_zones_and_lighting()
    ifc_path = tmp_path / "roundtrip.ifc"

    _export_ifc(model, str(ifc_path))
    imported = __import__("ifc_import", fromlist=["import_ifc"]).import_ifc(str(ifc_path))

    # ZONE-A should contain L1-101 and L1-102
    assert "ZONE-A" in imported.zones
    za = imported.zones["ZONE-A"]
    assert len(za.space_ids) == 2
    assert "L1-101" in za.space_ids
    assert "L1-102" in za.space_ids

    # ZONE-B should contain L1-103
    assert "ZONE-B" in imported.zones
    zb = imported.zones["ZONE-B"]
    assert len(zb.space_ids) == 1
    assert "L1-103" in zb.space_ids

    # Many-to-many: no space should appear in more than one zone in this fixture
    all_space_ids = set()
    for z in imported.zones.values():
        for sid in z.space_ids:
            assert sid not in all_space_ids, f"space {sid} appears in multiple zones"
            all_space_ids.add(sid)


def test_lighting_roundtrip(tmp_path):
    """IFC-03: lighting watt totals preserved within 1% tolerance."""
    model = _model_with_zones_and_lighting()
    ifc_path = tmp_path / "lighting.ifc"

    orig_total = sum(sp.lighting.total_w for sp in model.spaces.values())

    _export_ifc(model, str(ifc_path))
    imported = __import__("ifc_import", fromlist=["import_ifc"]).import_ifc(str(ifc_path))

    imported_total = sum(sp.lighting.total_w for sp in imported.spaces.values())
    tolerance = orig_total * 0.01  # 1%
    assert abs(imported_total - orig_total) <= tolerance, (
        f"lighting watt total drifted: orig={orig_total}, imported={imported_total}, "
        f"tolerance={tolerance}"
    )


def test_ifc_export_validates(tmp_path):
    """IFC-02: exported IFC4 file passes structural validation."""
    model = _model_with_zones_and_lighting()
    ifc_path = tmp_path / "validate.ifc"

    _export_ifc(model, str(ifc_path))

    validate_ifc4 = __import__("bem_export", fromlist=["validate_ifc4"]).validate_ifc4
    ok, errors = validate_ifc4(str(ifc_path))
    assert ok, f"validate_ifc4 failed: {errors}"

    f = ifcopenshell.open(str(ifc_path))
    assert f.schema == "IFC4"

    # Should have IfcZone entities
    zones = f.by_type("IfcZone")
    assert len(zones) >= 2, f"expected ≥2 IfcZone entities, got {len(zones)}"


def test_cross_sheet_dedup_via_roundtrip(tmp_path):
    """IFC-04: two link_elevations runs on same facade produce one SpaceOpening per window.

    This tests the end-to-end effect: when the same window appears in two
    separate elevation observations and gets linked twice, the IFC export
    should de-duplicate at the SpaceOpenings level before writing.

    Simulated by: creating two SpaceOpenings with identical (tag, sill, center, facade)
    on the same space, then verifying that after a round-trip only one remains.
    """
    model = BuildingModel(name="Dedup Test", levels=[Level(id="L1", wall_height_m=3.0)])
    prov = Provenance(sheet_id="synth", revision=1, method="synthetic", confidence=0.9)

    sp = Space(
        id="L1-101",
        level_id="L1",
        name="Room",
        number="101",
        polygon_m=[[0, 0], [10, 0], [10, 8], [0, 8]],
        area_m2=80.0,
    )
    # Two identical windows (simulating two elevations of same facade)
    sp.openings = [
        SpaceOpening(
            id="w1",
            tag="A",
            category="window",
            width_m=1.5,
            height_m=1.2,
            sill_m=0.9,
            host_facade="south",
            s_center_m=2.0,
            provenance=prov,
        ),
        SpaceOpening(
            id="w2",
            tag="A",
            category="window",
            width_m=1.5,
            height_m=1.2,
            sill_m=0.9,
            host_facade="south",
            s_center_m=2.0,
            provenance=prov,
        ),
    ]
    model.spaces["L1-101"] = sp

    ifc_path = tmp_path / "dedup.ifc"
    _export_ifc(model, str(ifc_path))
    imported = __import__("ifc_import", fromlist=["import_ifc"]).import_ifc(str(ifc_path))

    # After cross-sheet dedup, L1-101 should have exactly 1 opening (not 2)
    openings = imported.spaces["L1-101"].openings
    assert len(openings) == 1, f"expected 1 SpaceOpening after dedup, got {len(openings)}"
