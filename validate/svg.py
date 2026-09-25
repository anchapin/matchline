"""SVG validation checks — placeholder for future SVG-specific validation."""

from __future__ import annotations

from validate.types import CheckResult


def _check_svg_structure(ctx) -> CheckResult:
    """Placeholder: SVG-specific checks not yet implemented."""
    return CheckResult(
        "svg_structure",
        "SVG structure",
        "skip",
        "SVG validation not yet implemented",
    )
