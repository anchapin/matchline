"""Tests for in-product feedback capture (#1005)."""

from __future__ import annotations

import json

import pytest

from review.feedback_capture import (
    KIND_ABANDONED_REVIEW,
    KIND_FAILED_PARSE,
    KIND_OPEN_RESPONSE,
    KIND_OVERRIDE,
    FeedbackCaptureError,
    FeedbackLog,
    summarize,
)


@pytest.fixture()
def log(tmp_path):
    return FeedbackLog(tmp_path / "feedback" / "events.jsonl")


# -- contextual capture at the moment of friction ---------------------------


def test_override_records_allowlisted_context(log):
    event = log.log_override("wall", "rescale", confidence=0.93)
    assert event["schema"] == "matchline.feedback/1"
    assert event["kind"] == KIND_OVERRIDE
    assert event["context"] == {
        "class": "wall",
        "correction": "rescale",
        "confidence_bin": "80-100pct",
    }
    assert event["at"].endswith("Z")


def test_override_without_confidence_has_no_bin(log):
    assert log.log_override("door", "swap_door_window")["context"] == {
        "class": "door",
        "correction": "swap_door_window",
    }


def test_abandoned_review_records_item_kind(log):
    event = log.log_abandoned_review("unsure_detection", queue_position=3)
    assert event["kind"] == KIND_ABANDONED_REVIEW
    assert event["context"] == {"item": "unsure_detection", "queue_position": 3}


def test_failed_parse_records_stage_and_error_kind_only(log):
    event = log.log_failed_parse("takeoff", "unreadable_page")
    assert event["kind"] == KIND_FAILED_PARSE
    assert event["context"] == {"stage": "takeoff", "error": "unreadable_page"}


def test_failed_parse_refuses_raw_exception_text(log):
    with pytest.raises(FeedbackCaptureError):
        log.log_failed_parse("takeoff", "No such file or directory: /home/user/plans.pdf")


# -- building-specific context is refused at capture time -------------------


@pytest.mark.parametrize(
    "token",
    [
        "room_102",
        "space_3",
        "sheet_A101",
        "north_east_coords",
        "polygon_1",
        "project_name",
        "owner",
        "plans_path",
    ],
)
def test_building_specific_tokens_raise(log, token):
    with pytest.raises(FeedbackCaptureError):
        log.log_override(token, "rescale")
    with pytest.raises(FeedbackCaptureError):
        log.log_abandoned_review(token)
    with pytest.raises(FeedbackCaptureError):
        log.log_failed_parse("takeoff", token)
    with pytest.raises(FeedbackCaptureError):
        log.log_open_response("broken on startup", topic=token)


# -- open-ended channel ------------------------------------------------------


def test_open_response_stores_user_words_verbatim(log):
    event = log.log_open_response("The stair tool keeps resetting the run size.")
    assert event["kind"] == KIND_OPEN_RESPONSE
    assert event["context"]["text"] == "The stair tool keeps resetting the run size."
    assert "topic" not in event["context"]


def test_open_response_accepts_a_topic_token(log):
    event = log.log_open_response("crashes on reopen", topic="review_queue")
    assert event["context"]["topic"] == "review_queue"


def test_open_response_rejects_empty_and_oversized_text(log):
    with pytest.raises(FeedbackCaptureError):
        log.log_open_response("   ")
    with pytest.raises(FeedbackCaptureError):
        log.log_open_response("x" * 2001)


def test_unknown_kind_is_refused(tmp_path):
    log = FeedbackLog(tmp_path / "events.jsonl")
    with pytest.raises(FeedbackCaptureError):
        log._append("wrong_kind", {})


# -- the log is append-only, local, and reviewable ---------------------------


def test_log_round_trips_in_order(log):
    log.log_override("wall", "rescale")
    log.log_open_response("would love a batch export")
    log.log_failed_parse("takeoff", "unreadable_page")
    events = log.read()
    assert [e["kind"] for e in events] == [KIND_OVERRIDE, KIND_OPEN_RESPONSE, KIND_FAILED_PARSE]


def test_read_tolerates_a_malformed_line(log, tmp_path):
    log.log_override("wall", "rescale")
    with log.path.open("a") as fh:
        fh.write("not json at all\n")
    log.log_open_response("still works")
    assert len(log.read()) == 2


def test_read_of_a_missing_log_is_empty(tmp_path):
    assert FeedbackLog(tmp_path / "none.jsonl").read() == []


# -- themes summarized for roadmap re-prioritization ------------------------


def test_summarize_counts_kinds_and_friction(log):
    log.log_override("wall", "rescale")
    log.log_override("wall", "rescale")
    log.log_override("door", "swap_door_window")
    log.log_failed_parse("takeoff", "unreadable_page")
    summary = summarize(log.read())
    assert summary["total"] == 4
    assert summary["by_kind"] == {KIND_OVERRIDE: 3, KIND_FAILED_PARSE: 1}
    assert ("override:wall", 2) in summary["top_friction"]
    assert summary["top_friction"][0][1] == 2


def test_summarize_counts_theme_words_from_open_text(log):
    log.log_open_response("Please add a batch export for sheets.")
    log.log_open_response("Batch export would save me hours every week.")
    summary = summarize(log.read())
    assert summary["themes"][0] == ("batch", 2)
    assert ("export", 2) in summary["themes"]
    # stopwords and short words never become themes
    assert all(word not in {"please", "the", "me", "for"} for word, _ in summary["themes"])


def test_summarize_ignores_open_text_when_counting_friction(log):
    log.log_open_response("override smells like rescale failure")
    summary = summarize(log.read())
    assert summary["top_friction"] == []
    assert summary["themes"]  # the words still count as themes


def test_summarize_of_an_empty_log(log):
    summary = summarize(log.read())
    assert summary == {"total": 0, "by_kind": {}, "top_friction": [], "themes": []}


def test_events_are_jsonl_with_sorted_keys(log):
    log.log_override("wall", "rescale")
    line = log.path.read_text().strip().splitlines()[0]
    assert list(json.loads(line)) == sorted(json.loads(line))
