"""Defect injection utilities for pipeline-level integration tests.

This module provides utilities to inject realistic defects at pipeline boundaries
(drawing parsing, symbol detection, BIM export) and verify the pipeline handles
them gracefully.

Unlike model_factory.break_* functions which corrupt BuildingModel objects,
these utilities inject defects at the file/JSON boundary between pipeline stages.

Usage:
    # Corrupt a stage artifact to simulate a downstream parsing error
    with corrupt_file(stage_02_path) as corrupted:
        corrupt_json(corrupted, drop_keys=["spaces"])

    # Remove a required artifact to simulate a missing input
    with remove_file(stage_01_path):
        run_pipeline.main(ns)  # StageError raised

    # Inject a malformed value to simulate OCR/detector failure
    with corrupt_file(stage_02_path) as corrupted:
        patch_json(corrupted, ["spaces", "0", "area_m2"], -999.0)
"""

from __future__ import annotations

import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Generator


class CorruptedFile:
    """A corrupted file wrapper that preserves the original and restores on exit."""

    def __init__(self, path: Path):
        self.path = path
        self._backup_path: Path | None = None
        self._original_bytes: bytes | None = None

    def __enter__(self) -> "CorruptedFile":
        if self.path.exists():
            self._original_bytes = self.path.read_bytes()
            self._backup_path = self.path.with_suffix(self.path.suffix + ".bak")
            shutil.copy2(self.path, self._backup_path)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._backup_path and self._backup_path.exists():
            shutil.move(self._backup_path, self.path)
        elif self._original_bytes is None and self.path.exists():
            self.path.unlink()
        return False


def corrupt_json(corrupted_file: CorruptedFile, **patches: Any) -> Path:
    """Corrupt a JSON file by applying patches.

    Args:
        corrupted_file: A CorruptedFile context wrapping the path to corrupt.
        **patches: Keyword patches to apply. Supported patterns:
            - drop_keys=["key1", "key2"]: Remove top-level keys
            - set_values={"path.to.key": value}: Set a nested value using dot notation
            - set_null=["path.to.key"]: Set a value to null
            - set_invalid=["path.to.key"]: Set a value to an invalid type

    Returns:
        The path to the corrupted file.

    Example:
        with corrupt_file(stage_02_path) as corrupted:
            corrupt_json(corrupted,
                drop_keys=["spaces"],
                set_values={"metadata.provenance.confidence": 0.1}
            )
    """
    path = corrupted_file.path
    data = json.loads(path.read_text())

    for patch_name, patch_value in patches.items():
        if patch_name == "drop_keys":
            for key in patch_value:
                data.pop(key, None)
        elif patch_name == "set_values":
            for json_path, value in patch_value.items():
                _set_nested(data, json_path, value)
        elif patch_name == "set_null":
            for json_path in patch_value:
                _set_nested(data, json_path, None)
        elif patch_name == "set_invalid":
            for json_path in patch_value:
                _set_nested(data, json_path, "<INVALID>")

    path.write_text(json.dumps(data, indent=2, default=str))
    return path


def _set_nested(data: dict, path: str, value: Any) -> None:
    """Set a nested value in a dict using dot notation."""
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = value


def drop_nested(data: dict, path: str) -> None:
    """Drop a nested key from a dict using dot notation."""
    keys = path.split(".")
    current = data
    for key in keys[:-1]:
        if key not in current:
            return
        current = current[key]
    current.pop(keys[-1], None)


def inject_provenance_fault(
    path: Path,
    target_path: str,
    confidence: float = 0.05,
    method: str = "defect_injection_test",
) -> Path:
    """Inject a low-confidence provenance entry to trigger review queue triage.

    Args:
        path: Path to the JSON file to modify.
        target_path: Dot-notation path to the provenance list in the JSON.
        confidence: Confidence value to inject (0.0-1.0, low = triggers review).
        method: Method string to identify the fault source.

    Returns:
        The path to the modified file.
    """
    data = json.loads(path.read_text())
    provenance_list = _get_nested(data, target_path, [])

    provenance_list.append(
        {
            "source": "defect_injection",
            "method": method,
            "confidence": confidence,
            "sheet": "TEST_SHEET",
            "revision": "test",
            "note": "Injected low-confidence provenance for pipeline defect testing",
        }
    )

    _set_nested(data, target_path, provenance_list)
    path.write_text(json.dumps(data, indent=2, default=str))
    return path


