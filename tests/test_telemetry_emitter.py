"""Tests for the allowlist emitter (#1006) on the telemetry spec (#1003)."""

from __future__ import annotations

import inspect

import pytest

from telemetry_emitter import CONF_BINS, STAGE_OUTCOMES, EmitterBuildError, TelemetryEmitter
from telemetry_spec import EXCLUDED_KEY_PATTERNS, SPEC_VERSION, validate_payload


def _emitter() -> TelemetryEmitter:
    return TelemetryEmitter(consent=True)


# -- construction: allowlist only ------------------------------------------


def test_build_payload_signature_accepts_no_fields():
    params = [
        p for p in inspect.signature(TelemetryEmitter.build_payload).parameters if p != "self"
    ]
    assert params == [], "the emitter takes no extra fields, by construction"


def test_payload_carries_only_allowlisted_groups():
    payload = _emitter().build_payload()
    assert set(payload) == {
        "spec_version",
        "consent",
        "conf_distributions",
        "corrections_by_type",
        "check_failures",
        "binned_aggregates",
        "stage_outcomes",
    }
    assert payload["spec_version"] == SPEC_VERSION
    assert payload["consent"] is True


def test_blank_payload_validates_against_the_spec():
    assert validate_payload(_emitter().build_payload()) == []


# -- record methods wire the spec's metrics --------------------------------


def test_confidence_distributions_bin_into_five_buckets():
    emitter = _emitter()
    for value in (0.0, 0.05, 0.25, 0.55, 0.75, 0.95, 1.0):
        emitter.record_confidence("wall", value)
    assert emitter.build_payload()["conf_distributions"]["wall"] == {
        "0.0-0.2": 2,
        "0.2-0.4": 1,
        "0.4-0.6": 1,
        "0.6-0.8": 1,
        "0.8-1.0": 2,
    }


def test_confidence_bins_default_to_zero():
    emitter = _emitter()
    emitter.record_confidence("door", 0.5)
    bins = emitter.build_payload()["conf_distributions"]["door"]
    assert bins["0.4-0.6"] == 1
    assert [bins[b] for b in CONF_BINS if b != "0.4-0.6"] == [0, 0, 0, 0]


def test_confidence_rejects_out_of_range_values():
    emitter = _emitter()
    with pytest.raises(EmitterBuildError):
        emitter.record_confidence("wall", 1.5)
    with pytest.raises(EmitterBuildError):
        emitter.record_confidence("wall", float("nan"))


def test_corrections_count_by_type():
    emitter = _emitter()
    emitter.record_correction("swap_door_window")
    emitter.record_correction("swap_door_window")
    emitter.record_correction("rescale")
    assert emitter.build_payload()["corrections_by_type"] == {
        "swap_door_window": 2,
        "rescale": 1,
    }


def test_check_failures_count_frequencies():
    emitter = _emitter()
    for _ in range(3):
        emitter.record_check_failure("u_value_window")
    emitter.record_check_failure("orientation_rule")
    assert emitter.build_payload()["check_failures"] == {
        "u_value_window": 3,
        "orientation_rule": 1,
    }


def test_binned_aggregates_record_counts_and_short_labels():
    emitter = _emitter()
    emitter.record_binned("wall_area", "0-500")
    emitter.record_binned("wall_area", "0-500", 2)
    emitter.record_binned("wall_area", "500-1000")
    assert emitter.build_payload()["binned_aggregates"] == {
        "wall_area": {"0-500": 3, "500-1000": 1}
    }


def test_binned_rejects_negative_counts():
    with pytest.raises(EmitterBuildError):
        _emitter().record_binned("wall_area", "0-500", -1)


def test_stage_outcomes_and_timings():
    emitter = _emitter()
    emitter.record_stage("takeoff", "ok", seconds=4.25)
    emitter.record_stage("takeoff", "error", seconds=0.75)
    emitter.record_stage("bem", "ok", seconds=10.0)
    payload = emitter.build_payload()
    assert payload["stage_outcomes"]["takeoff"] == {
        "outcome": "error",
        "attempts": 2,
        "seconds": 5.0,
    }
    assert payload["stage_outcomes"]["bem"] == {"outcome": "ok", "attempts": 1, "seconds": 10.0}


def test_stage_outcome_must_be_a_known_value():
    with pytest.raises(EmitterBuildError):
        _emitter().record_stage("takeoff", "fine_i_guess")
    assert sorted(STAGE_OUTCOMES) == ["error", "ok", "skipped"]


# -- strip at source: building-specific data fails at record time ----------


@pytest.mark.parametrize(
    "key",
    [
        "room_102",  # room identifiers
        "space_3",  # space ids
        "level_2_office",  # level labels
        "sheet_A101",  # sheet references
        "north_east_coords",  # coordinates
        "polygon_1",  # geometry
        "project_name",  # names
        "floor_plan_pdf",  # images / documents
        "owner",  # identity
    ],
)
def test_bucket_key_matching_an_exclusion_pattern_raises(key):
    for pat in EXCLUDED_KEY_PATTERNS:
        if pat.search(key):
            break
    emitter = _emitter()
    with pytest.raises(EmitterBuildError):
        emitter.record_correction(key)
    with pytest.raises(EmitterBuildError):
        emitter.record_check_failure(key)
    with pytest.raises(EmitterBuildError):
        emitter.record_confidence(key, 0.5)
    with pytest.raises(EmitterBuildError):
        emitter.record_stage(key, "ok")
    with pytest.raises(EmitterBuildError):
        emitter.record_binned(key, "0-500")


def test_no_building_specific_data_survives_a_rich_run():
    emitter = _emitter()
    for detection_class, value in (
        ("wall", 0.91),
        ("door", 0.44),
        ("window", 0.78),
        ("curtain_wall", 0.83),
    ):
        emitter.record_confidence(detection_class, value)
    emitter.record_correction("swap_door_window")
    emitter.record_check_failure("u_value_window")
    emitter.record_binned("wall_area", "0-500")
    emitter.record_stage("takeoff", "ok", seconds=2.0)
    payload = emitter.build_payload()

    assert validate_payload(payload) == []
    blob = repr(payload)
    for forbidden in (
        "coords",
        "polygon",
        "geometry",
        "sheet",
        "image",
        "pdf",
        "label",
        "room",
        "owner",
        "path",
    ):
        assert forbidden not in blob
    assert all(isinstance(v, (int, float, str)) or isinstance(v, dict) for v in _flatten(payload))


def _flatten(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _flatten(v)
    elif isinstance(value, list):
        for v in value:
            yield from _flatten(v)
    else:
        yield value


# -- gates -----------------------------------------------------------------


def test_no_consent_disables_emission():
    assert TelemetryEmitter(consent=False).build_payload() is None


def test_local_only_disables_emission_entirely():
    emitter = TelemetryEmitter(consent=True, local_only=True)
    emitter.record_stage("takeoff", "ok", seconds=1.0)
    emitter.record_correction("rescale")
    assert emitter.build_payload() is None


def test_disabled_emitter_still_refuses_building_specific_keys():
    emitter = TelemetryEmitter(consent=True, local_only=True)
    with pytest.raises(EmitterBuildError):
        emitter.record_correction("room_102")
