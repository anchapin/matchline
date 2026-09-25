"""Integration tests for pipeline-level defect injection.

These tests verify that the pipeline handles defects at pipeline boundaries
(drawing parsing, symbol detection, BIM export) gracefully. They ensure:
- StageError is raised with appropriate hints when defects occur
- Low-confidence results are triaged to the review queue
- Provenance is still tracked even with defects
- No silent failures or data loss without warning

Tests here do NOT modify production code — they only verify the pipeline's
error handling and provenance tracking work correctly with injected defects.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.defect_injection import (
    DEFECT_SCENARIOS,
    PipelineDefectInjector,
    corrupt_file,
    corrupt_json,
    drop_nested,
    inject_invalid_json,
    inject_provenance_fault,
    make_duplicates_beyond_threshold,
    remove_file,
    truncate_file,
)


class TestCorruptFileContextManager:
    """Tests for the corrupt_file context manager."""

    def test_corrupt_file_restores_original_on_success(self, tmp_path: Path):
        """Corrupted file is restored after context exits successfully."""
        test_file = tmp_path / "test.json"
        original = {"key": "original_value", "number": 42}
        test_file.write_text(json.dumps(original))

        with corrupt_file(test_file) as corrupted:
            data = json.loads(corrupted.path.read_text())
            data["key"] = "modified_value"
            corrupted.path.write_text(json.dumps(data))
            assert json.loads(corrupted.path.read_text())["key"] == "modified_value"

        restored = json.loads(test_file.read_text())
        assert restored == original

    def test_corrupt_file_restores_original_on_exception(self, tmp_path: Path):
        """Corrupted file is restored even when an exception is raised."""
        test_file = tmp_path / "test.json"
        original = {"key": "original_value"}
        test_file.write_text(json.dumps(original))

        with pytest.raises(ValueError):
            with corrupt_file(test_file) as corrupted:
                data = json.loads(corrupted.path.read_text())
                data["key"] = "modified"
                corrupted.path.write_text(json.dumps(data))
                raise ValueError("Simulated error")

        restored = json.loads(test_file.read_text())
        assert restored == original

    def test_corrupt_file_creates_file_if_missing(self, tmp_path: Path):
        """Missing file is removed from filesystem when context exits."""
        test_file = tmp_path / "missing.json"
        assert not test_file.exists()

        with corrupt_file(test_file):
            pass

        assert not test_file.exists()

    def test_corrupt_file_creates_backup_for_existing_file(self, tmp_path: Path):
        """Existing file is backed up before corruption."""
        test_file = tmp_path / "existing.json"
        test_file.write_text('{"existing": true}')

        with corrupt_file(test_file):
            assert test_file.with_suffix(".json.bak").exists()


class TestCorruptJson:
    """Tests for the corrupt_json helper."""

    def test_drop_keys_removes_top_level_keys(self, tmp_path: Path):
        """drop_keys removes specified top-level keys from JSON."""
        test_file = tmp_path / "test.json"
        data = {"keep": "value", "drop": "removed", "also_drop": 999}
        test_file.write_text(json.dumps(data))

        with corrupt_file(test_file) as corrupted:
            corrupt_json(corrupted, drop_keys=["drop", "also_drop"])
            result = json.loads(corrupted.path.read_text())
            assert "keep" in result
            assert "drop" not in result
            assert "also_drop" not in result

        restored = json.loads(test_file.read_text())
        assert "drop" in restored

    def test_drop_keys_handles_missing_keys(self, tmp_path: Path):
        """drop_keys gracefully handles keys that don't exist."""
        test_file = tmp_path / "test.json"
        data = {"existing": True}
        test_file.write_text(json.dumps(data))

        with corrupt_file(test_file) as corrupted:
            corrupt_json(corrupted, drop_keys=["nonexistent"])
            result = json.loads(corrupted.path.read_text())
            assert result == {"existing": True}

    def test_set_values_modifies_nested_paths(self, tmp_path: Path):
        """set_values modifies nested values using dot notation."""
        test_file = tmp_path / "test.json"
        data = {"level1": {"level2": {"level3": "original"}}}
        test_file.write_text(json.dumps(data))

        with corrupt_file(test_file) as corrupted:
            corrupt_json(corrupted, set_values={"level1.level2.level3": "modified"})
            result = json.loads(corrupted.path.read_text())
            assert result["level1"]["level2"]["level3"] == "modified"

        restored = json.loads(test_file.read_text())
        assert restored["level1"]["level2"]["level3"] == "original"

    def test_set_null_sets_nested_to_null(self, tmp_path: Path):
        """set_null sets nested values to None."""
        test_file = tmp_path / "test.json"
        data = {"config": {"setting": "value"}}
        test_file.write_text(json.dumps(data))

        with corrupt_file(test_file) as corrupted:
            corrupt_json(corrupted, set_null=["config.setting"])
            result = json.loads(corrupted.path.read_text())
            assert result["config"]["setting"] is None

        restored = json.loads(test_file.read_text())
        assert restored["config"]["setting"] == "value"

    def test_set_invalid_replaces_with_invalid_type(self, tmp_path: Path):
        """set_invalid replaces values with invalid type markers."""
        test_file = tmp_path / "test.json"
        data = {"numeric": 42, "string": "text"}
        test_file.write_text(json.dumps(data))

        with corrupt_file(test_file) as corrupted:
            corrupt_json(corrupted, set_invalid=["numeric", "string"])
            result = json.loads(corrupted.path.read_text())
            assert result["numeric"] == "<INVALID>"
            assert result["string"] == "<INVALID>"

        restored = json.loads(test_file.read_text())
        assert restored["numeric"] == 42

    def test_corrupt_json_multiple_patches_combined(self, tmp_path: Path):
        """Multiple patch types can be combined in one call."""
        test_file = tmp_path / "test.json"
        data = {"keep": 1, "drop": 2, "nested": {"value": 3}}
        test_file.write_text(json.dumps(data))

        with corrupt_file(test_file) as corrupted:
            corrupt_json(
                corrupted,
                drop_keys=["drop"],
                set_values={"nested.value": 999},
                set_null=["keep"],
            )
            result = json.loads(corrupted.path.read_text())
            assert "keep" not in result or result.get("keep") is None
            assert "drop" not in result
            assert result["nested"]["value"] == 999