def _get_nested(data: dict, path: str, default: Any = None) -> Any:
    """Get a nested value from a dict using dot notation."""
    keys = path.split(".")
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                return default
        else:
            return default
        if current is None:
            return default
    return current if current is not None else default


@contextmanager
def corrupt_file(path: Path) -> Generator[CorruptedFile, None, None]:
    """Context manager that corrupts a file and restores it on exit.

    Example:
        with corrupt_file(stage_02_path) as corrupted:
            corrupt_json(corrupted, drop_keys=["spaces"])
            # Run pipeline with corrupted input
            run_pipeline.main(ns)
        # Original file restored
    """
    wrapper = CorruptedFile(path)
    wrapper.__enter__()
    try:
        yield wrapper
    finally:
        wrapper.__exit__(None, None, None)


@contextmanager
def remove_file(path: Path) -> Generator[None, None, None]:
    """Context manager that removes a file and restores it on exit.

    Example:
        with remove_file(stage_01_path):
            run_pipeline.main(ns)  # FileNotFoundError or StageError
        # Original file restored
    """
    backup_path: Path | None = None
    had_file = path.exists()
    original_bytes: bytes | None = None

    if had_file:
        original_bytes = path.read_bytes()
        backup_path = path.with_suffix(path.suffix + ".removed_bak")
        shutil.move(path, backup_path)

    try:
        yield
    finally:
        if had_file and backup_path:
            shutil.move(backup_path, path)
        elif had_file and original_bytes:
            path.write_bytes(original_bytes)


@contextmanager
def corrupt_directory(dir_path: Path) -> Generator[Path, None, None]:
    """Context manager that clears a directory and restores its contents on exit.

    Example:
        with corrupt_directory(stage_06_bem_dir) as cleared:
            (cleared / "empty.txt").write_text("")
            run_pipeline.main(ns)
        # Original contents restored
    """
    if not dir_path.exists():
        dir_path.mkdir(parents=True, exist_ok=True)
        yield dir_path
        shutil.rmtree(dir_path)
        return

    original_contents: dict[Path, bytes] = {}
    for item in dir_path.rglob("*"):
        if item.is_file():
            original_contents[item] = item.read_bytes()

    shutil.rmtree(dir_path)
    dir_path.mkdir(parents=True)

    try:
        yield dir_path
    finally:
        shutil.rmtree(dir_path)
        for item_path, item_bytes in original_contents.items():
            item_path.parent.mkdir(parents=True, exist_ok=True)
            item_path.write_bytes(item_bytes)


def make_duplicates_beyond_threshold(
    path: Path, target_key: str = "spaces", count: int = 1001
) -> Path:
    """Inject duplicate entries to exceed deduplication thresholds.

    Args:
        path: Path to the JSON file.
        target_key: Top-level key containing the list to duplicate.
        count: Number of duplicate entries to create.

    Returns:
        The path to the modified file.
    """
    data = json.loads(path.read_text())

    if target_key not in data or not isinstance(data[target_key], list):
        return path

    original = data[target_key]
    if len(original) == 0:
        return path

    # Create duplicates that will fail uniqueness checks
    data[target_key] = original * count

    path.write_text(json.dumps(data, indent=2, default=str))
    return path


def truncate_file(path: Path, keep_bytes: int = 100) -> Path:
    """Truncate a file to simulate partial/corrupt data.

    Args:
        path: Path to the file to truncate.
        keep_bytes: Number of bytes to keep from the start.

    Returns:
        The path to the truncated file.
    """
    if not path.exists():
        return path

    original_bytes = path.read_bytes()
    path.write_bytes(original_bytes[:keep_bytes])
    return path


def inject_invalid_json(path: Path) -> Path:
    """Replace a JSON file with invalid JSON content.

    Args:
        path: Path to the JSON file.

    Returns:
        The path to the modified file.
    """
    backup = path.parent / f"{path.stem}_bak{path.suffix}"
    shutil.copy2(path, backup)

    try:
        path.write_text("{ invalid json content where { should be }")
    except Exception:
        shutil.move(backup, path)
        raise

    return path


