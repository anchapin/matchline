"""Tests for scripts/benchmark.py (#748): config, metrics, scoreboard, missing data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.benchmark import (  # noqa: E402
    Building,
    Row,
    _convention_report,
    _gbxml_surface_counts,
    _hvac_columns,
    _mape,
    _median,
    evaluate_building,
    load_config,
    render_scoreboard,
)


def test_mape_and_median() -> None:
    assert _median([3.0, 1.0, 2.0]) == 2.0
    assert _median([1.0, 2.0]) == 1.5
    assert _median([]) is None
    assert _mape({"a": 9.0, "b": 2.0}, {"a": 10.0, "b": 2.0, "c": 5.0}) == 0.05
    assert _mape({}, {"a": 1.0}) is None
    # zero-truth keys are never divided by
    assert _mape({"a": 1.0}, {"a": 0.0}) is None


def test_load_config_resolves_relative_and_tilde(tmp_path: Path) -> None:
    cfg = {
        "buildings": [
            {
                "name": "rel",
                "arch_ifc": "arch.ifc",
                "hvac_ifc": None,
                "drawing_set": "~/x.pdf",
            }
        ]
    }
    p = tmp_path / "bench.json"
    p.write_text(json.dumps(cfg))
    got = load_config(p)
    assert got[0].name == "rel"
    assert got[0].arch_ifc == tmp_path / "arch.ifc"
    assert got[0].hvac_ifc is None
    assert str(got[0].drawing_set).startswith(str(Path.home()))


def test_missing_building_is_reported_not_skipped(tmp_path: Path) -> None:
    row = evaluate_building(Building(name="ghost"), tmp_path)
    assert row.status == "missing"
    assert "DATASETS.md" in row.detail
    md = render_scoreboard([row], openstudio=False)
    assert "| ghost |  | missing |" in md
    assert "OpenStudio column skipped" in md


def test_scoreboard_renders_a_scored_row() -> None:
    row = Row(
        name="ok-building",
        rooms_truth=42,
        rooms_model=41,
        area_err=0.031,
        wall_len_err=0.004,
        windows_truth=12,
        windows_model=12,
        window_area_err=0.02,
        lpd_median_err=0.0,
        terminals_scored=30,
        terminals_correct=29,
        cross_frontend="ok",
        openstudio="pass",
        schema="IFC4",
    )
    md = render_scoreboard([row], openstudio=True)
    assert "42 / 41" in md
    assert "3.1%" in md
    assert "29/30" in md
    assert "| ok-building | IFC4 | ok |" in md
    assert "OpenStudio column skipped" not in md


def test_error_row_when_arch_ifc_unreadable(tmp_path: Path) -> None:
    bad = tmp_path / "arch.ifc"
    bad.write_bytes(b"not an ifc file at all")
    row = evaluate_building(Building(name="bad", arch_ifc=bad), tmp_path)
    # import_ifc rejects it; whatever the failure mode, the harness reports it
    assert row.status in ("error", "missing")
    assert row.detail


def test_scoreboard_renders_doors_column() -> None:
    row = Row(name="with-doors", doors_truth=17, doors_model=17)
    md = render_scoreboard([row], openstudio=False)
    assert "| 17 / 17 |" in md
    assert "doors (t / m)" in md


def test_scoreboard_leaves_doors_empty_without_a_count() -> None:
    row = Row(name="no-doors")
    md = render_scoreboard([row], openstudio=False)
    # two adjacent empty cells: no doors, no window area error
    assert "| no-doors |  | ok |" in md
    assert "17" not in md


def test_raw_entity_counts_reads_the_file_not_the_model(tmp_path: Path) -> None:
    """Truth windows/doors come from the entity table, independent of import."""
    import ifcopenshell

    path = tmp_path / "raw.ifc"
    f = ifcopenshell.file(schema_identifier="IFC4")
    for i in range(3):
        f.create_entity("IfcWindow", GlobalId=f"w{i}", OverallHeight=1.0, OverallWidth=1.0)
    for i in range(2):
        f.create_entity("IfcDoor", GlobalId=f"d{i}", OverallHeight=2.0, OverallWidth=0.9)
    f.write(str(path))

    from scripts.benchmark import _raw_entity_counts

    assert _raw_entity_counts(path) == {"windows": 3, "doors": 2}


def test_hvac_columns_read_evaluate_shape(tmp_path, monkeypatch) -> None:
    """The HVAC columns come from evaluate()'s per-storey summarize shape.

    #964: the harness treated evaluate()'s return as the CLI report dict
    (KeyError: terminals_scored) and this code path had never run against a
    real hvac_ifc because the clinic row was missing locally.
    """
    hvac = tmp_path / "hvac.ifc"
    hvac.write_text("x")
    monkeypatch.setattr(
        "scripts.validate_clinic_hvac.load",
        lambda p: ({"L1": []}, [1, 2, 3, 4, 5]),
    )
    monkeypatch.setattr(
        "scripts.validate_clinic_hvac.evaluate",
        lambda rooms, terminals: {"L1": {"correct": 3}, "L2": {"correct": 1}},
    )
    scored, correct = _hvac_columns(hvac)
    assert scored == 5
    assert correct == 4


def test_scoreboard_surface_count_columns() -> None:
    """#972: the scoreboard carries envelope/roof/surface simplification columns."""
    row = Row(
        name="x",
        status="ok",
        walls_in=120,
        walls_out=34,
        roof_in=9,
        roof_out=6,
        surfaces_out=214,
    )
    md = render_scoreboard([row], openstudio=False)
    assert "envelope (in / out)" in md
    assert "roof (in / out)" in md
    assert "surfaces" in md
    assert "| 120 / 34 |" in md
    assert "| 9 / 6 |" in md
    assert "| 214 |" in md
    blank = render_scoreboard([Row(name="y", status="ok")], openstudio=False)
    assert "| y |  | ok |" in blank


def test_gbxml_surface_counts(tmp_path: Path) -> None:
    """#972: surfaces counted from the exported file, namespace-agnostic."""
    p = tmp_path / "m.gbxml"
    p.write_text(
        '<?xml version="1.0"?><gbXML xmlns="http://www.gbxml.org/schema">'
        '<Surface id="1"/><Surface id="2"/><Opening id="1"/></gbXML>'
    )
    assert _gbxml_surface_counts(p) == 2
    assert _gbxml_surface_counts(tmp_path / "nope.gbxml") is None


def test_convention_report_written_with_the_run(tmp_path: Path) -> None:
    # #1165: each benchmark run attaches a convention report next to the
    # scoreboard, so published accuracy numbers carry their conventions.
    from tests.model_factory import make_clean_model

    out = tmp_path / "bsi-clinic-convention.json"
    verdict = _convention_report(make_clean_model(), out)
    assert verdict == "written" and out.exists()
    payload = json.loads(out.read_text())
    assert payload["schema"] == "matchline.convention_report/1"
    assert "volume_bias" in payload and "non_room_area" in payload


def test_convention_report_failure_is_recorded_not_raised(tmp_path: Path) -> None:
    class Broken:
        name = "broken"

        @property
        def spaces(self):
            raise RuntimeError("no spaces")

    verdict = _convention_report(Broken(), tmp_path / "b.json")
    assert verdict.startswith("error: ") and not (tmp_path / "b.json").exists()
