"""Integration tests: review_queue unacknowledged items block export (fixes #237).

Confirms the AGENTS.md invariant: when any review_queue item has
needs_review=True and acknowledged=False, export_gate must return False.
Only when the item is acknowledged (or needs_review=False) may export proceed.
"""

from __future__ import annotations

from building_model import ReviewItem
from tests.model_factory import P, make_clean_model
from validate import export_gate, run_checks


class TestReviewQueueBlocksExport:
    """Verify that unacknowledged review_queue items block BEM export."""

    def test_unacknowledged_review_items_block_export(self):
        """export_gate returns False when review_queue has unacknowledged needs_review items."""
        model = make_clean_model()
        model.review_queue.append(
            ReviewItem(
                id="rq-001",
                kind="window_room_link",
                description="Low-confidence window area extraction on sheet A101",
                status="open",
                confidence=0.5,
                provenance=P(),
                needs_review=True,
                acknowledged=False,
            )
        )
        report = run_checks(model)
        assert not export_gate(report), (
            "export_gate should return False when unacknowledged "
            "needs_review items are present in review_queue"
        )

    def test_acknowledged_review_items_do_not_block_export(self):
        """export_gate returns True when all needs_review items in review_queue are acknowledged."""
        model = make_clean_model()
        model.review_queue.append(
            ReviewItem(
                id="rq-002",
                kind="window_room_link",
                description="Low-confidence window area extraction on sheet A101",
                status="confirmed",
                confidence=0.5,
                provenance=P(),
                needs_review=True,
                acknowledged=True,
            )
        )
        report = run_checks(model)
        assert export_gate(report), (
            "export_gate should return True when all needs_review items "
            "in review_queue are acknowledged"
        )