class PipelineDefectInjector:
    """Object-based defect injector for complex pipeline scenarios.

    Supports staged defect injection where multiple artifacts need to be
    corrupted in sequence across multiple pipeline runs.

    Example:
        injector = PipelineDefectInjector(tmp_path)
        injector.add_stage_artifact("stage_01_building.json", "building")
        injector.add_stage_artifact("stage_02_model.json", "model")

        injector.corrupt("building", drop_keys=["spaces"])
        injector.corrupt("model", set_values={"metadata.provenance.confidence": 0.1})

        # Run pipeline - will use corrupted artifacts
        run_pipeline.main(ns)

        injector.restore()  # Restore all original artifacts
    """

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.artifacts: dict[str, Path] = {}
        self.backups: dict[str, Path | None] = {}
        self._active = False

    def add_stage_artifact(self, filename: str, alias: str) -> "PipelineDefectInjector":
        """Register a stage artifact file with an alias."""
        path = self.output_dir / filename
        self.artifacts[alias] = path
        return self

    def corrupt(self, alias: str, **patches: Any) -> "PipelineDefectInjector":
        """Corrupt an artifact by alias."""
        if alias not in self.artifacts:
            raise KeyError(f"No artifact registered with alias: {alias}")

        path = self.artifacts[alias]
        if not path.exists():
            return self

        # Backup original
        if alias not in self.backups:
            backup = path.with_suffix(path.suffix + ".defect_bak")
            shutil.copy2(path, backup)
            self.backups[alias] = backup

        # Apply corruption
        data = json.loads(path.read_text())

        for patch_name, patch_value in patches.items():
            if patch_name == "drop_keys":
                for key in patch_value:
                    data.pop(key, None)
            elif patch_name == "set_values":
                for json_path, value in patch_value.items():
                    _set_nested(data, json_path, value)
            elif patch_name == "set_null":
                for json_path in patch_value:
                    _set_nested(data, json_path, None)
            elif patch_name == "set_invalid":
                for json_path in patch_value:
                    _set_nested(data, json_path, "<INVALID>")

        path.write_text(json.dumps(data, indent=2, default=str))
        return self

    def restore(self) -> None:
        """Restore all corrupted artifacts to their original state."""
        for alias, backup_path in self.backups.items():
            if backup_path and backup_path.exists():
                artifact_path = self.artifacts.get(alias)
                if artifact_path:
                    shutil.move(backup_path, artifact_path)
        self.backups.clear()

    def __enter__(self) -> "PipelineDefectInjector":
        self._active = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.restore()
        self._active = False
        return False


# ---------------------------------------------------------------------------
# Pre-built defect scenarios for common pipeline failure modes
# ---------------------------------------------------------------------------


class DefectScenario:
    """Represents a specific defect scenario for pipeline testing."""

    name: str
    description: str
    target_stage: int
    defect_fn: Callable[[Path], None]

    def __init__(
        self,
        name: str,
        description: str,
        target_stage: int,
        defect_fn: Callable[[Path], None],
    ):
        self.name = name
        self.description = description
        self.target_stage = target_stage
        self.defect_fn = defect_fn


def corrupt_building_missing_spaces(output_dir: Path) -> DefectScenario:
    """Stage 1-2 boundary: building.json missing spaces key."""

    def apply(output_dir: Path):
        path = output_dir / "stage_01_building.json"
        if not path.exists():
            return
        with corrupt_file(path) as corrupted:
            corrupt_json(corrupted, drop_keys=["spaces"])

    return DefectScenario(
        name="corrupt_building_missing_spaces",
        description="Corrupt stage_01_building.json by removing the 'spaces' key to simulate a drawing parsing failure",
        target_stage=2,
        defect_fn=apply,
    )