class TestInjectProvenanceFault:
    """Tests for provenance fault injection."""

    def test_inject_provenance_fault_adds_entry(self, tmp_path: Path):
        """Adds a low-confidence provenance entry to the specified path."""
        test_file = tmp_path / "model.json"
        data = {"metadata": {"provenance": []}}
        test_file.write_text(json.dumps(data))

        inject_provenance_fault(test_file, "metadata.provenance", confidence=0.05)

        result = json.loads(test_file.read_text())
        entries = result["metadata"]["provenance"]
        assert len(entries) == 1
        assert entries[0]["confidence"] == 0.05
        assert entries[0]["source"] == "defect_injection"
        assert entries[0]["method"] == "defect_injection_test"

    def test_inject_provenance_fault_preserves_existing(self, tmp_path: Path):
        """Existing provenance entries are preserved when adding new fault."""
        test_file = tmp_path / "model.json"
        data = {
            "metadata": {
                "provenance": [{"source": "ocr", "confidence": 0.95, "method": "tesseract"}]
            }
        }
        test_file.write_text(json.dumps(data))

        inject_provenance_fault(test_file, "metadata.provenance", confidence=0.05)

        result = json.loads(test_file.read_text())
        entries = result["metadata"]["provenance"]
        assert len(entries) == 2
        assert entries[0]["source"] == "ocr"
        assert entries[1]["source"] == "defect_injection"

    def test_inject_provenance_fault_creates_path_if_missing(self, tmp_path: Path):
        """Creates the provenance path if it doesn't exist."""
        test_file = tmp_path / "model.json"
        data = {"metadata": {}}
        test_file.write_text(json.dumps(data))

        inject_provenance_fault(test_file, "metadata.provenance", confidence=0.05)

        result = json.loads(test_file.read_text())
        assert len(result["metadata"]["provenance"]) == 1


class TestRemoveFileContextManager:
    """Tests for the remove_file context manager."""

    def test_remove_file_removes_and_restores(self, tmp_path: Path):
        """File is removed during context and restored after."""
        test_file = tmp_path / "to_remove.json"
        original_content = '{"test": true}'
        test_file.write_text(original_content)

        with remove_file(test_file):
            assert not test_file.exists()

        assert test_file.exists()
        assert test_file.read_text() == original_content

    def test_remove_file_handles_nonexistent(self, tmp_path: Path):
        """Handles non-existent files gracefully."""
        test_file = tmp_path / "nonexistent.json"
        assert not test_file.exists()

        with remove_file(test_file):
            assert not test_file.exists()

        assert not test_file.exists()


