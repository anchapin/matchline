"""#1293: matchline's own Schematron policy gate over BuildingSync output.

The upstream ASHRAE 211 use case (L000, committed byte-identical) checks
that an export carries what an auditor's use case needs. This gate checks
the repo's own business rules for the files the writer produces:

* zone-load-impacting inputs must be present before, and traceable to, the
  HVAC sizing elements they size (ML-POL-001, ML-POL-002);
* envelope thermal values must be traceable to drawing data or ASHRAE
  90.1-2019 Table 5.5 (ML-POL-003, ML-POL-004).

The ruleset lives in assets/schematron/matchline_policy-1.0.0.sch and runs
through the same engine and reporting shape as the use case gate
(``bem.buildingsync_export.validate_buildingsync_policy``), so a failure
names the rule id (ML-POL-*) and what is missing instead of a bare assert.

The committed example export carries no HVAC system and no envelope, so the
rules are vacuous on it; the writer-export test below is what proves the
rules are satisfiable by what the writer actually emits, and the committed
sample-violation fixture is what proves they fire.
"""

import hashlib
from pathlib import Path

from bem.buildingsync_export import (
    POLICY_SCHEMATRON_PATH,
    SCHEMATRON_DIR,
    validate_buildingsync,
    validate_buildingsync_policy,
    write_buildingsync,
)
from tests.test_buildingsync_export import _tiny_model

EXAMPLE_PATH = (
    Path(__file__).resolve().parent.parent / "assets" / "examples" / "matchline_l000_example.xml"
)
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "buildingsync_policy_violation.xml"

# SHA-256 of the upstream L000 use case, recorded in assets/schematron/README.md.
# The upstream artifact is never edited; this test is the guard for that.
UPSTREAM_L000_SHA256 = "62112eba446286ab6ae9da73e8e47634168319a8bc308ca568f10be3c1d03134"

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


def _policy_model():
    """A model whose export exercises every policy rule (HVAC + envelope)."""
    model = _tiny_model(
        air_loops=[_AIR_LOOP],
        zone_air_loop={"zone-z-1": "AL-1"},
        zone_terminals={"z-1": [("d-1", "A", 1.0, 1.0)]},
        zone_flow_per_area={"zone-z-1": 0.0125},
        roof_u_value_w_m2k=0.18,
        slab_u_value_w_m2k=0.30,
        opening_constructions={
            "t55-window": {"name": "Double pane", "category": "window", "u": 2.0, "shgc": 0.4}
        },
    )
    model.spaces[0].wall_u_value_w_m2k = 0.35
    return model


def test_policy_ruleset_is_committed():
    """The ruleset is a new file next to the upstream one, not a copy of it."""
    assert POLICY_SCHEMATRON_PATH.exists()
    assert POLICY_SCHEMATRON_PATH.parent == SCHEMATRON_DIR
    assert POLICY_SCHEMATRON_PATH.name != "L000_Audit-1.0.0.sch"


def test_upstream_use_case_artifact_is_untouched():
    """The upstream L000 file stays byte-identical (see assets/schematron/README.md)."""
    upstream = SCHEMATRON_DIR / "L000_Audit-1.0.0.sch"
    digest = hashlib.sha256(upstream.read_bytes()).hexdigest()
    assert digest == UPSTREAM_L000_SHA256, (
        "assets/schematron/L000_Audit-1.0.0.sch is an upstream artifact and must never be edited"
    )


def test_committed_example_passes_the_policy_rules():
    """The committed example export stays policy-clean."""
    ok, failures = validate_buildingsync_policy(EXAMPLE_PATH)
    assert ok, failures


def test_writer_export_passes_the_policy_rules(tmp_path):
    """The rules are satisfiable by what the writer actually emits.

    Without this the gate could pass only by being vacuous: here the export
    carries a sized HVAC system, a fan, a served zone with a floor area and
    a scheduled airflow, and envelope values.
    """
    path = write_buildingsync(_policy_model(), tmp_path / "out.xml")
    ok, errors = validate_buildingsync(path)
    assert ok, errors
    ok, failures = validate_buildingsync_policy(path)
    assert ok, failures


def test_sample_violation_fixture_fails_the_policy_rules():
    """The committed sample-violation fixture trips the rules it breaks.

    The fixture is a writer export with three policy violations worked into
    it: the served zone lost every zone-load input, the fan lost the link to
    the system it serves, and the fenestration value lost its source
    construction.
    """
    assert FIXTURE_PATH.exists()
    ok, failures = validate_buildingsync_policy(FIXTURE_PATH)
    assert not ok, "the sample-violation fixture must not pass the policy rules"
    report = "\n".join(failures)
    for rule in ("ML-POL-001", "ML-POL-002", "ML-POL-003"):
        assert rule in report, f"{rule} did not fire on the fixture:\n{report}"
    # Actionable, not bare: each message names the element and what is wrong.
    assert "HVACSystemType-AL-1" in report and "zone-load-impacting input" in report
    assert "FanSystemType-AL-1" in report and "LinkedSystemID" in report
    assert "FenestrationSystemType-1" in report and "source construction" in report


def test_sample_violation_fixture_is_still_a_valid_buildingsync_document():
    """The fixture breaks policy, not structure: it stays XSD-valid."""
    ok, errors = validate_buildingsync(FIXTURE_PATH)
    assert ok, errors


def test_untouched_rules_stay_quiet_on_the_fixture():
    """ML-POL-004 is not broken by the fixture: every envelope element has a value."""
    ok, failures = validate_buildingsync_policy(FIXTURE_PATH)
    assert not ok
    assert "ML-POL-004" not in "\n".join(failures)
