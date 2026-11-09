"""Batch review triage by filter (#1294).

``run_review.py`` could only decide one review item at a time
(``--confirm ID`` / ``--reject ID``). Items that pile up per kind and per
sheet (``air_loop``, ``mech_schedule_value``, ...) need a batch surface:
decide every OPEN item matching a filter, through the same decision path
the single-item flags use, and record the decisions in the report's
decisions-file format (``matchline.review_decisions/1``) so the batch
round-trips through ``matchline review --apply``.

These tests drive ``run_review.py`` directly (the flags live on that CLI).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from building_model import BuildingModel, Provenance, ReviewItem

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(args: list[str]) -> subprocess.CompletedProcess:
    """Run ``run_review.py`` with the given arguments."""
    return subprocess.run(
        [sys.executable, "run_review.py"] + args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _model_with_items() -> BuildingModel:
    """A model with two ``air_loop`` items on two sheets and one other kind."""
    model = BuildingModel()
    a101 = Provenance(sheet_id="A101", revision=1, method="test", confidence=0.75)
    m201 = Provenance(sheet_id="M201", revision=1, method="test", confidence=0.75)
    model.review_queue.append(
        ReviewItem(
            id="R1",
            kind="air_loop",
            description="Air loop on sheet A101",
            status="open",
            confidence=0.4,
            provenance=a101,
        )
    )
    model.review_queue.append(
        ReviewItem(
            id="R2",
            kind="air_loop",
            description="Air loop on sheet M201",
            status="open",
            confidence=0.5,
            provenance=m201,
        )
    )
    model.review_queue.append(
        ReviewItem(
            id="R3",
            kind="mech_schedule_value",
            description="Schedule value on sheet A101",
            status="open",
            confidence=0.6,
            provenance=a101,
        )
    )
    return model


@pytest.fixture()
def model_path(tmp_path: Path) -> Path:
    p = tmp_path / "model.json"
    p.write_text(_model_with_items().to_json(), encoding="utf-8")
    return p


def _statuses(path: Path) -> dict[str, str]:
    model = BuildingModel.from_json(path.read_text(encoding="utf-8"))
    return {i.id: i.status for i in model.review_queue}


class TestBatchConfirm:
    """--confirm-kind decides every OPEN item of that kind."""

    def test_batch_confirm_applies_to_every_matching_open_item(self, model_path: Path):
        r = _run_cli([str(model_path), "--confirm-kind", "air_loop"])
        assert r.returncode == 0, r.stderr

        assert _statuses(model_path) == {
            "R1": "confirmed",
            "R2": "confirmed",
            "R3": "open",
        }

    def test_batch_confirm_writes_decisions_file(self, model_path: Path):
        r = _run_cli([str(model_path), "--confirm-kind", "air_loop"])
        assert r.returncode == 0, r.stderr

        decisions_path = model_path.parent / "review_decisions.json"
        assert decisions_path.exists()
        doc = json.loads(decisions_path.read_text(encoding="utf-8"))

        assert doc["schema"] == "matchline.review_decisions/1"
        assert [(d["id"], d["action"]) for d in doc["decisions"]] == [
            ("R1", "confirm"),
            ("R2", "confirm"),
        ]
        # the automatic state is recorded as it was before the batch
        assert doc["auto"]["R1"]["status"] == "open"
        assert doc["auto"]["R3"]["status"] == "open"

    def test_batch_confirm_reruns_validation(self, model_path: Path):
        r = _run_cli([str(model_path), "--confirm-kind", "air_loop"])
        assert r.returncode == 0, r.stderr
        assert "Validation" in r.stdout

    def test_batch_confirm_respects_the_sheet_filter(self, model_path: Path):
        r = _run_cli(
            [
                str(model_path),
                "--confirm-kind",
                "air_loop",
                "--confirm-sheet",
                "A101",
            ]
        )
        assert r.returncode == 0, r.stderr

        assert _statuses(model_path) == {"R1": "confirmed", "R2": "open", "R3": "open"}
        doc = json.loads((model_path.parent / "review_decisions.json").read_text())
        assert [d["id"] for d in doc["decisions"]] == ["R1"]


class TestBatchReject:
    """--reject-kind decides every OPEN item of that kind."""

    def test_batch_reject_applies_to_every_matching_open_item(self, model_path: Path):
        r = _run_cli([str(model_path), "--reject-kind", "mech_schedule_value"])
        assert r.returncode == 0, r.stderr

        assert _statuses(model_path) == {
            "R1": "open",
            "R2": "open",
            "R3": "rejected",
        }

    def test_batch_reject_writes_decisions_file(self, model_path: Path):
        r = _run_cli([str(model_path), "--reject-kind", "mech_schedule_value"])
        assert r.returncode == 0, r.stderr

        doc = json.loads((model_path.parent / "review_decisions.json").read_text())
        assert doc["schema"] == "matchline.review_decisions/1"
        assert [(d["id"], d["action"]) for d in doc["decisions"]] == [("R3", "reject")]


class TestBatchNoMatch:
    """A batch that matches nothing changes nothing and writes nothing."""

    def test_no_match_exits_cleanly_and_writes_nothing(self, model_path: Path):
        before = model_path.read_bytes()
        r = _run_cli([str(model_path), "--confirm-kind", "nosuchkind"])
        assert r.returncode == 0, r.stderr
        assert "no open review items match" in r.stdout.lower()

        assert model_path.read_bytes() == before
        assert not (model_path.parent / "review_decisions.json").exists()

    def test_no_match_leaves_existing_decisions_file_alone(self, model_path: Path):
        _run_cli([str(model_path), "--confirm-kind", "air_loop"])
        decisions_path = model_path.parent / "review_decisions.json"
        before = decisions_path.read_bytes()

        r = _run_cli([str(model_path), "--reject-kind", "nosuchkind"])
        assert r.returncode == 0, r.stderr
        assert decisions_path.read_bytes() == before


class TestBatchDecisionsRoundTrip:
    """The decisions file the batch writes replays through --apply."""

    def test_decisions_file_replays_to_the_same_state(self, tmp_path: Path):
        source = tmp_path / "model.json"
        source.write_text(_model_with_items().to_json(), encoding="utf-8")

        r = _run_cli([str(source), "--confirm-kind", "air_loop"])
        assert r.returncode == 0, r.stderr
        decisions = tmp_path / "review_decisions.json"

        # replay the file on a fresh copy of the queue
        replay = tmp_path / "replay.json"
        replay.write_text(_model_with_items().to_json(), encoding="utf-8")
        r2 = _run_cli([str(replay), "--apply", str(decisions)])
        assert r2.returncode == 0, r2.stderr

        assert _statuses(replay) == {"R1": "confirmed", "R2": "confirmed", "R3": "open"}

    def test_decisions_file_matches_the_golden_format_byte_for_byte(self, tmp_path: Path):
        """An empty decisions document is byte-identical to the golden file."""
        from review.review_report import sha256_text, template, write_decisions
        from tests.make_goldens import _pinned_review_model

        model = _pinned_review_model()
        doc = template(model, sha256_text(model.to_json()))
        out = write_decisions(tmp_path / "decisions.json", doc)

        golden = (REPO_ROOT / "tests" / "goldens" / "review_decisions.json").read_bytes()
        assert out.read_bytes() == golden

    def test_decisions_out_flag_honours_the_path(self, model_path: Path, tmp_path: Path):
        out = tmp_path / "elsewhere" / "decisions.json"
        r = _run_cli(
            [
                str(model_path),
                "--confirm-kind",
                "air_loop",
                "--decisions-out",
                str(out),
            ]
        )
        assert r.returncode == 0, r.stderr
        assert out.exists()
        assert not (model_path.parent / "review_decisions.json").exists()


class TestBatchFlagErrors:
    """The batch flags refuse to be combined with each other."""

    def test_confirm_and_reject_kind_are_exclusive(self, model_path: Path):
        r = _run_cli(
            [
                str(model_path),
                "--confirm-kind",
                "air_loop",
                "--reject-kind",
                "air_loop",
            ]
        )
        assert r.returncode == 1
        assert "only one" in r.stderr.lower()

    def test_batch_takes_no_single_item_flags(self, model_path: Path):
        r = _run_cli([str(model_path), "--confirm-kind", "air_loop", "--confirm", "R1"])
        assert r.returncode == 1
        assert "error" in r.stderr.lower()

    def test_batch_needs_a_filter(self, model_path: Path):
        r = _run_cli([str(model_path), "--confirm-kind", ""])
        assert r.returncode == 1
        assert "error" in r.stderr.lower()


class TestBatchHelp:
    """The new flags are documented in --help."""

    def test_help_documents_the_batch_flags(self):
        r = _run_cli(["--help"])
        assert r.returncode == 0, r.stderr
        for flag in ("--confirm-kind", "--reject-kind", "--confirm-sheet", "--reject-sheet"):
            assert flag in r.stdout


def test_no_stray_tempfiles(tmp_path: Path):
    """The batch writes one decisions file beside the model.

    The correction history (``corrections.json``) and the feedback log
    (``feedback.jsonl``) are written too, exactly as the single-item
    confirm/reject path writes them.
    """
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "model.json"
        p.write_text(_model_with_items().to_json(), encoding="utf-8")
        _run_cli([str(p), "--confirm-kind", "air_loop"])
        names = sorted(x.name for x in Path(td).iterdir())
        assert names == [
            "corrections.json",
            "feedback.jsonl",
            "model.json",
            "review_decisions.json",
        ]