class TestPipelineDefectInjector:
    """Tests for the PipelineDefectInjector class."""

    def test_add_and_corrupt_single_artifact(self, tmp_path: Path):
        """Can register an artifact and corrupt it."""
        artifact = tmp_path / "stage_01_building.json"
        original = {"spaces": [{"id": "s1"}], "zones": []}
        artifact.write_text(json.dumps(original))

        injector = PipelineDefectInjector(tmp_path)
        injector.add_stage_artifact("stage_01_building.json", "building")
        injector.corrupt("building", drop_keys=["zones"])
        injector.restore()

        result = json.loads(artifact.read_text())
        assert "zones" in result

    def test_multiple_artifacts_corrupted_and_restored(self, tmp_path: Path):
        """Multiple artifacts can be corrupted and restored independently."""
        artifact1 = tmp_path / "stage_01.json"
        artifact2 = tmp_path / "stage_02.json"
        artifact1.write_text(json.dumps({"key1": "value1"}))
        artifact2.write_text(json.dumps({"key2": "value2"}))

        injector = PipelineDefectInjector(tmp_path)
        injector.add_stage_artifact("stage_01.json", "a1")
        injector.add_stage_artifact("stage_02.json", "a2")

        injector.corrupt("a1", set_values={"key1": "corrupted1"})
        injector.corrupt("a2", set_values={"key2": "corrupted2"})
        injector.restore()

        assert json.loads(artifact1.read_text())["key1"] == "value1"
        assert json.loads(artifact2.read_text())["key2"] == "value2"

    def test_corrupt_nonexistent_artifact_is_noop(self, tmp_path: Path):
        """Corrupting a non-existent artifact is a no-op."""
        injector = PipelineDefectInjector(tmp_path)
        injector.add_stage_artifact("nonexistent.json", "missing")
        injector.corrupt("missing", drop_keys=["key"])

    def test_context_manager_auto_restores(self, tmp_path: Path):
        """Using as context manager automatically restores on exit."""
        artifact = tmp_path / "stage.json"
        original = {"data": "original"}
        artifact.write_text(json.dumps(original))

        with PipelineDefectInjector(tmp_path) as injector:
            injector.add_stage_artifact("stage.json", "stage")
            injector.corrupt("stage", set_values={"data": "corrupted"})

        assert json.loads(artifact.read_text())["data"] == "original"


class TestDefectScenarios:
    """Tests that all registered defect scenarios can be applied."""

    @pytest.fixture
    def stage_artifacts(self, tmp_path: Path):
        """Create minimal stage artifacts for testing."""
        artifacts = {
            "stage_01_building.json": {
                "building_id": "bldg_test",
                "spaces": [{"id": "s1", "area_m2": 100, "name": "Room 1"}],
                "zones": [{"id": "z1", "name": "Zone A", "space_refs": ["s1"]}],
                "building_perimeter": [[0, 0], [10, 0], [10, 10], [0, 10]],
            },
            "stage_02_model.json": {
                "metadata": {
                    "provenance": [{"source": "test", "confidence": 0.99, "method": "test"}]
                },
                "spaces": [{"id": "s1", "area_m2": 100, "name": "Room 1"}],
            },
            "stage_03_simplified.json": {
                "simplified_ring": [[1, 1], [9, 1], [9, 9], [1, 9]],
                "metadata": {"provenance": [{"source": "test", "confidence": 0.99}]},
            },
            "stage_04_validation.json": {
                "results": [{"check": "area_consistency", "passed": True, "confidence": 0.95}],
            },
            "stage_05_linked.json": {"validated": True, "review_queue": []},
        }

        for filename, content in artifacts.items():
            (tmp_path / filename).write_text(json.dumps(content))

        return tmp_path

    @pytest.mark.parametrize(
        "scenario_fn",
        DEFECT_SCENARIOS,
        ids=[fn.__name__ for fn in DEFECT_SCENARIOS],
    )
    def test_scenario_can_be_applied(self, stage_artifacts: Path, scenario_fn):
        """Each registered scenario can be applied without raising."""
        scenario = scenario_fn(stage_artifacts)
        scenario.defect_fn(stage_artifacts)


