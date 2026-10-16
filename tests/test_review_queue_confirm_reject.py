"""Integration tests for review queue confirm/reject workflow (Issue #266).

Tests the full workflow:
1. Model with review-flagged items is loaded
2. Items are confirmed or rejected via API and CLI
3. Confirmed items appear in output; rejected items do not
4. Validation is re-run after confirm/reject
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from building_model import BuildingModel, Provenance, ReviewItem
from run_review import _confirm_item, _reject_item


def _run_cli(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run([sys.executable, "-m", "cli"] + args, **kwargs)


class TestReviewQueueConfirmRejectAPI:
    """Programmatic/API tests for confirm/reject workflow."""

    def _make_model_with_review_items(self) -> BuildingModel:
        """Create a minimal BuildingModel with two review items for confirm/reject."""
        model = BuildingModel()
        prov = Provenance(sheet_id="sheet_A", revision=1, method="test", confidence=0.75)

        item1 = ReviewItem(
            id="R1",
            kind="window_room_link",
            description="Window W1 in Room 101",
            status="open",
            confidence=0.75,
            provenance=prov,
        )
        item2 = ReviewItem(
            id="R2",
            kind="fixture_assignment",
            description="Light fixture L1 in Room 102",
            status="open",
            confidence=0.65,
            provenance=prov,
        )
        model.review_queue.append(item1)
        model.review_queue.append(item2)
        return model

    def test_confirm_item_updates_status(self):
        """Confirm via API changes item status to 'confirmed'."""
        model = self._make_model_with_review_items()
        confirmed_model, msg = _confirm_item(model, "R1")

        r1 = next(i for i in confirmed_model.review_queue if i.id == "R1")
        assert r1.status == "confirmed"
        assert "R1" in msg
        assert "confirmed" in msg.lower()

    def test_reject_item_updates_status(self):
        """Reject via API changes item status to 'rejected'."""
        model = self._make_model_with_review_items()
        rejected_model, msg = _reject_item(model, "R1")

        r1 = next(i for i in rejected_model.review_queue if i.id == "R1")
        assert r1.status == "rejected"
        assert "R1" in msg
        assert "rejected" in msg.lower()

    def test_confirm_nonexistent_raises(self):
        """Confirming a non-existent item raises ValueError."""
        model = self._make_model_with_review_items()
        with pytest.raises(ValueError, match="DOES_NOT_EXIST"):
            _confirm_item(model, "DOES_NOT_EXIST")

    def test_reject_nonexistent_raises(self):
        """Rejecting a non-existent item raises ValueError."""
        model = self._make_model_with_review_items()
        with pytest.raises(ValueError, match="DOES_NOT_EXIST"):
            _reject_item(model, "DOES_NOT_EXIST")

    def test_confirmed_item_excluded_from_open_filter(self):
        """Confirmed items are excluded from 'open' status filter."""
        model = self._make_model_with_review_items()
        confirmed_model, _ = _confirm_item(model, "R1")

        open_items = [i for i in confirmed_model.review_queue if i.status == "open"]
        assert all(i.id != "R1" for i in open_items)

    def test_rejected_item_excluded_from_open_filter(self):
        """Rejected items are excluded from 'open' status filter."""
        model = self._make_model_with_review_items()
        rejected_model, _ = _reject_item(model, "R1")

        open_items = [i for i in rejected_model.review_queue if i.status == "open"]
        assert all(i.id != "R1" for i in open_items)

    def test_confirmed_item_included_when_showing_all(self):
        """Confirmed items appear when show_all=True."""
        model = self._make_model_with_review_items()
        confirmed_model, _ = _confirm_item(model, "R1")

        all_items = [i for i in confirmed_model.review_queue]
        assert any(i.id == "R1" and i.status == "confirmed" for i in all_items)

    def test_rejected_item_included_when_showing_all(self):
        """Rejected items appear when show_all=True."""
        model = self._make_model_with_review_items()
        rejected_model, _ = _reject_item(model, "R1")

        all_items = [i for i in rejected_model.review_queue]
        assert any(i.id == "R1" and i.status == "rejected" for i in all_items)

    def test_multiple_items_confirm_reject_independent(self):
        """Confirming one item does not affect another's status."""
        model = self._make_model_with_review_items()
        confirmed_model, _ = _confirm_item(model, "R1")

        r2 = next(i for i in confirmed_model.review_queue if i.id == "R2")
        assert r2.status == "open"


