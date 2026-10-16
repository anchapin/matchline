"""Allowlist emitter (#1006) on the minimum-telemetry spec (#1003).

The emitter is the only way run data becomes a telemetry payload. It
accepts nothing but the spec's five metric groups, records them under
bucket keys that are checked against the spec's exclusion list at record
time, and validates the finished payload through
`telemetry_spec.validate_payload` before returning it. Anything outside
the allowlist, or anything matching the exclusion list, raises here: the
failure happens at build time, never in front of users, and nothing
non-conforming ships.

Local-only mode disables emission entirely: the emitter collects nothing
and `build_payload` returns None, consent or not (#1003).

Plain-language data page: docs/telemetry-data.md.
"""

from __future__ import annotations

import math
from typing import Any

from telemetry_spec import (
    EXCLUDED_KEY_PATTERNS,
    MAX_STRING_VALUE_LEN,
    SPEC_VERSION,
    emission_enabled,
    validate_payload,
)

#: Default histogram bins for per-class confidence distributions.
CONF_BINS: tuple[str, ...] = ("0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0")

#: Stage outcomes the emitter accepts. Anything else is a typo, not data.
STAGE_OUTCOMES: frozenset[str] = frozenset({"ok", "error", "skipped"})


class EmitterBuildError(ValueError):
    """A payload the emitter refuses to build.

    Raised when a record call names a bucket key the exclusion list
    blocks, or when the finished payload fails the spec. Failing at build
    time is the point: bad data never reaches the emission step, let
    alone users.
    """


def _check_bucket_key(key: Any, where: str) -> str:
    if not isinstance(key, str) or not key:
        raise EmitterBuildError(f"{where}: bucket key must be a non-empty string, got {key!r}")
    if len(key) > MAX_STRING_VALUE_LEN:
        raise EmitterBuildError(
            f"{where}: bucket key longer than {MAX_STRING_VALUE_LEN} characters"
        )
    for pat in EXCLUDED_KEY_PATTERNS:
        if pat.search(key):
            raise EmitterBuildError(
                f"{where}: key {key!r} matches exclusion pattern {pat.pattern!r}; "
                "building-specific data fails at build time, never ships"
            )
    return key


def _check_count(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EmitterBuildError(f"{where}: count must be a non-negative integer, got {value!r}")
    return int(value)


class TelemetryEmitter:
    """Builds one spec-conforming payload from allowlisted metrics.

    Only the spec's five metric groups exist here. There is no API for
    adding another top-level field: a payload built by this emitter is
    complete and allowlisted by construction, and is validated through
    the spec before it is returned.
    """

    def __init__(self, consent: bool = False, local_only: bool = False) -> None:
        self._consent = bool(consent)
        self.enabled = emission_enabled(self._consent, local_only)
        self._conf: dict[str, dict[str, int]] = {}
        self._corrections: dict[str, int] = {}
        self._checks: dict[str, int] = {}
        self._binned: dict[str, dict[str, int]] = {}
        self._stages: dict[str, dict[str, Any]] = {}

    # -- record methods (one per metric group) --------------------------------

    def record_confidence(self, detection_class: str, value: float) -> None:
        """Record one detection confidence into its per-class histogram bin."""
        _check_bucket_key(detection_class, "conf_distributions")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
        ):
            raise EmitterBuildError(
                f"conf_distributions: confidence must be a finite number, got {value!r}"
            )
        if not 0.0 <= float(value) <= 1.0:
            raise EmitterBuildError(
                f"conf_distributions: confidence must be in [0, 1], got {value!r}"
            )
        idx = min(int(float(value) * 5), len(CONF_BINS) - 1)
        bucket = self._conf.setdefault(detection_class, dict.fromkeys(CONF_BINS, 0))
        bucket[CONF_BINS[idx]] += 1

    def record_correction(self, correction_type: str) -> None:
        """Count one correction of the given type."""
        _check_bucket_key(correction_type, "corrections_by_type")
        self._corrections[correction_type] = self._corrections.get(correction_type, 0) + 1

    def record_check_failure(self, check: str) -> None:
        """Count one failure of a named validation check."""
        _check_bucket_key(check, "check_failures")
        self._checks[check] = self._checks.get(check, 0) + 1

    def record_binned(self, metric: str, bucket: str, count: int = 1) -> None:
        """Record a pre-binned aggregate (counts and short bin labels only)."""
        _check_bucket_key(metric, "binned_aggregates")
        _check_bucket_key(bucket, f"binned_aggregates[{metric}]")
        counts = self._binned.setdefault(metric, {})
        counts[bucket] = counts.get(bucket, 0) + _check_count(count, "binned_aggregates")

    def record_stage(self, stage: str, outcome: str, seconds: float | None = None) -> None:
        """Record one pipeline stage outcome and its duration."""
        _check_bucket_key(stage, "stage_outcomes")
        if outcome not in STAGE_OUTCOMES:
            raise EmitterBuildError(
                f"stage_outcomes: outcome must be one of {sorted(STAGE_OUTCOMES)}, got {outcome!r}"
            )
        entry: dict[str, Any] = self._stages.setdefault(stage, {"outcome": outcome, "attempts": 0})
        entry["attempts"] += 1
        if outcome == "error":
            entry["outcome"] = "error"
        if seconds is not None:
            if (
                not isinstance(seconds, (int, float))
                or isinstance(seconds, bool)
                or not math.isfinite(seconds)
                or seconds < 0
            ):
                raise EmitterBuildError(
                    f"stage_outcomes: seconds must be a finite non-negative number, got {seconds!r}"
                )
            entry["seconds"] = round(float(entry.get("seconds", 0.0)) + float(seconds), 1)

    # -- build ----------------------------------------------------------------

    def build_payload(self) -> dict | None:
        """Build the payload, or None when emission is disabled.

        The finished payload is run through the spec's validator; any
        violation raises here. Nothing non-conforming can leave this
        method.
        """
        if not self.enabled:
            return None
        payload = {
            "spec_version": SPEC_VERSION,
            "consent": True,
            "conf_distributions": {k: dict(v) for k, v in self._conf.items()},
            "corrections_by_type": dict(self._corrections),
            "check_failures": dict(self._checks),
            "binned_aggregates": {k: dict(v) for k, v in self._binned.items()},
            "stage_outcomes": {k: dict(v) for k, v in self._stages.items()},
        }
        errors = validate_payload(payload)
        if errors:
            raise EmitterBuildError("payload failed spec validation: " + "; ".join(errors))
        return payload