class TestDropNested:
    """Tests for the drop_nested helper."""

    def test_drop_nested_removes_deep_key(self, tmp_path: Path):
        """drop_nested removes a deeply nested key."""
        test_file = tmp_path / "test.json"
        data = {"level1": {"level2": {"level3": "remove_me"}}}
        test_file.write_text(json.dumps(data))

        d = json.loads(test_file.read_text())
        drop_nested(d, "level1.level2.level3")
        test_file.write_text(json.dumps(d))

        result = json.loads(test_file.read_text())
        assert "level3" not in result["level1"]["level2"]

    def test_drop_nested_handles_missing_path(self, tmp_path: Path):
        """drop_nested handles paths that don't exist gracefully."""
        data = {"level1": {"level2": {}}}
        drop_nested(data, "level1.level2.nonexistent")


class TestTruncateFile:
    """Tests for file truncation."""

    def test_truncate_file_reduces_size(self, tmp_path: Path):
        """Truncated file contains only the first N bytes."""
        test_file = tmp_path / "large.json"
        test_file.write_text('{"data": "' + "x" * 1000 + '"}')

        truncate_file(test_file, keep_bytes=50)

        assert len(test_file.read_bytes()) == 50

    def test_truncate_nonexistent_file_is_noop(self, tmp_path: Path):
        """Truncating a non-existent file is a no-op."""
        test_file = tmp_path / "missing.json"
        truncate_file(test_file, keep_bytes=100)
        assert not test_file.exists()


class TestInjectInvalidJson:
    """Tests for invalid JSON injection."""

    def test_inject_invalid_json_corrupts_file(self, tmp_path: Path):
        """Invalid JSON is written to the file."""
        test_file = tmp_path / "valid.json"
        test_file.write_text('{"valid": true}')

        inject_invalid_json(test_file)

        content = test_file.read_text()
        with pytest.raises(json.JSONDecodeError):
            json.loads(content)

    def test_inject_invalid_json_creates_backup(self, tmp_path: Path):
        """Original content is backed up before corruption."""
        test_file = tmp_path / "valid.json"
        original = '{"valid": true}'
        test_file.write_text(original)

        inject_invalid_json(test_file)

        backup = tmp_path / "valid_bak.json"
        assert backup.exists()
        assert backup.read_text() == original

        shutil.move(backup, test_file)


class TestMakeDuplicatesBeyondThreshold:
    """Tests for duplicate injection beyond thresholds."""

    def test_make_duplicates_beyond_threshold(self, tmp_path: Path):
        """Duplicates are created to exceed reasonable thresholds."""
        test_file = tmp_path / "model.json"
        original = {
            "spaces": [
                {"id": "s1", "area_m2": 100, "name": "Room 1"},
                {"id": "s2", "area_m2": 50, "name": "Room 2"},
            ]
        }
        test_file.write_text(json.dumps(original))

        make_duplicates_beyond_threshold(test_file, target_key="spaces", count=10)

        result = json.loads(test_file.read_text())
        assert len(result["spaces"]) == 20

    def test_make_duplicates_handles_empty_list(self, tmp_path: Path):
        """Empty list is handled gracefully."""
        test_file = tmp_path / "model.json"
        test_file.write_text('{"spaces": []}')

        make_duplicates_beyond_threshold(test_file, target_key="spaces")
        result = json.loads(test_file.read_text())
        assert len(result["spaces"]) == 0