def corrupt_building_missing_zones(output_dir: Path) -> DefectScenario:
    """Stage 1-2 boundary: building.json missing zones key."""

    def apply(output_dir: Path):
        path = output_dir / "stage_01_building.json"
        if not path.exists():
            return
        with corrupt_file(path) as corrupted:
            corrupt_json(corrupted, drop_keys=["zones"])

    return DefectScenario(
        name="corrupt_building_missing_zones",
        description="Corrupt stage_01_building.json by removing the 'zones' key",
        target_stage=2,
        defect_fn=apply,
    )


def corrupt_building_invalid_geometry(output_dir: Path) -> DefectScenario:
    """Stage 1-2 boundary: building.json has invalid polygon coordinates."""

    def apply(output_dir: Path):
        path = output_dir / "stage_01_building.json"
        if not path.exists():
            return
        with corrupt_file(path) as corrupted:
            corrupt_json(corrupted, set_invalid=["building_perimeter"])

    return DefectScenario(
        name="corrupt_building_invalid_geometry",
        description="Corrupt stage_01_building.json perimeter to invalid values",
        target_stage=2,
        defect_fn=apply,
    )


def corrupt_model_missing_provenance(output_dir: Path) -> DefectScenario:
    """Stage 2-3 boundary: model.json missing provenance metadata."""

    def apply(output_dir: Path):
        path = output_dir / "stage_02_model.json"
        if not path.exists():
            return
        with corrupt_file(path) as corrupted:
            corrupt_json(corrupted, drop_keys=["metadata"])

    return DefectScenario(
        name="corrupt_model_missing_provenance",
        description="Corrupt stage_02_model.json by removing metadata/provenance",
        target_stage=3,
        defect_fn=apply,
    )


def corrupt_model_low_confidence(output_dir: Path) -> DefectScenario:
    """Stage 2-3 boundary: model.json has low-confidence provenance entries."""

    def apply(output_dir: Path):
        path = output_dir / "stage_02_model.json"
        if not path.exists():
            return
        inject_provenance_fault(path, "metadata.provenance", confidence=0.05)

    return DefectScenario(
        name="corrupt_model_low_confidence",
        description="Inject low-confidence provenance to trigger review queue",
        target_stage=3,
        defect_fn=apply,
    )


def corrupt_model_missing_spaces(output_dir: Path) -> DefectScenario:
    """Stage 2-3 boundary: model.json missing spaces."""

    def apply(output_dir: Path):
        path = output_dir / "stage_02_model.json"
        if not path.exists():
            return
        with corrupt_file(path) as corrupted:
            corrupt_json(corrupted, drop_keys=["spaces"])

    return DefectScenario(
        name="corrupt_model_missing_spaces",
        description="Corrupt stage_02_model.json by removing spaces",
        target_stage=3,
        defect_fn=apply,
    )


def corrupt_simplified_missing_geometry(output_dir: Path) -> DefectScenario:
    """Stage 3-4 boundary: simplified model missing ring geometry."""

    def apply(output_dir: Path):
        path = output_dir / "stage_03_simplified.json"
        if not path.exists():
            return
        with corrupt_file(path) as corrupted:
            corrupt_json(corrupted, drop_keys=["simplified_ring"])

    return DefectScenario(
        name="corrupt_simplified_missing_geometry",
        description="Corrupt stage_03_simplified.json by removing simplified_ring",
        target_stage=4,
        defect_fn=apply,
    )


def corrupt_validation_missing_results(output_dir: Path) -> DefectScenario:
    """Stage 4-5 boundary: validation results missing checks."""

    def apply(output_dir: Path):
        path = output_dir / "stage_04_validation.json"
        if not path.exists():
            return
        with corrupt_file(path) as corrupted:
            corrupt_json(corrupted, drop_keys=["results"])

    return DefectScenario(
        name="corrupt_validation_missing_results",
        description="Corrupt stage_04_validation.json by removing results",
        target_stage=5,
        defect_fn=apply,
    )


# Registry of all available defect scenarios
DEFECT_SCENARIOS: list[Callable[[Path], DefectScenario]] = [
    corrupt_building_missing_spaces,
    corrupt_building_missing_zones,
    corrupt_building_invalid_geometry,
    corrupt_model_missing_provenance,
    corrupt_model_low_confidence,
    corrupt_model_missing_spaces,
    corrupt_simplified_missing_geometry,
    corrupt_validation_missing_results,
]
