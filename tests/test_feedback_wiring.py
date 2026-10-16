"""Feedback capture wiring (#1038): friction moments land in the local log.

Review confirm/reject decisions record override events beside the
corrections history, a failed pipeline stage records a failed-parse
event in <out-dir>/feedback.jsonl, and `matchline feedback` is the CLI
entry point for open-ended feedback and log summaries. Nothing here is
telemetry: the log stays local.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cli
import run_pipeline
import run_review
from building_model import BuildingModel, Provenance


def _review_item(model: BuildingModel, kind: str):
    prov = Provenance(sheet_id="test", revision=1, method="test", confidence=0.5)
    item = model.flag_for_review(
        kind=kind, description="fixture item", confidence=0.5, provenance=prov
    )
    assert item is not None
    return item


def test_review_decision_logs_override(tmp_path: Path):
    model = BuildingModel()
    item = _review_item(model, "fixture_assignment")
    run_review._decide(model, item.id, "confirm", corrections_path=tmp_path / "model.json")
    events = [json.loads(line) for line in (tmp_path / "feedback.jsonl").read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["kind"] == "override"
    assert events[0]["context"]["class"] == "fixture_assignment"
    assert events[0]["context"]["correction"] == "accept"


def test_excluded_kind_falls_back_to_review_item(tmp_path: Path):
    model = BuildingModel()
    item = _review_item(model, "window_room_link")  # "room" is on the exclusion list
    run_review._decide(model, item.id, "confirm", corrections_path=tmp_path / "model.json")
    events = [json.loads(line) for line in (tmp_path / "feedback.jsonl").read_text().splitlines()]
    assert len(events) == 1
    assert events[0]["context"]["class"] == "review_item"


def test_stage_failure_captures_failed_parse(tmp_path: Path):
    argv = ["run", "--ifc", str(tmp_path / "nope.ifc"), "--out-dir", str(tmp_path / "out")]
    args = cli.build_parser().parse_args(argv)
    with pytest.raises(SystemExit):
        run_pipeline.main(args)
    events = [
        json.loads(line) for line in (tmp_path / "out" / "feedback.jsonl").read_text().splitlines()
    ]
    assert len(events) == 1
    assert events[0]["kind"] == "failed_parse"
    assert events[0]["context"]["stage"] == "stage_1"
    assert events[0]["context"]["error"] == "FileNotFoundError"


def _feedback(argv: list[str], tmp_path: Path):
    log = tmp_path / "fb.jsonl"
    args = cli.build_parser().parse_args(["feedback", "--log", str(log), *argv])
    args.func(args)
    return log


def test_open_response_logged(tmp_path: Path):
    log = _feedback(["--text", "the legend editor loses my tags", "--topic", "review"], tmp_path)
    event = json.loads(log.read_text().splitlines()[0])
    assert event["schema"] == "matchline.feedback/1"
    assert event["kind"] == "open_response"
    assert event["context"]["topic"] == "review"


def test_refused_topic_exits_1(tmp_path: Path, capsys):
    with pytest.raises(SystemExit) as e:
        _feedback(["--text", "something", "--topic", "labels"], tmp_path)
    assert e.value.code == 1
    assert "exclusion pattern" in capsys.readouterr().err


def test_summary_counts(tmp_path: Path, capsys):
    _feedback(["--text", "first note", "--topic", "review"], tmp_path)
    _feedback(["--text", "second note", "--topic", "export"], tmp_path)
    _feedback(["--summary"], tmp_path)
    out = capsys.readouterr().out
    assert '"total": 2' in out
    assert "open_response" in out
