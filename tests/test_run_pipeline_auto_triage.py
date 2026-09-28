from __future__ import annotations

from unittest.mock import patch

from building_model import BuildingModel, Provenance, ReviewItem
from run_pipeline import _run_auto_triage


class TestRunAutoTriage:
    def test_calls_triage_item_per_queue_item(self) -> None:
        model = BuildingModel(name="test", auto_triage=True)
        item = ReviewItem(
            id="rq-1",
            kind="space_no_geometry",
            description="Room 101 has no geometry",
            confidence=0.85,
            provenance=Provenance(
                sheet_id="A-101", revision="1", method="rule_check", confidence=0.85
            ),
        )
        model.review_queue.append(item)

        with patch.object(model, "_triage_item") as mock_triage:
            _run_auto_triage(model)
            mock_triage.assert_called_once_with(item)

    def test_calls_triage_item_multiple_items(self) -> None:
        model = BuildingModel(name="test", auto_triage=True)
        items = [
            ReviewItem(
                id=f"rq-{i}",
                kind="space_no_geometry",
                description=f"Room {i} has no geometry",
                confidence=0.80,
                provenance=Provenance(
                    sheet_id="A-101", revision="1", method="rule_check", confidence=0.80
                ),
            )
            for i in range(3)
        ]
        model.review_queue.extend(items)

        with patch.object(model, "_triage_item") as mock_triage:
            _run_auto_triage(model)
            assert mock_triage.call_count == 3
            mock_triage.assert_any_call(items[0])
            mock_triage.assert_any_call(items[1])
            mock_triage.assert_any_call(items[2])

    def test_empty_review_queue_calls_nothing(self) -> None:
        model = BuildingModel(name="test", auto_triage=True)
        with patch.object(model, "_triage_item") as mock_triage:
            _run_auto_triage(model)
            mock_triage.assert_not_called()

    def test_auto_triage_disabled_still_calls_triage_item(self) -> None:
        model = BuildingModel(name="test", auto_triage=False)
        item = ReviewItem(
            id="rq-1",
            kind="space_no_geometry",
            description="Room 101 has no geometry",
            confidence=0.85,
            provenance=Provenance(
                sheet_id="A-101", revision="1", method="rule_check", confidence=0.85
            ),
        )
        model.review_queue.append(item)

        with patch.object(model, "_triage_item") as mock_triage:
            _run_auto_triage(model)
            mock_triage.assert_called_once_with(item)

    def test_auto_triage_none_uses_enable_flag(self) -> None:
        model = BuildingModel(name="test", auto_triage=None)
        item = ReviewItem(
            id="rq-1",
            kind="space_no_geometry",
            description="Room 101 has no geometry",
            confidence=0.85,
            provenance=Provenance(
                sheet_id="A-101", revision="1", method="rule_check", confidence=0.85
            ),
        )
        model.review_queue.append(item)

        with patch.object(model, "_triage_item") as mock_triage:
            _run_auto_triage(model)
            mock_triage.assert_called_once_with(item)
