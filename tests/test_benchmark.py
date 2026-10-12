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
    assert "| ghost | missing |" in md
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
    )
    md = render_scoreboard([row], openstudio=True)
    assert "42 / 41" in md
    assert "3.1%" in md
    assert "29/30" in md
    assert "| ok-building | ok |" in md
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
    assert "| no-doors | ok |" in md
    assert "17" not in md
