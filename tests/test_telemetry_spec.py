"""Tests for the minimum-telemetry spec (#1003).

The spec defines the allowlist an emitted payload must satisfy, the
exclusion list of building-specific data, and the consent gates. The
emitter (#1006) accepts only these fields; these tests pin the spec.
"""

from pathlib import Path

import telemetry_spec
from telemetry_spec import SPEC_VERSION, emission_enabled, validate_payload

DOCS_PAGE = Path(__file__).resolve().parent.parent / "docs" / "telemetry-data.md"


def _payload(**over):
    base = {
        "spec_version": SPEC_VERSION,
        "consent": True,
        "conf_distributions": {"door": {"0.9-1.0": 12}},
        "corrections_by_type": {"swap_door_window": 3},
        "check_failures": {"storey_height_missing": 2},
        "binned_aggregates": [{"metric": "space_count", "bin": "10-20", "count": 7}],
        "stage_outcomes": [{"stage": "build_model", "outcome": "ok", "seconds": 1.2}],
    }
    base.update(over)
    return base


def test_minimal_conforming_payload_passes():
    assert validate_payload(_payload()) == []


def test_unknown_top_level_field_fails():
    errs = validate_payload(_payload(extra_field=1))
    assert any("outside the allowlist" in e for e in errs)


def test_missing_required_field_fails():
    payload = _payload()
    del payload["corrections_by_type"]
    errs = validate_payload(payload)
    assert any("missing required fields" in e for e in errs)


def test_wrong_spec_version_fails():
    errs = validate_payload(_payload(spec_version="matchline.telemetry-spec/0"))
    assert any("spec_version" in e for e in errs)


def test_geometry_key_fails():
    for key in (
        "coordinates",
        "polygon_ring",
        "room_label",
        "sheet_image",
        "building_name",
        "file_path",
    ):
        errs = validate_payload(_payload(**{key: 1}))
        assert any("excluded field" in e for e in errs), key


def test_nested_geometry_key_fails():
    errs = validate_payload(
        _payload(stage_outcomes=[{"stage": "s", "outcome": "ok", "coordinates": [1, 2]}])
    )
    assert any("excluded field" in e and "coordinates" in e for e in errs)


def test_long_string_value_fails():
    payload = _payload(conf_distributions={"door": {"0.9-1.0": "x" * 200}})
    errs = validate_payload(payload)
    assert any("longer than" in e for e in errs)


def test_consent_false_fails():
    errs = validate_payload(_payload(consent=False))
    assert any("opt-in consent" in e for e in errs)


def test_local_only_never_appears_on_a_payload():
    errs = validate_payload(_payload(local_only=True))
    assert any("local-only" in e for e in errs)


def test_non_dict_payload_fails():
    assert validate_payload([1, 2]) != []  # type: ignore[arg-type]


def test_emission_gates():
    assert emission_enabled(consent=True, local_only=False) is True
    assert emission_enabled(consent=True, local_only=True) is False
    assert emission_enabled(consent=False, local_only=False) is False
    assert emission_enabled(consent=False, local_only=True) is False


def test_allowlist_itself_contains_no_excluded_keys():
    for field in telemetry_spec.ALLOWED_FIELDS:
        assert validate_payload({field: 1}) == [] or all(
            "excluded field" not in e for e in validate_payload({field: 1})
        ), field


def test_data_page_states_version_and_gates():
    text = DOCS_PAGE.read_text()
    assert SPEC_VERSION in text
    assert "opt-in" in text
    assert "local-only" in text
    assert "data-contribution-agreement.md" in text
