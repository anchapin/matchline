"""run --baseline: diff a finished run against a baseline out-dir (#1077)."""

import json
from pathlib import Path

import pytest

from cli import _baseline_diff, build_parser
from tests.model_factory import make_clean_model


def _write_run(d: Path, model) -> Path:
    d.mkdir(parents=True)
    stage02 = {"model": model.to_dict()["model"], "link_report": None}
    (d / "stage_02_model.json").write_text(json.dumps(stage02))
    return d


def test_baseline_diff_matches_manual_flow_and_attaches_items(tmp_path):
    """The diff equals the manual two-command flow, and the queue gains its items."""
    import takeoff_diff

    old_m, new_m = make_clean_model(), make_clean_model()
    del new_m.spaces["L1-102"]  # a removed room: the strongest review item
    old_d = _write_run(tmp_path / "old", old_m)
    new_d = _write_run(tmp_path / "new", new_m)

    res = _baseline_diff(new_d, old_d)

    old = json.loads((old_d / "stage_02_model.json").read_text())
    new = json.loads((new_d / "stage_02_model.json").read_text())
    assert res["report"] == takeoff_diff.diff_takeoffs(old, new)
    assert res["report"]["rooms"]["removed"] == 1
    items = json.loads((new_d / "takeoff_diff_review_items.json").read_text())
    assert items == takeoff_diff.review_items(res["report"]) and items
    assert res["attached"] == len(items)
    # the queue on the run's own stage file grew by exactly the attached items
    stage = json.loads((new_d / "stage_02_model.json").read_text())
    assert len(stage["model"]["review_queue"]) == len(items)
    # stable ids: re-diffing the same baseline appends nothing
    assert _baseline_diff(new_d, old_d)["attached"] == 0
    stage2 = json.loads((new_d / "stage_02_model.json").read_text())
    assert len(stage2["model"]["review_queue"]) == len(items)


def test_baseline_diff_reports_moved_pairs(tmp_path):
    """A window moved between spaces lands in moved_pairs with tag evidence."""
    old_m, new_m = make_clean_model(), make_clean_model()
    for m in (old_m, new_m):
        m.spaces["L1-101"].openings[0].tag = "W1"  # unique per revision
    moved = new_m.spaces["L1-101"].openings.pop(0)
    new_m.spaces["L1-102"].openings.append(moved)
    old_d = _write_run(tmp_path / "old", old_m)
    new_d = _write_run(tmp_path / "new", new_m)

    rep = _baseline_diff(new_d, old_d)["report"]

    assert rep["windows"]["moved"] == 1
    assert rep["windows"]["moved_pairs"] == [
        {"old_space": "L1-101", "new_space": "L1-102", "tag": "W1", "evidence": "tag"}
    ]


def test_baseline_diff_missing_stage_model(tmp_path):
    new_d = _write_run(tmp_path / "new", make_clean_model())
    with pytest.raises(FileNotFoundError):
        _baseline_diff(new_d, tmp_path / "old")


def test_run_parser_accepts_baseline():
    args = build_parser().parse_args(
        ["run", "--seed", "1", "--out-dir", "out", "--baseline", "prev"]
    )
    assert args.baseline == "prev"
