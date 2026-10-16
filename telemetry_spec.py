"""Versioned minimum-telemetry spec (#1003).

Defines, in one place, the minimum dataset a run may send back, the fields
that are allowed on an emitted payload (the allowlist), and the exclusion
list of building-specific data that must never leave the machine. The
emitter (#1006) accepts only allowlisted fields; anything else fails
validation here rather than shipping to users.

Plain-language data page: docs/telemetry-data.md. Versioned alongside
docs/data-contribution-agreement.md.
"""

from __future__ import annotations

import re
from typing import Any

SPEC_VERSION = "matchline.telemetry-spec/1"

#: Top-level fields allowed on an emitted payload. Anything else is outside
#: the allowlist and fails validation.
ALLOWED_FIELDS = frozenset(
    {
        "spec_version",
        "consent",
        "conf_distributions",
        "corrections_by_type",
        "check_failures",
        "binned_aggregates",
        "stage_outcomes",
    }
)

#: Fields every emitted payload must carry.
REQUIRED_FIELDS = frozenset(
    {
        "spec_version",
        "consent",
        "conf_distributions",
        "corrections_by_type",
        "check_failures",
        "binned_aggregates",
        "stage_outcomes",
    }
)

#: Key patterns that must never appear anywhere in a payload: no geometry,
#: no labels, no names, no coordinates, no sheet images, nothing that
#: identifies a building or a firm (#1003 exclusion list).
EXCLUDED_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"coord",
        r"polygon|vertex|vertices|geometry|geom_|bbox|extent",
        r"sheet|image|png|jpg|jpeg|pdf|raster",
        r"name|label|title",
        r"building|firm|project_|filename|file_|path|address|owner",
        r"room|space_|zone_|level_|id_|_id$",
    )
)

#: Payloads carry counts and short bin labels only; a longer string value is
#: a sign that detail (a path, a name, a serialised drawing) slipped in.
MAX_STRING_VALUE_LEN = 128


def emission_enabled(consent: bool, local_only: bool) -> bool:
    """Whether emission is allowed at all.

    Telemetry is opt-in: without consent nothing is emitted. Local-only mode
    disables emission entirely, consent or not (#1003).
    """
    return bool(consent) and not bool(local_only)


def _iter_keys(value: Any, path: str = ""):
    if isinstance(value, dict):
        for k, v in value.items():
            key_path = f"{path}.{k}" if path else str(k)
            if isinstance(k, str):
                yield key_path, k
            yield from _iter_keys(v, key_path)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _iter_keys(v, f"{path}[{i}]")


def _iter_string_values(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _iter_string_values(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _iter_string_values(v)
    elif isinstance(value, str):
        yield value


def validate_payload(payload: dict) -> list[str]:
    """Validate an emitted payload against the spec.

    Returns a list of violations (empty when the payload conforms). The
    emitter must treat any non-empty list as a hard failure: nothing ships.
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload is not an object"]

    unknown = sorted(set(payload) - ALLOWED_FIELDS)
    if unknown:
        errors.append(f"fields outside the allowlist: {unknown}")
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        errors.append(f"missing required fields: {missing}")

    if payload.get("spec_version") != SPEC_VERSION:
        errors.append(f"spec_version must be {SPEC_VERSION!r}, got {payload.get('spec_version')!r}")
    if "consent" in payload and payload["consent"] is not True:
        errors.append("emission requires opt-in consent (consent: true)")
    if "local_only" in payload:
        errors.append(
            "local-only mode disables emission entirely; local_only must never appear on a payload"
        )

    for key_path, key in _iter_keys(payload):
        for pat in EXCLUDED_KEY_PATTERNS:
            if pat.search(key):
                errors.append(
                    f"excluded field at {key_path}: '{key}' matches "
                    f"exclusion pattern '{pat.pattern}'"
                )
                break
    for v in _iter_string_values(payload):
        if len(v) > MAX_STRING_VALUE_LEN:
            errors.append(
                f"string value longer than {MAX_STRING_VALUE_LEN} characters: "
                "payloads carry counts and short bin labels only"
            )
            break
    return errors
