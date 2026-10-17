"""Tests for the grouped-vs-flat schedule-row scorer (#1112)."""

from __future__ import annotations

from mech_schedule import MechScheduleRow
from scripts.schedule_score import mech_row_to_dict, score_schedule_rows


def _row(tag, system_type, service_area, capacity):
    return {
        "tag": tag,
        "fields": {"system_type": system_type, "service_area": service_area, "capacity": capacity},
    }


def test_exact_match_scores_one_on_both():
    truth = [_row("AC-1", "fan_coil", "1B21", {"value": 3.0, "unit": "kW"})]
    s = score_schedule_rows(truth, truth)
    assert s.grouped == 1.0 and s.flat_f1 == 1.0 and s.crossed == 0


def test_crossed_row_flatters_flat_but_not_grouped():
    truth = [
        _row("AC-1", "fan_coil", "1B21", {"value": 3.0, "unit": "kW"}),
        _row("AC-2", "fan_coil", "1B22", {"value": 5.0, "unit": "kW"}),
    ]
    pred = [
        _row("AC-1", "fan_coil", "1B21", {"value": 5.0, "unit": "kW"}),
        _row("AC-2", "fan_coil", "1B22", {"value": 3.0, "unit": "kW"}),
    ]
    s = score_schedule_rows(pred, truth)
    assert s.matched == 2 and s.crossed == 2
    assert 0.0 < s.flat_f1 < 1.0
    assert s.grouped == 0.0


def test_missing_tag_drops_grouped_and_recall():
    truth = [_row("AC-1", "fan_coil", "1B21", None), _row("EF1-1", "exhaust_fan", "1B21", None)]
    pred = [_row("AC-1", "fan_coil", "1B21", None)]
    s = score_schedule_rows(pred, truth)
    assert s.missing_tags == ["EF1-1"]
    assert s.grouped == 0.5
    assert s.flat_recall < s.flat_precision


def test_extra_tag_is_reported_not_scored_against_grouped():
    truth = [_row("AC-1", "fan_coil", "1B21", None)]
    pred = [_row("AC-1", "fan_coil", "1B21", None), _row("ZZ-9", "other", "", None)]
    s = score_schedule_rows(pred, truth)
    assert s.extra_tags == ["ZZ-9"] and s.grouped == 1.0
    assert s.flat_precision < 1.0


def test_duplicate_tags_are_excluded_and_flagged():
    truth = [_row("AC-1", "fan_coil", "1B21", None)]
    pred = [_row("AC-1", "fan_coil", "1B21", None), _row("AC-1", "ahu", "1B21", None)]
    s = score_schedule_rows(pred, truth)
    assert s.duplicate_tags == ["AC-1"]
    assert s.matched == 0 and s.grouped == 0.0


def test_capacity_compares_value_and_unit_not_raw():
    truth = [_row("AC-1", "ahu", "1B21", {"value": 633.0, "unit": "kW"})]
    same = score_schedule_rows([_row("AC-1", "ahu", "1B21", {"value": 633.0, "unit": "kW"})], truth)
    assert same.grouped == 1.0
    unit_only = score_schedule_rows(
        [_row("AC-1", "ahu", "1B21", {"value": 633.0, "unit": "tons"})], truth
    )
    assert unit_only.crossed == 1


def test_mech_row_adapter_carries_tag_and_capacity_pair():
    row = MechScheduleRow(
        tag="AC-1",
        name="AC Unit Type 1 AC-1",
        type_name="FC 3kW",
        type_description="3 kW",
        category="Mechanical",
        description="",
        service_area="1B21",
        system_type="fan_coil",
        confidence="high",
        capacity={"value": 3.0, "unit": "kW", "raw": "3 kW", "source": "type_description"},
    )
    d = mech_row_to_dict(row)
    assert d["tag"] == "AC-1"
    assert d["fields"]["capacity"] == {"value": 3.0, "unit": "kW"}
    s = score_schedule_rows([d], [{"tag": "AC-1", "fields": d["fields"]}])
    assert s.grouped == 1.0


def test_summary_renders_both_scores_and_the_audit():
    s = score_schedule_rows([], [])
    assert "grouped 1.000" in s.summary() and "flat F1 1.000" in s.summary()
