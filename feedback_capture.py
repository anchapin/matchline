"""In-product feedback capture (#1005).

Captures structured feedback at the moment of friction, locally, for
later review and roadmap re-prioritization:

- a detector override (the user corrected a detection),
- an abandoned review item (the user left a review-queue item untouched),
- a failed parse (a stage could not read its input),
- an open-ended response (bugs, feature requests, places where the
  conversation does not work well).

Context fields follow the telemetry allowlist (#1003): tokens are
checked against the spec's exclusion list at capture time, so room
names, paths, sheet references and the like are refused here rather than
landing in the log. Free text from the open-ended channel is stored
verbatim (it is the user's own words) but never flows into a telemetry
payload: the emitter builds payloads only from its five metric groups,
and feedback is not one of them. Nothing here sends anything anywhere;
the log is a local JSONL file someone on the team opens and reads.

Plain-language data page: docs/telemetry-data.md.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry_spec import EXCLUDED_KEY_PATTERNS

SCHEMA = "matchline.feedback/1"

#: Friction moments and the open-ended channel, one `kind` each.
KIND_OVERRIDE = "override"
KIND_ABANDONED_REVIEW = "abandoned_review"
KIND_FAILED_PARSE = "failed_parse"
KIND_OPEN_RESPONSE = "open_response"
KINDS: frozenset[str] = frozenset(
    {KIND_OVERRIDE, KIND_ABANDONED_REVIEW, KIND_FAILED_PARSE, KIND_OPEN_RESPONSE}
)

#: Open-ended responses carry the user's own words, so they get more room
#: than telemetry strings; everything else stays short labels and counts.
MAX_OPEN_TEXT_LEN = 2000
MAX_TOKEN_LEN = 128

#: Words dropped before theme counting; everything else is counted as-is.
STOPWORDS: frozenset[str] = frozenset(
    """a an and any are as at be but by can could did do for from get got
    had has have how i if in into is it its just like me more my no not of
    on one or our out over should so some than that the their them then
    there these they this to too up was we were what when where which who
    why will with would you your very really about after all also because
    been before being between during each few he her here him his she still
    such take takes taking them thing things think though through under
    until use used using want wants wanted well went yes hi hello hey
    please thanks sorry gonna gotta""".split()
)

_WORD_RE = re.compile(r"[a-z][a-z0-9_-]{1,29}")


class FeedbackCaptureError(ValueError):
    """A feedback event the capture refuses to record.

    Raised when a context token matches the telemetry exclusion list
    (building-specific data) or an event is malformed. Failing at
    capture time keeps the log free of data nobody should review.
    """


def _check_token(token: Any, where: str) -> str:
    if not isinstance(token, str) or not token.strip():
        raise FeedbackCaptureError(f"{where}: token must be a non-empty string, got {token!r}")
    token = token.strip()
    if len(token) > MAX_TOKEN_LEN:
        raise FeedbackCaptureError(f"{where}: token longer than {MAX_TOKEN_LEN} characters")
    for pat in EXCLUDED_KEY_PATTERNS:
        if pat.search(token):
            raise FeedbackCaptureError(
                f"{where}: token {token!r} matches exclusion pattern {pat.pattern!r}; "
                "building-specific context is refused at capture time"
            )
    return token


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FeedbackLog:
    """Append-only local feedback log (JSONL), reviewable by hand."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- capture ---------------------------------------------------------------

    def _append(self, kind: str, context: dict[str, Any]) -> dict[str, Any]:
        if kind not in KINDS:
            raise FeedbackCaptureError(
                f"unknown feedback kind {kind!r}; expected one of {sorted(KINDS)}"
            )
        event = {"schema": SCHEMA, "kind": kind, "at": _now(), "context": context}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def log_override(
        self,
        detection_class: str,
        correction_type: str,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """The user overrode a detection of `detection_class`.

        Context is allowlist-shaped only: the generic class and the
        correction type, plus the confidence as a coarse bin. Never the
        room, sheet, or geometry involved.
        """
        ctx: dict[str, Any] = {
            "class": _check_token(detection_class, KIND_OVERRIDE),
            "correction": _check_token(correction_type, KIND_OVERRIDE),
        }
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
                raise FeedbackCaptureError(
                    f"{KIND_OVERRIDE}: confidence must be a number in [0, 1], got {confidence!r}"
                )
            lo = min(int(float(confidence) * 5), 4) * 20
            ctx["confidence_bin"] = f"{lo}-{lo + 20}pct"
        return self._append(KIND_OVERRIDE, ctx)

    def log_abandoned_review(
        self, item_kind: str, queue_position: int | None = None
    ) -> dict[str, Any]:
        """The user left a review-queue item of `item_kind` untouched."""
        ctx: dict[str, Any] = {"item": _check_token(item_kind, KIND_ABANDONED_REVIEW)}
        if queue_position is not None:
            if (
                not isinstance(queue_position, int)
                or isinstance(queue_position, bool)
                or queue_position < 0
            ):
                raise FeedbackCaptureError(
                    f"{KIND_ABANDONED_REVIEW}: queue_position must be a"
                    f" non-negative integer, got {queue_position!r}"
                )
            ctx["queue_position"] = queue_position
        return self._append(KIND_ABANDONED_REVIEW, ctx)

    def log_failed_parse(self, stage: str, error_kind: str) -> dict[str, Any]:
        """A stage failed to parse its input.

        Context is two short tokens: the stage and a coarse error kind.
        Raw exception messages are deliberately not recorded; they carry
        paths and file contents the exclusion list exists to keep out.
        """
        return self._append(
            KIND_FAILED_PARSE,
            {
                "stage": _check_token(stage, KIND_FAILED_PARSE),
                "error": _check_token(error_kind, KIND_FAILED_PARSE),
            },
        )

    def log_open_response(self, text: str, topic: str | None = None) -> dict[str, Any]:
        """The open-ended channel: bugs, feature requests, process friction.

        The user's own words, stored verbatim (trimmed, length-capped)
        under the local log only. Never emitted through the telemetry
        emitter.
        """
        if not isinstance(text, str) or not text.strip():
            raise FeedbackCaptureError(f"{KIND_OPEN_RESPONSE}: text must be a non-empty string")
        text = text.strip()
        if len(text) > MAX_OPEN_TEXT_LEN:
            raise FeedbackCaptureError(
                f"{KIND_OPEN_RESPONSE}: text longer than {MAX_OPEN_TEXT_LEN} characters"
            )
        ctx: dict[str, Any] = {"text": text}
        if topic is not None:
            ctx["topic"] = _check_token(topic, KIND_OPEN_RESPONSE)
        return self._append(KIND_OPEN_RESPONSE, ctx)

    # -- read ------------------------------------------------------------------

    def read(self) -> list[dict[str, Any]]:
        """All recorded events in order; malformed lines are skipped."""
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a feedback log for roadmap re-prioritization.

    Returns counts by kind, the most common friction points as
    (kind, token) pairs, and the top theme words from open-ended
    responses. Counts and generic labels only; never event text.
    """
    by_kind: dict[str, int] = {}
    friction: dict[str, int] = {}
    themes: dict[str, int] = {}
    for event in events:
        kind = event.get("kind", "")
        if kind not in KINDS:
            continue
        by_kind[kind] = by_kind.get(kind, 0) + 1
        ctx = event.get("context", {})
        tokens: list[str] = []
        if kind == KIND_OVERRIDE:
            tokens = [t for t in (ctx.get("class"), ctx.get("correction")) if t]
        elif kind == KIND_ABANDONED_REVIEW:
            tokens = [t] if (t := ctx.get("item")) else []
        elif kind == KIND_FAILED_PARSE:
            tokens = [t for t in (ctx.get("stage"), ctx.get("error")) if t]
        elif kind == KIND_OPEN_RESPONSE:
            for word in _WORD_RE.findall(ctx.get("text", "").lower()):
                if word not in STOPWORDS and len(word) > 2:
                    themes[word] = themes.get(word, 0) + 1
        for token in tokens:
            key = f"{kind}:{token}"
            friction[key] = friction.get(key, 0) + 1

    def _top(counter: dict[str, int], n: int) -> list[tuple[str, int]]:
        return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    return {
        "total": sum(by_kind.values()),
        "by_kind": dict(sorted(by_kind.items())),
        "top_friction": _top(friction, 5),
        "themes": _top(themes, 10),
    }
