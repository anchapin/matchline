"""The BuildingSync writer (#1238): sibling of the gbXML export.

Fixture exports must pass XSD validation (the acceptance criterion), the
schema version is a parameter, zones map to ThermalZones with Space refs,
air loops map to HVACSystem + FanSystem + Deliveries, and values the 2.6.0
schema has no slot for travel in namespaced UserDefinedFields instead of
being dropped or converted. Geometry is never dumped.
"""

import xml.etree.ElementTree as ET

from bem.bem_geometry import BEMModel, BEMSpace
from bem.buildingsync_export import (
    AUC_NS,
    DEFAULT_VERSION,
    UDF_PREFIX,
    validate_buildingsync,
    write_buildingsync,
)

AUC = f"{{{AUC_NS}}}"


def _tiny_model(**overrides) -> BEMModel:
    space = BEMSpace(
        sid="L1-101",
        name="Office 101",
        number="101",
        polygon_m=[(0, 0), (10, 0), (10, 10), (0, 10)],
        area_m2=100.0,
        volume_m3=300.0,
    )
    model = BEMModel(
        building_name="Test Building",
        spaces=[space],
        openings=[],
        ring_m=[(0, 0), (20, 0), (20, 20), (0, 20)],
        wall_height_m=3.0,
        area_delta_pct=0.0,
        simplify_tolerance=0.1,
        zones=[("z-1", ["L1-101"])],
    )
    for key, value in overrides.items():
        setattr(model, key, value)
    return model


def _tags(root, local: str) -> list:
    return [el for el in root.iter(f"{AUC}{local}")]


def _first(root, local: str):
    found = _tags(root, local)
    return found[0] if found else None


def _udf_map(el) -> dict:
    out = {}
    for udf in el.findall(f"{AUC}UserDefinedFields/{AUC}UserDefinedField"):
        name = udf.findtext(f"{AUC}FieldName")
        value = udf.findtext(f"{AUC}FieldValue")
        if name:
            out[name] = value
    return out


def test_write_buildingsync_and_validate_roundtrip(tmp_path):
    """A written export validates against the shipped 2.6.0 XSD."""
    path = write_buildingsync(_tiny_model(), tmp_path / "out.xml")
    ok, errors = validate_buildingsync(path)
    assert ok is True, f"validate_buildingsync failed: {errors}"
    assert errors == []


def test_version_parameter_is_emitted(tmp_path):
    """The target schema version is a parameter, not hardcoded."""
    default = ET.parse(write_buildingsync(_tiny_model(), tmp_path / "a.xml")).getroot()
    assert default.get("version") == DEFAULT_VERSION
    other = ET.parse(
        write_buildingsync(_tiny_model(), tmp_path / "b.xml", version="2.7.0")
    ).getroot()
    assert other.get("version") == "2.7.0"


def test_zones_become_thermal_zones_with_space_refs(tmp_path):
    """HVAC zones map to ThermalZones whose Spaces reference written Spaces."""
    root = ET.parse(write_buildingsync(_tiny_model(), tmp_path / "out.xml")).getroot()
    spaces = {el.get("ID") for el in _tags(root, "Space")}
    assert spaces, "no Space elements written"
    thermal = _first(root, "ThermalZone")
    assert thermal is not None, "no ThermalZone written"
    zone_spaces = [el.get("ID") for el in thermal.findall(f"{AUC}Spaces/{AUC}Space")]
    assert zone_spaces and all(ref in spaces for ref in zone_spaces)
    assert len(zone_spaces) == len(spaces), "every space lands in exactly one thermal zone"


def test_air_loops_map_to_hvac_fan_and_deliveries(tmp_path):
    """One HVACSystem + FanSystem per air loop, one Delivery per served zone."""
    loop = {
        "id": "AL-1",
        "name": "AHU 1",
        "system_type": "VariableAirVolume",
        "fan_airflow_lps": 1.5,
        "cooling_total_btuh": 24000.0,
        "cooling_sensible_btuh": 18000.0,
        "heating_btuh": 30000.0,
        "sources": ["pdf_schedule_equipment"],
    }
    model = _tiny_model(
        air_loops=[loop],
        zone_air_loop={"zone-z-1": "AL-1"},
        zone_terminals={"z-1": [("d-1", "A", 1.0, 1.0)]},
    )
    root = ET.parse(write_buildingsync(model, tmp_path / "out.xml")).getroot()
    hvac = _first(root, "HVACSystem")
    assert hvac is not None, "no HVACSystem written"
    fan = _first(root, "FanSystem")
    assert fan is not None, "no FanSystem written"
    linked = fan.find(f"{AUC}LinkedSystemIDs/{AUC}LinkedSystemID")
    assert linked is not None and linked.get("IDref") == hvac.get("ID"), (
        "FanSystem does not link its HVACSystem"
    )
    delivery = _first(root, "Delivery")
    assert delivery is not None, "no Delivery written"
    zone_ref_el = _first(root, "ThermalZone").find(f"{AUC}DeliveryIDs/{AUC}DeliveryID")
    assert zone_ref_el is not None and zone_ref_el.get("IDref") == delivery.get("ID"), (
        "ThermalZone does not reference its delivery"
    )