class TestReviewQueueConfirmRejectCLI:
    """CLI tests for confirm/reject workflow."""

    def _write_model_to_temp(self, model: BuildingModel) -> Path:
        """Write model to a temp JSON file and return the path."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(fd, "w") as f:
            f.write(model.to_json())
        return Path(path)

    def _make_model_with_review_items(self) -> BuildingModel:
        """Create a minimal BuildingModel with two review items."""
        model = BuildingModel()
        prov = Provenance(sheet_id="sheet_A", revision=1, method="test", confidence=0.75)

        item1 = ReviewItem(
            id="R1",
            kind="window_room_link",
            description="Window W1 in Room 101",
            status="open",
            confidence=0.75,
            provenance=prov,
        )
        item2 = ReviewItem(
            id="R2",
            kind="fixture_assignment",
            description="Light fixture L1 in Room 102",
            status="open",
            confidence=0.65,
            provenance=prov,
        )
        model.review_queue.append(item1)
        model.review_queue.append(item2)
        return model

    def test_review_confirm_cli_succeeds(self):
        """`matchline review --confirm <id>` exits 0 and saves updated model."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        r = _run_cli(["review", str(model_path), "--confirm", "R1"])
        assert r.returncode == 0, r.stderr
        assert "confirmed" in r.stdout.lower()
        assert "saved" in r.stdout.lower() or "Model saved" in r.stdout

        updated = BuildingModel.from_json(model_path.read_text())
        r1 = next(i for i in updated.review_queue if i.id == "R1")
        assert r1.status == "confirmed"

        model_path.unlink()

    def test_review_reject_cli_succeeds(self):
        """`matchline review --reject <id>` exits 0 and saves updated model."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        r = _run_cli(["review", str(model_path), "--reject", "R2"])
        assert r.returncode == 0, r.stderr
        assert "rejected" in r.stdout.lower()
        assert "saved" in r.stdout.lower() or "Model saved" in r.stdout

        updated = BuildingModel.from_json(model_path.read_text())
        r2 = next(i for i in updated.review_queue if i.id == "R2")
        assert r2.status == "rejected"

        model_path.unlink()

    def test_review_confirm_nonexistent_cli_fails(self):
        """`matchline review --confirm <nonexistent>` exits 1."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        r = _run_cli(["review", str(model_path), "--confirm", "DOES_NOT_EXIST"])
        assert r.returncode == 1
        assert "Error" in r.stderr or "not found" in r.stderr.lower()

        model_path.unlink()

    def test_review_reject_nonexistent_cli_fails(self):
        """`matchline review --reject <nonexistent>` exits 1."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        r = _run_cli(["review", str(model_path), "--reject", "DOES_NOT_EXIST"])
        assert r.returncode == 1
        assert "Error" in r.stderr or "not found" in r.stderr.lower()

        model_path.unlink()

    def test_review_confirm_and_reject_mutually_exclusive(self):
        """Specifying both --confirm and --reject exits 1."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        r = _run_cli(["review", str(model_path), "--confirm", "R1", "--reject", "R2"])
        assert r.returncode == 1
        assert "only one" in r.stderr.lower() or "not both" in r.stderr.lower()

        model_path.unlink()

    def test_review_list_after_confirm_shows_confirmed(self):
        """After confirming R1, `review --list --show-all` includes R1 as confirmed."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        _run_cli(["review", str(model_path), "--confirm", "R1"])

        r = _run_cli(["review", str(model_path), "--list", "--show-all", "--format", "json"])
        assert r.returncode == 0

        items = json.loads(r.stdout)
        r1 = next((i for i in items if i["id"] == "R1"), None)
        assert r1 is not None
        assert r1["status"] == "confirmed"

        model_path.unlink()

    def test_review_list_excludes_rejected_by_default(self):
        """After rejecting R1, `review --list` (without --show-all) excludes R1."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        _run_cli(["review", str(model_path), "--reject", "R1"])

        r = _run_cli(["review", str(model_path), "--list", "--format", "json"])
        assert r.returncode == 0

        items = json.loads(r.stdout)
        r1 = next((i for i in items if i["id"] == "R1"), None)
        assert r1 is None

        model_path.unlink()


