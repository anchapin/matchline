from __future__ import annotations

FT2_PER_M2 = 10.7639
OPENING_DEDUP_TOL_M = 0.15  # center-distance tolerance for same-tag dedup

"""Interval overlap helpers."""


def _intervals_overlap(a: list | None, b: list | None, tol: float) -> bool:
    """Return True if intervals [a0,a1] and [b0,b1] overlap by at least tol."""
    if a is None or b is None:
        return False
    a0, a1 = min(a), max(a)
    b0, b1 = min(b), max(b)
    return not (a1 + tol < b0 or b1 + tol < a0)