def test_unslotted_values_travel_in_namespaced_udfs(tmp_path):
    """Capacities, airflow and U-values carry units in the FieldName, unconverted."""
    space2 = BEMSpace(
        sid="L1-102",
        name="Office 102",
        number="102",
        polygon_m=[(10, 0), (20, 0), (20, 10), (10, 10)],
        area_m2=100.0,
        volume_m3=300.0,
        wall_u_value_w_m2k=0.35,
    )
    model = _tiny_model(
        spaces=[space2],
        roof_u_value_w_m2k=0.18,
        slab_u_value_w_m2k=0.30,
        opening_constructions={
            "W1": {"name": "Double pane", "category": "window", "u": 2.0, "shgc": 0.4}
        },
        air_loops=[
            {
                "id": "AL-1",
                "name": "AHU 1",
                "system_type": "VariableAirVolume",
                "fan_airflow_lps": 1.5,
                "cooling_total_btuh": 24000.0,
                "cooling_sensible_btuh": 18000.0,
                "heating_btuh": 30000.0,
                "sources": [],
            }
        ],
        zone_air_loop={"zone-z-1": "AL-1"},
    )
    root = ET.parse(write_buildingsync(model, tmp_path / "out.xml")).getroot()
    ok, errors = validate_buildingsync(tmp_path / "out.xml")
    assert ok is True, f"validate_buildingsync failed: {errors}"
    hvac_udfs = _udf_map(_first(root, "HVACSystem"))
    assert hvac_udfs[f"{UDF_PREFIX}cooling_total_btuh"] == "24000.0000"
    assert hvac_udfs[f"{UDF_PREFIX}system_type"] == "VariableAirVolume"
    fan_udfs = _udf_map(_first(root, "FanSystem"))
    assert fan_udfs[f"{UDF_PREFIX}fan_airflow_lps"] == "1.5000"
    wall_udfs = _udf_map(_first(root, "WallSystem"))
    assert wall_udfs[f"{UDF_PREFIX}wall_u_value_w_m2k"] == "0.3500"
    fen_udfs = _udf_map(_first(root, "FenestrationSystem"))
    assert fen_udfs[f"{UDF_PREFIX}u_value_w_m2k"] == "2.0000"
    assert fen_udfs[f"{UDF_PREFIX}shgc"] == "0.400"
    # the R-value slots stay empty: the file cannot carry their unit honestly
    assert _first(root, "WallInsulationRValue") is None


def test_no_geometry_dumped(tmp_path):
    """BuildingSync wants far less geometry than gbXML; none is dumped."""
    path = write_buildingsync(_tiny_model(), tmp_path / "out.xml")
    ok, errors = validate_buildingsync(path)
    assert ok is True, f"validate_buildingsync failed: {errors}"
    root = ET.parse(path).getroot()
    for el in root.iter():
        local = el.tag.split("}")[-1]
        assert local not in {"PolyLoop", "PlanarCoordinate", "CartesianPoint", "Vertex"}, (
            f"geometry element {local} leaked into the BuildingSync export"
        )


def test_audit_records_are_minimal_standins(tmp_path):
    """Contacts and Reports exist but carry no invented audit data."""
    root = ET.parse(write_buildingsync(_tiny_model(), tmp_path / "out.xml")).getroot()
    contact = _first(root, "Contact")
    assert contact is not None, "no Contact stand-in written"
    assert contact.findtext(f"{AUC}ContactName") == "Not specified"
    report = _first(root, "Report")
    assert report is not None, "no Report stand-in written"
    scenarios = _tags(root, "Scenario")
    assert len(scenarios) == 1, "the stand-in report carries invented scenarios"


def test_idrefs_resolve_across_the_document(tmp_path):
    """Every IDref in an export points at an ID written by the same export."""
    model = _tiny_model(
        air_loops=[
            {
                "id": "AL-1",
                "name": "AHU 1",
                "system_type": "",
                "fan_airflow_lps": 0.0,
                "cooling_total_btuh": 0.0,
                "cooling_sensible_btuh": 0.0,
                "heating_btuh": 0.0,
                "sources": [],
            }
        ],
        zone_air_loop={"zone-z-1": "AL-1"},
    )
    root = ET.parse(write_buildingsync(model, tmp_path / "out.xml")).getroot()
    ids = {el.get("ID") for el in root.iter() if el.get("ID")}
    refs = [el.get(key) for el in root.iter() for key in el.attrib if key.endswith("IDref")]
    assert refs, "expected at least one IDref"
    assert all(ref in ids for ref in refs)
