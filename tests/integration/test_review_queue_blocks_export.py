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

    def test_multiple_unacknowledged_items_block_export(self):
        """export_gate returns False when multiple unacknowledged items in review_queue."""
        model = make_clean_model()
        model.review_queue.extend(
            [
                ReviewItem(
                    id="rq-003",
                    kind="window_room_link",
                    description="Low-confidence window area extraction on sheet A101",
                    status="open",
                    confidence=0.5,
                    provenance=P(),
                    needs_review=True,
                    acknowledged=False,
                ),
                ReviewItem(
                    id="rq-004",
                    kind="zone_assignment",
                    description="Zone assignment inconsistency on sheet A102",
                    status="open",
                    confidence=0.6,
                    provenance=P(),
                    needs_review=True,
                    acknowledged=False,
                ),
            ]
        )
        report = run_checks(model)
        assert not export_gate(report), (
            "export_gate should return False when multiple unacknowledged "
            "needs_review items are present in review_queue"
        )

    def test_mixed_acknowledged_and_unacknowledged_items(self):
        """export_gate returns False when any unacknowledged needs_review items exist."""
        model = make_clean_model()
        model.review_queue.extend(
            [
                ReviewItem(
                    id="rq-005",
                    kind="window_room_link",
                    description="Low-confidence window area extraction on sheet A101",
                    status="confirmed",
                    confidence=0.5,
                    provenance=P(),
                    needs_review=True,
                    acknowledged=True,
                ),
                ReviewItem(
                    id="rq-006",
                    kind="zone_assignment",
                    description="Zone assignment inconsistency on sheet A102",
                    status="open",
                    confidence=0.6,
                    provenance=P(),
                    needs_review=True,
                    acknowledged=False,
                ),
            ]
        )
        report = run_checks(model)
        assert not export_gate(report), (
            "export_gate should return False when any unacknowledged "
            "needs_review item exists, even if others are acknowledged"
        )

    def test_pipeline_integration_blocks_export_with_unacknowledged(self):
        """Integration test: pipeline flow with unacknowledged items blocks export.

        This test simulates the pipeline behavior: run_checks populates
        review_queue items, then export_gate checks if export is allowed.
        """
        from run_pipeline import run_checks as pipeline_run_checks

        model = make_clean_model()
        model.review_queue.append(
            ReviewItem(
                id="rq-007",
                kind="envelope_area",
                description="Envelope area mismatch detected",
                status="open",
                confidence=0.7,
                provenance=P(),
                needs_review=True,
                acknowledged=False,
            )
        )
        report = pipeline_run_checks(model)
        assert not export_gate(report), (
            "Pipeline integration: export_gate should return False when "
            "unacknowledged needs_review items are present"
        )

    def test_confirmed_review_items_do_not_block_export(self):
        """export_gate returns True when review_queue has confirmed items (fixes #348).

        When a review item is confirmed via the pipeline (status='confirmed'), it must
        not block export even if needs_review=True and acknowledged=False --
        the confirmed status itself indicates the item has been reviewed and resolved.
        """
        model = make_clean_model()
        model.review_queue.append(
            ReviewItem(
                id="rq-003",
                kind="window_room_link",
                description="Low-confidence window area extraction on sheet A101",
                status="confirmed",
                confidence=0.85,
                provenance=P(),
                needs_review=True,
                acknowledged=False,
            )
        )
        report = run_checks(model)
        assert export_gate(report), (
            "export_gate should return True when review_queue has confirmed items "
            "(status='confirmed'), regardless of needs_review/acknowledged flags"
        )

    def test_rejected_review_items_do_not_block_export(self):
        """export_gate returns True when review_queue has rejected items (fixes #348).

        When a review item is rejected via the pipeline (status='rejected'), it must
        not block export even if needs_review=True and acknowledged=False --
        the rejected status itself indicates the item has been reviewed and resolved.
        """
        model = make_clean_model()
        model.review_queue.append(
            ReviewItem(
                id="rq-004",
                kind="window_room_link",
                description="Low-confidence window area extraction on sheet A101",
                status="rejected",
                confidence=0.85,
                provenance=P(),
                needs_review=True,
                acknowledged=False,
            )
        )
        report = run_checks(model)
        assert export_gate(report), (
            "export_gate should return True when review_queue has rejected items "
            "(status='rejected'), regardless of needs_review/acknowledged flags"
        )