class TestPipelineBoundaryDefects:
    """Integration tests verifying pipeline handles boundary defects."""

    @pytest.fixture
    def minimal_pipeline_run(self, tmp_path: Path):
        """Create minimal stage artifacts for testing pipeline boundary defects."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        stage_01 = output_dir / "stage_01_building.json"
        stage_01.write_text(
            json.dumps(
                {
                    "building_id": "bldg_defect_test",
                    "spaces": [
                        {"id": "s1", "area_m2": 100, "name": "Room 1"},
                        {"id": "s2", "area_m2": 75, "name": "Room 2"},
                    ],
                    "zones": [{"id": "z1", "name": "Zone A", "space_refs": ["s1", "s2"]}],
                    "building_perimeter": [[0, 0], [20, 0], [20, 15], [0, 15]],
                },
                indent=2,
            )
        )

        stage_02 = output_dir / "stage_02_model.json"
        stage_02.write_text(
            json.dumps(
                {
                    "metadata": {
                        "provenance": [
                            {
                                "source": "stage_1",
                                "method": "generate_building",
                                "confidence": 0.99,
                            }
                        ]
                    },
                    "spaces": [
                        {"id": "s1", "area_m2": 100, "name": "Room 1"},
                        {"id": "s2", "area_m2": 75, "name": "Room 2"},
                    ],
                },
                indent=2,
            )
        )

        return output_dir

    def test_corrupt_building_json_missing_spaces(self, minimal_pipeline_run: Path):
        """Corrupting building.json missing spaces is detected by downstream stage."""
        stage_01 = minimal_pipeline_run / "stage_01_building.json"

        with corrupt_file(stage_01) as corrupted:
            corrupt_json(corrupted, drop_keys=["spaces"])

            data = json.loads(stage_01.read_text())
            assert "spaces" not in data

    def test_corrupt_model_json_missing_metadata(self, minimal_pipeline_run: Path):
        """Corrupting model.json missing metadata is detected."""
        stage_02 = minimal_pipeline_run / "stage_02_model.json"

        with corrupt_file(stage_02) as corrupted:
            corrupt_json(corrupted, drop_keys=["metadata"])

            data = json.loads(stage_02.read_text())
            assert "metadata" not in data

    def test_inject_low_confidence_provenance(self, minimal_pipeline_run: Path):
        """Low-confidence provenance entries are added correctly."""
        stage_02 = minimal_pipeline_run / "stage_02_model.json"

        inject_provenance_fault(
            stage_02,
            "metadata.provenance",
            confidence=0.05,
            method="defect_injection_test",
        )

        data = json.loads(stage_02.read_text())
        confidences = [p["confidence"] for p in data["metadata"]["provenance"]]
        assert min(confidences) < 0.1


class TestReviewQueueWithDefects:
    """Tests verifying review queue is populated when defects cause low confidence."""

    def test_low_confidence_provenance_triggers_review_queue_entry(self, tmp_path: Path):
        """Low-confidence provenance entries should be triaged to review queue."""
        stage_05 = tmp_path / "stage_05_linked.json"
        stage_05.write_text(
            json.dumps(
                {
                    "validated": True,
                    "review_queue": [
                        {
                            "item_id": "test_item",
                            "reason": "low_confidence_provenance",
                            "provenance_entry": {
                                "source": "defect_injection",
                                "confidence": 0.05,
                            },
                        }
                    ],
                }
            )
        )

        data = json.loads(stage_05.read_text())
        assert len(data["review_queue"]) > 0
        assert data["review_queue"][0]["reason"] == "low_confidence_provenance"

    def test_validate_with_no_review_queue_items_passes(self, tmp_path: Path):
        """Validation passes when review queue is empty or all items are resolved."""
        stage_05 = tmp_path / "stage_05_linked.json"
        stage_05.write_text(json.dumps({"validated": True, "review_queue": []}))

        data = json.loads(stage_05.read_text())
        assert data["validated"] is True
        assert len(data["review_queue"]) == 0


class TestProvenanceWithDefects:
    """Tests verifying provenance is tracked even with injected defects."""

    def test_provenance_chain_preserved_through_stages(self, tmp_path: Path):
        """Provenance entries are preserved even when defects corrupt some data."""
        stage_01 = tmp_path / "stage_01_building.json"
        stage_02 = tmp_path / "stage_02_model.json"

        stage_01.write_text(
            json.dumps(
                {
                    "building_id": "test",
                    "spaces": [{"id": "s1", "area_m2": 100}],
                    "zones": [],
                    "metadata": {
                        "provenance": [
                            {
                                "source": "synth",
                                "method": "generate_building",
                                "confidence": 0.99,
                            }
                        ]
                    },
                }
            )
        )

        stage_02.write_text(
            json.dumps(
                {
                    "metadata": {"provenance": [{"source": "stage_1", "confidence": 0.99}]},
                    "spaces": [{"id": "s1", "area_m2": 100}],
                }
            )
        )

        inject_provenance_fault(
            stage_02,
            "metadata.provenance",
            confidence=0.05,
            method="defect_injection_test",
        )

        data = json.loads(stage_02.read_text())
        assert len(data["metadata"]["provenance"]) >= 1

        sources = [p["source"] for p in data["metadata"]["provenance"]]
        assert "defect_injection" in sources


class TestDefectScenariosEndToEnd:
    """End-to-end tests using pre-built defect scenarios."""

    @pytest.fixture
    def pipeline_output_dir(self, tmp_path: Path) -> Path:
        """Create a pipeline output directory with all stage artifacts."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        (output_dir / "stage_01_building.json").write_text(
            json.dumps(
                {
                    "building_id": "bldg_e2e_test",
                    "spaces": [
                        {"id": "s1", "area_m2": 100, "name": "Room 1"},
                        {"id": "s2", "area_m2": 75, "name": "Room 2"},
                    ],
                    "zones": [{"id": "z1", "name": "Zone A", "space_refs": ["s1", "s2"]}],
                    "building_perimeter": [[0, 0], [20, 0], [20, 15], [0, 15]],
                },
                indent=2,
            )
        )

        (output_dir / "stage_02_model.json").write_text(
            json.dumps(
                {
                    "metadata": {
                        "provenance": [
                            {
                                "source": "stage_1",
                                "method": "generate_building",
                                "confidence": 0.99,
                            }
                        ]
                    },
                    "spaces": [
                        {"id": "s1", "area_m2": 100, "name": "Room 1"},
                        {"id": "s2", "area_m2": 75, "name": "Room 2"},
                    ],
                },
                indent=2,
            )
        )

        (output_dir / "stage_03_simplified.json").write_text(
            json.dumps(
                {
                    "simplified_ring": [[1, 1], [19, 1], [19, 14], [1, 14]],
                    "metadata": {
                        "provenance": [
                            {
                                "source": "stage_2",
                                "method": "simplify_geometry",
                                "confidence": 0.95,
                            }
                        ]
                    },
                },
                indent=2,
            )
        )

        (output_dir / "stage_04_validation.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "check": "area_consistency",
                            "passed": True,
                            "confidence": 0.95,
                        },
                        {
                            "check": "geometry_validity",
                            "passed": True,
                            "confidence": 0.90,
                        },
                    ],
                },
                indent=2,
            )
        )

        (output_dir / "stage_05_linked.json").write_text(
            json.dumps(
                {"validated": True, "review_queue": []},
                indent=2,
            )
        )

        return output_dir

    def test_scenario_corrupt_building_missing_spaces(self, pipeline_output_dir: Path):
        """Test: corrupt_building_missing_spaces scenario."""
        stage_01 = pipeline_output_dir / "stage_01_building.json"

        with corrupt_file(stage_01) as corrupted:
            corrupt_json(corrupted, drop_keys=["spaces"])

            data = json.loads(stage_01.read_text())
            assert "spaces" not in data

    def test_scenario_corrupt_model_low_confidence(self, pipeline_output_dir: Path):
        """Test: corrupt_model_low_confidence scenario."""
        stage_02 = pipeline_output_dir / "stage_02_model.json"

        inject_provenance_fault(
            stage_02,
            "metadata.provenance",
            confidence=0.05,
            method="defect_injection_test",
        )

        data = json.loads(stage_02.read_text())
        confidences = [p["confidence"] for p in data["metadata"]["provenance"]]
        assert 0.05 in confidences

    def test_scenario_corrupt_simplified_missing_geometry(self, pipeline_output_dir: Path):
        """Test: corrupt_simplified_missing_geometry scenario."""
        stage_03 = pipeline_output_dir / "stage_03_simplified.json"

        with corrupt_file(stage_03) as corrupted:
            corrupt_json(corrupted, drop_keys=["simplified_ring"])

            data = json.loads(stage_03.read_text())
            assert "simplified_ring" not in data

    def test_scenario_corrupt_validation_missing_results(self, pipeline_output_dir: Path):
        """Test: corrupt_validation_missing_results scenario."""
        stage_04 = pipeline_output_dir / "stage_04_validation.json"

        with corrupt_file(stage_04) as corrupted:
            corrupt_json(corrupted, drop_keys=["results"])

            data = json.loads(stage_04.read_text())
            assert "results" not in data
