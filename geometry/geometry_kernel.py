"""geometry_kernel — one shared kernel for the polygon primitives (#1338).

Single home for the three helpers that were duplicated across the BEM
export/import pipeline:

  * ``_shoelace``  — signed polygon area (shoelace); positive = CCW.
  * ``_ensure_ccw`` — ring normalisation rule (reverse when signed area < 0).
  * ``_fmt``        — the shared ``f"{v:.4f}"`` number formatter.

The module deliberately imports nothing (stdlib only, no project imports) so
that every caller — ``bem/``, ``ifc/``, ``run_pipeline.py``, ``validate/`` —
can depend on it without circular-import risk.

Behaviour is unchanged with respect to the previous per-module copies: the
shoelace sign convention, the ``_ensure_ccw`` reversal rule and the 4-decimal
formatting are byte-identical.
"""

from __future__ import annotations

__all__ = ["_ensure_ccw", "_fmt", "_shoelace"]


def _shoelace(poly) -> float:
    """Signed area of a polygon ring (shoelace formula).

    Positive for counter-clockwise rings, negative for clockwise ones,
    zero for degenerate rings (fewer than 3 vertices).
    """
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _ensure_ccw(ring):
    """Return ``ring`` as a list, reversed when its signed area is negative."""
    return list(reversed(ring)) if _shoelace(ring) < 0 else list(ring)


def _fmt(v: float) -> str:
    """Format a number with the shared 4-decimal precision."""
    return f"{v:.4f}"
