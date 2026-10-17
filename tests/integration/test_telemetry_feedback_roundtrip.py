"""Integration: telemetry + feedback round trip through run_review (#1126).

Drives a real review session (confirm, bulk confirm) against a model on
disk and asserts the whole seam end to end: the consent-gated telemetry
payload carries the expected event names and passes the spec validator,
the captured correction lands in the local feedback log in the pinned
event schema, and the export package's trust report picks both up. A
telemetry_spec or feedback schema change now fails loudly here instead of
drifting silently.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import export_package  # noqa: E402
from building_model import BuildingModel, ReviewItem  # noqa: E402
from run_review import main as review_main  # noqa: E402
from telemetry_spec import SPEC_VERSION, validate_payload  # noqa: E402
from tests.model_factory import P, make_clean_model  # noqa: E402
from validate import run_checks  # noqa: E402


def _write_model(out_dir: Path, kind: str = "door_height", n: int = 1) -> Path:
    """A model on disk with n open review items of an exclusion-clean kind."""
    model = make_clean_model()
    for i in range(1, n + 1):
        model.review_queue.append(
            ReviewItem(
                id=f"rq-{i:03d}",
                kind=kind,
                description="Door height on sheet A101 needs a check",
                status="open",
                confidence=0.5,
                provenance=P(),
                needs_review=True,
                acknowledged=False,
            )
        )
    model_path = out_dir / "stage_02_model.json"
    model_path.write_text(model.to_json())
    return model_path


def _args(model_path: Path, **over) -> argparse.Namespace:
    base = dict(
        model=str(model_path),
        confirm=None,
        reject=None,
        show_all=False,
        auto_triage=False,
        list=False,
        format="text",
        apply=None,
        out=None,
        report=None,
        undo=None,
        revert_to_auto=None,
        corrections=None,
        bulk_confirm=False,
        bulk_reject=False,
        kind=None,
        conf_above=None,
        conf_below=None,
        level=None,
        telemetry=True,
    )
    base.update(over)
    return argparse.Namespace(**base)


def _events(feedback_path: Path) -> list[dict]:
    return [json.loads(line) for line in feedback_path.read_text().splitlines() if line.strip()]


def test_review_decision_roundtrip(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    model_path = _write_model(out_dir)

    review_main(_args(model_path, confirm="rq-001"))

    # (a) telemetry events fired with the expected event names, spec-clean
    payload = json.loads((out_dir / "telemetry.json").read_text())
    assert payload["spec_version"] == SPEC_VERSION
    assert payload["consent"] is True
    assert payload["corrections_by_type"] == {"accept": 1}
    assert validate_payload(payload) == []

    # (b) the captured correction landed in the feedback log, schema pinned
    events = _events(out_dir / "feedback.jsonl")
    assert len(events) == 1
    assert events[0]["schema"] == "matchline.feedback/1"
    assert events[0]["kind"] == "override"
    assert events[0]["context"] == {"class": "door_height", "correction": "accept"}

    # and it appears in the export package's trust report
    model = BuildingModel.from_json(model_path.read_text())
    pkg = export_package.build_package(out_dir, model, run_checks(model), {}, [])
    tr = json.loads((pkg / "trust_report.json").read_text())
    assert tr["feedback"]["present"] is True
    assert tr["feedback"]["file"] == "feedback.jsonl"
    assert tr["feedback"]["summary"]["by_kind"] == {"override": 1}
    assert tr["corrections"]["recorded"] is True


def test_no_consent_skips_the_payload_but_still_captures_feedback(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    model_path = _write_model(out_dir)

    review_main(_args(model_path, confirm="rq-001", telemetry=False))

    assert not (out_dir / "telemetry.json").exists()
    assert len(_events(out_dir / "feedback.jsonl")) == 1


def test_excluded_kind_falls_back_to_review_item(tmp_path):
    # "window_room_link" contains "room", an excluded substring, so the
    # feedback log falls back to the generic "review_item" token while the
    # telemetry count still fires under the action vocabulary
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    model_path = _write_model(out_dir, kind="window_room_link")

    review_main(_args(model_path, confirm="rq-001"))

    events = _events(out_dir / "feedback.jsonl")
    assert events[0]["context"] == {"class": "review_item", "correction": "accept"}
    payload = json.loads((out_dir / "telemetry.json").read_text())
    assert payload["corrections_by_type"] == {"accept": 1}


def test_bulk_confirm_records_each_decision(tmp_path):
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    model_path = _write_model(out_dir, n=2)

    review_main(_args(model_path, bulk_confirm=True))

    payload = json.loads((out_dir / "telemetry.json").read_text())
    assert payload["corrections_by_type"] == {"accept": 2}
    assert validate_payload(payload) == []
    assert len(_events(out_dir / "feedback.jsonl")) == 2
