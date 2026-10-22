"""#1239: CI gate over BuildingSync exports (XSD + ASHRAE 211 L000 use case).

Layered with the #1238 tests: those cover the mapping and round-trip shape;
this file covers the validation gate itself -- the export staying XSD-valid
across writer paths, the audit-context layer emitting only caller-supplied
facts, and the committed L000 example staying current and valid.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from bem.buildingsync_export import (
    AUC_NS,
    SCHEMATRON_DIR,
    AuditContext,
    validate_buildingsync,
    validate_buildingsync_use_case,
    write_buildingsync,
)
from tests.test_buildingsync_export import _tiny_model

AUC = f"{{{AUC_NS}}}"

EXAMPLE_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "examples" / "matchline_l000_example.xml"
)

# A complete L000 context: everything the use case requires, caller-supplied.
FULL_AUDIT = AuditContext(
    building_classification="Commercial",
    occupancy_classification="Office",
    year_of_construction=1998,
    climate_zone="6A",
    site_energy_use_intensity=55.0,
    energy_cost_index=1.85,
    benchmark_tool="Portfolio Manager",
    benchmark_year=2024,
    benchmark_value=75.0,
)

_AIR_LOOP = {
    "id": "AL-1",
    "name": "AHU 1",
    "system_type": "VariableAirVolume",
    "fan_airflow_lps": 1.5,
    "cooling_total_btuh": 24000.0,
    "cooling_sensible_btuh": 18000.0,
    "heating_btuh": 30000.0,
    "sources": ["pdf_schedule_equipment"],
}


def test_full_audit_context_passes_xsd_and_l000(tmp_path):
    model = _tiny_model(
        air_loops=[_AIR_LOOP],
        zone_air_loop={"zone-z-1": "AL-1"},
        zone_terminals={"z-1": [("d-1", "A", 1.0, 1.0)]},
    )
    path = write_buildingsync(model, tmp_path / "out.xml", audit=FULL_AUDIT)
    ok, errs = validate_buildingsync(path)
    assert ok, errs
    ok, failures = validate_buildingsync_use_case(path)
    assert ok, failures


def test_partial_audit_context_emits_only_supplied_facts(tmp_path):
    """Building facts without energy values: no invented numbers or scenarios."""
    path = write_buildingsync(
        _tiny_model(),
        tmp_path / "out.xml",
        audit=AuditContext(
            building_classification="Commercial",
            occupancy_classification="Office",
            year_of_construction=1998,
            climate_zone="6A",
        ),
    )
    root = ET.parse(path).getroot()
    ok, errs = validate_buildingsync(path)
    assert ok, errs
    report = root.find(f".//{AUC}Report")
    scenario = report.find(f"{AUC}Scenarios/{AUC}Scenario")
    assert scenario.findtext(f"{AUC}ScenarioName") == "As-is"
    assert scenario.find(f"{AUC}ScenarioType") is None, "invented a scenario type"
    assert scenario.find(f"{AUC}AllResourceTotals") is None, "invented energy totals"
    assert len(report.findall(f"{AUC}Scenarios/{AUC}Scenario")) == 1, "invented a benchmark"
    assert report.findtext(f"{AUC}ASHRAEAuditLevel") == "Preliminary Energy-Use Analysis"
    assert root.findtext(f".//{AUC}BuildingClassification") == "Commercial"
    # L000 honestly fails: the measured scenario it requires is absent.
    ok, failures = validate_buildingsync_use_case(path)
    assert not ok and failures


def test_unaudited_export_has_no_audit_furniture(tmp_path):
    path = write_buildingsync(_tiny_model(), tmp_path / "out.xml")
    root = ET.parse(path).getroot()
    ok, errs = validate_buildingsync(path)
    assert ok, errs
    for tag in (
        "YearOfConstruction",
        "BuildingClassification",
        "OccupancyClassification",
        "ClimateZoneType",
        "Address",
        "ASHRAEAuditLevel",
    ):
        assert root.find(f".//{AUC}{tag}") is None, f"{tag} emitted without audit context"
    ok, failures = validate_buildingsync_use_case(path)
    assert not ok and failures


def test_committed_example_is_current_and_valid():
    """The committed L000 example stays byte-identical to the writer's output."""
    rebuild = EXAMPLE_PATH.with_name("matchline_l000_example.rebuild.xml")
    try:
        write_buildingsync(_tiny_model(), rebuild, audit=FULL_AUDIT)
        assert rebuild.read_bytes() == EXAMPLE_PATH.read_bytes(), (
            "assets/examples/matchline_l000_example.xml is stale; "
            "regenerate it with the FULL_AUDIT context from this test"
        )
    finally:
        rebuild.unlink(missing_ok=True)
    ok, errs = validate_buildingsync(EXAMPLE_PATH)
    assert ok, errs
    ok, failures = validate_buildingsync_use_case(EXAMPLE_PATH)
    assert ok, failures


def test_schematron_committed():
    assert (SCHEMATRON_DIR / "L000_Audit-1.0.0.sch").exists()
