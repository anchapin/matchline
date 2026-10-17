"""Tests for the grouped-vs-flat schedule-row scorer (#1112)."""

from __future__ import annotations

from mech_schedule import MechScheduleRow
from scripts.schedule_noise import degrade_texts
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


# --- KR / EM / VF1 (#1114) ---


def test_kr_is_identity_agnostic():
    truth = [_row("AC-1", "fan_coil", "1B21", {"value": 3.0, "unit": "kW"})]
    pred = [_row("ZZ-9", "fan_coil", "1B21", {"value": 3.0, "unit": "kW"})]
    s = score_schedule_rows(pred, truth)
    assert s.kr == 1.0 and s.grouped == 0.0


def test_em_equals_grouped():
    truth = [_row("AC-1", "fan_coil", "1B21", None)]
    s = score_schedule_rows([_row("AC-1", "fan_coil", "1B21", None)], truth)
    assert s.em == s.grouped == 1.0
    s2 = score_schedule_rows([_row("AC-1", "ahu", "1B21", None)], truth)
    assert s2.em == s2.grouped == 0.0


def test_vf1_folds_case_and_whitespace():
    truth = [_row("AC-1", "Fan Coil ", "1B21", None)]
    s = score_schedule_rows([_row("AC-1", "fan coil", "1B21", None)], truth)
    assert s.vf1 == 1.0 and s.flat_f1 < 1.0


def test_vf1_numeric_capacity_tolerance():
    truth = [_row("AC-1", "ahu", "1B21", {"value": 3.0001, "unit": "kW"})]
    s = score_schedule_rows([_row("AC-1", "ahu", "1B21", {"value": 3.0, "unit": "kW"})], truth)
    assert s.vf1 == 1.0
    s2 = score_schedule_rows([_row("AC-1", "ahu", "1B21", {"value": 3.0, "unit": "tons"})], truth)
    assert s2.vf1 < 1.0


def test_ocr_confusion_is_deterministic_and_map_only():
    texts = ["VAV BOX", "6 kW", "1B21"]
    a = degrade_texts(texts, "ocr_confusion", 2026, rate=0.15)
    b = degrade_texts(texts, "ocr_confusion", 2026, rate=0.15)
    assert a == b
    for got, want in zip(a, texts):
        allowed = set("O0l1IS5B8Z")
        assert all(c in allowed or c in want for c in got if c not in want)


def test_glyph_dropout_is_deterministic_and_removes():
    texts = ["EXHAUST FAN 1200 CFM"]
    a = degrade_texts(texts, "glyph_dropout", 2026, rate=0.1)
    b = degrade_texts(texts, "glyph_dropout", 2026, rate=0.1)
    assert a == b and len(a[0]) <= len(texts[0])
    wiped = degrade_texts(texts, "glyph_dropout", 2026, rate=1.0)
    assert wiped == [""]


def test_equipment_row_mapping_scores_clean_truth():
    from scripts.eval_schedule_noise import equipment_row_to_dict, truth_rows

    rec = {
        "tag": "AC-1",
        "values": {"TYPE": "FAN COIL UNIT", "CAPACITY": "12 kW"},
    }
    d = equipment_row_to_dict(rec)
    s = score_schedule_rows([d], truth_rows(), fields=("system_type", "capacity"))
    ac1 = [r for r in truth_rows() if r["tag"] == "AC-1"][0]
    assert d["fields"] == ac1["fields"] and s.grouped >= 1 / 5
