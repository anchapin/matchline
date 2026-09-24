"""Type-checking tests for ReviewItem.kind Literal type.

This file is validated by mypy. If an invalid kind value is used,
mypy will report an error. Run: mypy tests/test_review_item_kind_types.py

Invalid kind values (should cause mypy errors):
- "invalid_kind" (not in the Literal type)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from building_model import Provenance, ReviewItem

if TYPE_CHECKING:

    def reveal_type(x: object) -> None: ...

    """mypy should flag this as an error because 'invalid_kind' is not a valid kind."""
    bad_kind: Literal[
        "fixture_assignment",
        "fixture_schedule",
        "diffuser_assignment",
        "sensor_assignment",
        "window_room_link",
        "space_no_geometry",
        "elevation_conflict",
        "window_reconciliation",
        "gd_complex_row",
    ] = "invalid_kind"

    item = ReviewItem(
        id="test",
        kind=bad_kind,
        description="test",
        confidence=0.5,
        provenance=Provenance(sheet_id="test", revision=0, method="test", confidence=0.5),
    )
    reveal_type(item.kind)
