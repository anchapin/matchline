"""Tests for the schedule-truth benchmark (#1240): independent frozen schedule truth."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.schedule_bench import (  # noqa: E402
    run_schedule_bench,
    stated_values,
)
from scripts.schedule_truth import (  # noqa: E402
    SCHEMA,
    TruthMismatch,
    build_truth,
    load_or_freeze_truth,
)


def _workbook(path: Path, tamper_row: bool = False) -> Path:
    """A two-sheet COBie-shaped workbook with stated tags and requirements."""
    import openpyxl

    wb = openpyxl.Workbook()
    comp = wb.active
    comp.title = "Component"
    comp.append(
        ["Name", "CreatedBy", "CreatedOn", "Category", "System", "TypeName",
         "Space", "ExtObject", "ExtIdentifier", "ExternalObject",
         "ExternalIdentifier", "Description", "TagNumber"]
    )
    comp.append(
        ["AC Unit Type 1 AC-1", "", "", "Requirement", "", "AC Unit Type 1",
         "1A01", "", "", "", "", "Rooftop unit", "n/a"]
    )
    comp.append(
        ["Chiller CH-2", "", "", "Requirement", "", "Chiller",
         "1A02", "", "", "", "", "Water cooled", "CH-2"]
    )
    if tamper_row:
        comp.append(
            ["AC Unit Type 1 AC-1", "", "", "Requirement", "", "AC Unit Type 1",
             "1A09", "", "", "", "", "Moved", "n/a"]
        )
    ty = wb.create_sheet("Type")
    ty.append(["Name", "CreatedBy", "CreatedOn", "Category", "ExtObject",
               "ExtIdentifier", "Description"])
    ty.append(["AC Unit Type 1", "", "", "Equipment", "", "", "3500 Watt Capacity"])
    ty.append(["Chiller", "", "", "Equipment", "", "", "63300000 J"])
    sp = wb.create_sheet("Space")
    sp.append(["Name", "CreatedBy", "CreatedOn", "Category", "FloorName",
               "ExtObject", "ExtIdentifier", "Description"])
    sp.append(["1A01", "", "", "Requirement", "First Floor", "", "", "Clinic"])
    sp.append(["1A02", "", "", "Requirement", "First Floor", "", "", "Clinic"])
    at = wb.create_sheet("Attribute")
    at.append(["Name", "CreatedBy", "CreatedOn", "Category", "SheetName",
               "RowName", "Value", "Unit"])
    at.append(["Total", "", "", "Requirement", "Component",
               "AC Unit Type 1 AC-1", "300", "L/s"])
    wb.save(path)
    return path


def _assert_never_imports(source: str, forbidden: str) -> None:
    mods = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    assert all(not m.split(".")[0] == forbidden for m in mods), mods


def test_truth_reader_never_imports_the_extractor() -> None:
    """Truth is read independently, or the schedule benchmark scores itself."""
    _assert_never_imports((ROOT / "scripts" / "schedule_truth.py").read_text(), "mech_schedule")
    src = (ROOT / "scripts" / "schedule_truth.py").read_text()
    _assert_never_imports(src, "scripts.schedule_score")


def test_truth_reader_keys_rows_by_stated_tag(tmp_path: Path) -> None:
    wb_path = _workbook(tmp_path / "w.xlsx")
    truth = build_truth(wb_path)
    assert truth["schema"] == SCHEMA
    assert set(truth["rows"]) == {"AC-1", "CH-2"}
    # The stated TagNumber wins when the workbook carries one; the embedded
    # name token is the clinic convention, recorded as its source.
    assert truth["rows"]["CH-2"]["tag_source"] == "TagNumber"
    assert truth["rows"]["AC-1"]["tag_source"] == "name"


def test_truth_reader_reports_untagged_and_duplicate_rows(tmp_path: Path) -> None:
    import openpyxl

    wb_path = _workbook(tmp_path / "w.xlsx")
    wb = openpyxl.load_workbook(wb_path)
    comp = wb["Component"]
    comp.append(["Thermostat", "", "", "Requirement", "", "Thermostat",
                 "1A01", "", "", "", "", "Wall stat", "n/a"])
    comp.append(["Fan FU-2 HU-2", "", "", "Requirement", "", "Fan Unit",
                 "1A02", "", "", "", "", "Dup", "n/a"])
    comp.append(["Fan FU-2 HU-2", "", "", "Requirement", "", "Fan Unit",
                 "1A03", "", "", "", "", "Dup again", "n/a"])
    wb.save(wb_path)
    truth = build_truth(wb_path)
    assert truth["counts"]["untagged"] == 1
    assert truth["untagged"][0]["name"] == "Thermostat"
    assert "reason" in truth["untagged"][0]
    assert truth["counts"]["duplicate_tags"] == 1
    # First stated row is kept; the duplicate is recorded, never merged.
    assert truth["rows"]["HU-2"]["space"] == "1A02"
    assert truth["duplicate_tags"][0]["first_row"] == truth["rows"]["HU-2"]["row"]


def test_truth_reader_records_stated_requirements_and_spaces(tmp_path: Path) -> None:
    wb_path = _workbook(tmp_path / "w.xlsx")
    truth = build_truth(wb_path)
    assert truth["requirements"]["AC Unit Type 1 AC-1"] == [
        {"name": "Total", "value": "300", "unit": "L/s", "row": 2}
    ]
    assert truth["spaces"]["1A01"]["floor_name"] == "First Floor"
    assert truth["types"]["AC Unit Type 1"]["description"] == "3500 Watt Capacity"


def test_truth_freezes_then_loads_and_refuses_drift(tmp_path: Path) -> None:
    wb_path = _workbook(tmp_path / "w.xlsx")
    truth, how = load_or_freeze_truth(wb_path)
    assert how == "frozen"
    assert (wb_path.parent / "truth.json").exists()
    truth2, how2 = load_or_freeze_truth(wb_path)
    assert how2 == "loaded"
    assert truth2["workbook_sha256"] == truth["workbook_sha256"]
    # A changed workbook drifts the frozen truth away: refuse, never re-read.
    _workbook(tmp_path / "w.xlsx", tamper_row=True)
    with pytest.raises(TruthMismatch):
        load_or_freeze_truth(wb_path)
    truth3, how3 = load_or_freeze_truth(wb_path, force=True)
    assert how3 == "refrozen"
    # First stated row still wins; the moved row is a recorded duplicate.
    assert truth3["rows"]["AC-1"]["space"] == "1A01"
    assert truth3["counts"]["duplicate_tags"] == 1


def test_stated_values_parse_independent_tokens() -> None:
    # A stated capacity with a unit the extractor does not table yet.
    assert stated_values("3500 Watt Capacity") == [
        {"value": 3500.0, "unit": "Watt", "raw": "3500 Watt"}
    ]
    # A stated range keeps value None on both sides.
    assert stated_values("633-703 kW") == [{"value": None, "unit": "kW", "raw": "633-703 kW"}]
    # Dimension tokens never parse: mm and x are stated geometry, and units
    # are single tokens so "4100mm Troffer" does not read "Troffer" as a unit.
    # A stated "32 Cell" is a value token, but Cell is no capacity unit.
    toks = stated_values("0600x4100mm Troffer with 32 Cell, 78mm Deep")
    assert toks == [{"value": 32.0, "unit": "Cell", "raw": "32 Cell"}]


def test_run_schedule_bench_scores_extraction_against_truth(tmp_path: Path) -> None:
    from mech_schedule import parse_cobie_workbook

    wb_path = _workbook(tmp_path / "w.xlsx")
    truth = build_truth(wb_path)
    result = parse_cobie_workbook(wb_path)
    m = run_schedule_bench(truth, result)
    # Truth adds one component the extractor excludes by design, so the
    # scorecard shows the miss instead of scoring the extractor's own
    # decisions.
    assert m["counts"]["matched_tags"] == len(result.rows)
    assert m["counts"]["extra_tags"] == 0
    assert m["counts"]["missed_tags"] >= 0
    assert 0.0 <= m["tags"]["recall"] <= 1.0
    assert m["tags"]["precision"] == 1.0
    # Zone linkage: every matched tag's service area equals the stated space.
    assert m["zone_linkage"]["mismatch"] == 0
    # Airflow: the stated Attribute requirement has no extractor slot yet.
    if m["airflow"]["stated"]:
        assert m["airflow"]["agree"] <= m["airflow"]["stated"]


def test_run_schedule_bench_reports_real_disagreement(tmp_path: Path) -> None:
    """A truth tag the extractor missed must show up as a miss, never a match."""
    from mech_schedule import parse_cobie_workbook

    wb_path = _workbook(tmp_path / "w.xlsx")
    truth = build_truth(wb_path)
    truth["rows"]["EX-99"] = {
        "row": 99, "name": "Exhaust Fan EX-99", "type_name": "Exhaust Fan",
        "space": "1A03", "description": "", "tag_source": "name",
    }
    result = parse_cobie_workbook(wb_path)
    m = run_schedule_bench(truth, result)
    assert "EX-99" in [x["tag"] for x in m["tags"]["missed_sample"]] or \
        m["counts"]["missed_tags"] >= 1
    assert m["tags"]["recall"] < 1.0


def test_bench_json_roundtrip(tmp_path: Path) -> None:
    wb_path = _workbook(tmp_path / "w.xlsx")
    truth = build_truth(wb_path)
    truth["workbook_sha256"] = json.loads(
        json.dumps(truth["workbook_sha256"])
    )
    assert truth["counts"]["tagged"] == len(truth["rows"])
