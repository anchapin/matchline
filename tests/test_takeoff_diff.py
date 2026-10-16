"""Takeoff diff between two drawing-set revisions (#993)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import takeoff_diff  # noqa: E402
from cli import main as cli_main  # noqa: E402


def _space(name, area, level="L1", windows=0, doors=0):
    return {
        "id": name,
        "level_id": level,
        "name": name,
        "area_m2": area,
        "openings": (
            [{"category": "window"} for _ in range(windows)]
            + [{"category": "door"} for _ in range(doors)]
        ),
    }


def test_rooms_added_removed_and_unchanged():
    old = {"model": {"spaces": {"a": _space("Office", 20.0), "b": _space("Hall", 10.0)}}}
    new = {"model": {"spaces": {"a": _space("Office", 20.0), "c": _space("Cafe", 30.0)}}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["rooms"]["added"] == 1 and rep["rooms"]["added_ids"] == ["c"]
    assert rep["rooms"]["removed"] == 1 and rep["rooms"]["removed_ids"] == ["b"]
    assert rep["rooms"]["unchanged"] == 1


def test_room_rename_area_and_level_changes():
    old = {"spaces": {"a": _space("Office", 20.0, level="L1")}}
    new = {"spaces": {"a": _space("Office 2", 25.0, level="L2")}}
    rep = takeoff_diff.diff_takeoffs(old, new)
    r = rep["rooms"]
    assert r["renamed"] == 1 and r["area_changed"] == 1 and r["moved_level"] == 1
    assert r["unchanged"] == 0
    # tiny area drift is rounding noise, not a change
    old2 = {"spaces": {"a": _space("Office", 20.0)}}
    new2 = {"spaces": {"a": _space("Office", 20.05)}}
    assert takeoff_diff.diff_takeoffs(old2, new2)["rooms"]["area_changed"] == 0


def test_windows_and_doors_counted_per_space():
    old = {
        "spaces": {
            "a": _space("Office", 20.0, windows=2, doors=1),
            "b": _space("Hall", 10.0, doors=1),
        }
    }
    new = {
        "spaces": {
            "a": _space("Office", 20.0, windows=3, doors=1),
            "b": _space("Hall", 10.0, windows=1),
        }
    }
    rep = takeoff_diff.diff_takeoffs(old, new)
    assert rep["windows"] == {"added": 2, "removed": 0, "spaces_changed": 2}
    assert rep["doors"] == {"added": 0, "removed": 1, "spaces_changed": 1}


def test_symbols_from_set_reports():
    old_rep = {
        "hvac_legends": [{"rows": [{"cls": "diffuser"}, {"cls": "diffuser"}, {"cls": "sensor"}]}]
    }
    new_rep = {"hvac_legends": [{"rows": [{"cls": "diffuser"}, {"cls": "grille"}]}]}
    rep = takeoff_diff.diff_takeoffs({}, {}, old_report=old_rep, new_report=new_rep)
    assert rep["symbols"]["provided"] is True
    assert rep["symbols"]["added"] == 1 and rep["symbols"]["removed"] == 2
    assert rep["symbols"]["by_class"]["diffuser"] == {"added": 0, "removed": 1}
    assert rep["symbols"]["by_class"]["grille"] == {"added": 1, "removed": 0}


def test_symbols_absent_without_reports():
    rep = takeoff_diff.diff_takeoffs({}, {})
    assert rep["symbols"] == {"provided": False, "added": 0, "removed": 0, "by_class": {}}


def test_cli_takeoff_diff(tmp_path, capsys):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(
        '{"spaces": {"a": {"name": "Office", "area_m2": 20.0, '
        '"level_id": "L1", "openings": [{"category": "door"}]}}}'
    )
    new.write_text(
        '{"spaces": {"a": {"name": "Office", "area_m2": 20.0, '
        '"level_id": "L1", "openings": [{"category": "door"}, '
        '{"category": "door"}]}}}'
    )
    out = tmp_path / "diff.json"
    cli_main(["takeoff-diff", "--old", str(old), "--new", str(new), "--out", str(out)])
    assert out.exists()
    import json

    rep = json.loads(out.read_text())
    assert rep["schema"] == "matchline.takeoff_diff/1"
    assert rep["doors"]["added"] == 1