class TestReviewQueueConfirmRejectValidationReRun:
    """Tests that validation is re-run after confirm/reject."""

    def _write_model_to_temp(self, model: BuildingModel) -> Path:
        """Write model to a temp JSON file and return the path."""
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(fd, "w") as f:
            f.write(model.to_json())
        return Path(path)

    def _make_model_with_review_items(self) -> BuildingModel:
        """Create a minimal BuildingModel with two review items."""
        model = BuildingModel()
        prov = Provenance(sheet_id="sheet_A", revision=1, method="test", confidence=0.75)

        item1 = ReviewItem(
            id="R1",
            kind="window_room_link",
            description="Window W1 in Room 101",
            status="open",
            confidence=0.75,
            provenance=prov,
        )
        model.review_queue.append(item1)
        return model

    def test_confirm_runs_validation(self):
        """Confirm action triggers re-validation and prints summary."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        r = _run_cli(["review", str(model_path), "--confirm", "R1"])
        assert r.returncode == 0
        assert (
            "validation" in r.stdout.lower()
            or "passed" in r.stdout.lower()
            or "errors" in r.stdout.lower()
        )

        model_path.unlink()

    def test_reject_runs_validation(self):
        """Reject action triggers re-validation and prints summary."""
        model = self._make_model_with_review_items()
        model_path = self._write_model_to_temp(model)

        r = _run_cli(["review", str(model_path), "--reject", "R1"])
        assert r.returncode == 0
        assert (
            "validation" in r.stdout.lower()
            or "passed" in r.stdout.lower()
            or "errors" in r.stdout.lower()
        )

        model_path.unlink()


class TestReviewQueueBulkCLI:
    """Bulk confirm/reject with filters, and worst-first listing (#996)."""

    def _write_model_to_temp(self, model: BuildingModel) -> Path:
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(fd, "w") as f:
            f.write(model.to_json())
        return Path(path)

    def _make_bulk_model(self) -> BuildingModel:
        model = BuildingModel()
        prov = Provenance(sheet_id="sheet_A", revision=1, method="test", confidence=0.75)
        for iid, kind, conf, urg in [
            ("A1", "window_room_link", 0.40, 1),
            ("A2", "window_room_link", 0.80, 0),
            ("A3", "fixture_assignment", 0.30, 2),
        ]:
            item = ReviewItem(
                id=iid,
                kind=kind,
                description=f"{kind} {iid}",
                status="open",
                confidence=conf,
                provenance=prov,
                urgency=urg,
            )
            model.review_queue.append(item)
        model.review_queue[0].target = {"kind": "space", "id": "L1-101"}
        done = ReviewItem(
            id="A4",
            kind="space_merge",
            description="already handled",
            status="confirmed",
            confidence=0.5,
            provenance=prov,
            urgency=3,
        )
        model.review_queue.append(done)
        return model

    def test_bulk_confirm_kind_filter(self):
        model = self._make_bulk_model()
        model_path = self._write_model_to_temp(model)
        r = _run_cli(["review", str(model_path), "--bulk-confirm", "--kind", "window_room_link"])
        assert r.returncode == 0, r.stderr
        assert "Bulk confirmed: 2 item(s) matched" in r.stdout
        updated = BuildingModel.from_json(model_path.read_text())
        statuses = {i.id: i.status for i in updated.review_queue}
        assert statuses == {"A1": "confirmed", "A2": "confirmed", "A3": "open", "A4": "confirmed"}
        model_path.unlink()

    def test_bulk_reject_confidence_band(self):
        model = self._make_bulk_model()
        model_path = self._write_model_to_temp(model)
        r = _run_cli(["review", str(model_path), "--bulk-reject", "--conf-below", "0.5"])
        assert r.returncode == 0, r.stderr
        assert "Bulk rejected: 2 item(s) matched" in r.stdout
        updated = BuildingModel.from_json(model_path.read_text())
        statuses = {i.id: i.status for i in updated.review_queue}
        assert statuses["A1"] == "rejected" and statuses["A3"] == "rejected"
        assert statuses["A2"] == "open"
        model_path.unlink()

    def test_bulk_conf_above_and_level(self):
        from building_model import Level, Space

        model = self._make_bulk_model()
        model.levels.append(Level(id="L1", wall_height_m=3.0))
        model.spaces["L1-101"] = Space(
            id="L1-101",
            level_id="L1",
            number="101",
            polygon_m=[[0, 0], [4, 0], [4, 4], [0, 4]],
        )
        model_path = self._write_model_to_temp(model)
        r = _run_cli(
            ["review", str(model_path), "--bulk-confirm", "--conf-above", "0.2", "--level", "L1"]
        )
        assert r.returncode == 0, r.stderr
        # only A1 carries a target on L1
        assert "Bulk confirmed: 1 item(s) matched" in r.stdout
        model_path.unlink()

    def test_bulk_zero_matches_fails(self):
        model = self._make_bulk_model()
        model_path = self._write_model_to_temp(model)
        r = _run_cli(["review", str(model_path), "--bulk-confirm", "--kind", "nosuch"])
        assert r.returncode == 1
        assert "no open review items match" in r.stderr
        model_path.unlink()

    def test_bulk_and_single_are_exclusive(self):
        model = self._make_bulk_model()
        model_path = self._write_model_to_temp(model)
        r = _run_cli(["review", str(model_path), "--bulk-confirm", "--confirm", "A1"])
        assert r.returncode == 1
        model_path.unlink()

    def test_bulk_reject_both_fails(self):
        model = self._make_bulk_model()
        model_path = self._write_model_to_temp(model)
        r = _run_cli(["review", str(model_path), "--bulk-confirm", "--bulk-reject"])
        assert r.returncode == 1
        model_path.unlink()

    def test_list_is_worst_first(self):
        model = self._make_bulk_model()
        model_path = self._write_model_to_temp(model)
        r = _run_cli(["review", str(model_path), "--list"])
        assert r.returncode == 0, r.stderr
        order = [line for line in r.stdout.splitlines() if line.startswith("[A")]
        ids = [line.split("]")[0].strip("[") for line in order]
        # open first, highest urgency, then lowest confidence; resolved A4 never shown
        assert ids == ["A3", "A1", "A2"]
        model_path.unlink()

    def test_list_json_carries_level(self):
        model = self._make_bulk_model()
        model_path = self._write_model_to_temp(model)
        r = _run_cli(["review", str(model_path), "--list", "--format", "json"])
        assert r.returncode == 0, r.stderr
        rows = json.loads(r.stdout)
        assert all("level" in row for row in rows)
        model_path.unlink()
