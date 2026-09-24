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

    def test_write_functions_not_called_when_review_queue_blocks(self, tmp_path, monkeypatch):
        """write_gbxml and write_ifc4 must never be called when review queue blocks export."""
        import argparse
        import sys

        import bem_export
        import run_pipeline
        from building_model import ReviewItem
        from tests.model_factory import P, make_clean_model

        out_dir = tmp_path / "run_review_block"
        out_dir.mkdir(parents=True, exist_ok=True)

        ns = argparse.Namespace(
            seed=101,
            image=None,
            aec_bench=None,
            detections=None,
            schedule_csv=None,
            weights=None,
            out_dir=out_dir,
            open_office_span=False,
            elevation_key="elev_grid",
            simplify_tol=0.02,
        )

        model = make_clean_model()
        model.review_queue.append(
            ReviewItem(
                id="rq-003",
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
        assert not export_gate(report), "precondition: export_gate should be False"

        def mock_run_checks(model, **kwargs):
            return report

        monkeypatch.setattr("run_pipeline.run_checks", mock_run_checks)

        gbxml_called = False
        ifc_called = False

        original_write_gbxml = bem_export.write_gbxml
        original_write_ifc4 = bem_export.write_ifc4

        def mock_write_gbxml(*args, **kwargs):
            nonlocal gbxml_called
            gbxml_called = True
            return original_write_gbxml(*args, **kwargs)

        def mock_write_ifc4(*args, **kwargs):
            nonlocal ifc_called
            ifc_called = True
            return original_write_ifc4(*args, **kwargs)

        monkeypatch.setattr(bem_export, "write_gbxml", mock_write_gbxml)
        monkeypatch.setattr(bem_export, "write_ifc4", mock_write_ifc4)

        exit_called = False

        def mock_exit(code=0):
            nonlocal exit_called
            exit_called = True
            raise SystemExit(code)

        monkeypatch.setattr(sys, "exit", mock_exit)

        try:
            run_pipeline.main(ns)
        except SystemExit:
            pass

        assert not gbxml_called, (
            "write_gbxml must NOT be called when review_queue blocks export; "
            "unacknowledged needs_review items must block export"
        )
        assert not ifc_called, (
            "write_ifc4 must NOT be called when review_queue blocks export; "
            "unacknowledged needs_review items must block export"
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
