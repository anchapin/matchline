"""Pipeline-specific exception hierarchy."""

import importlib.util


class PipelineError(Exception):
    """Base exception for all pipeline errors."""

    pass


class PipelineDependencyError(PipelineError):
    """Raised when an optional dependency (e.g. ifcopenshell) is unavailable or broken."""

    pass


def ensure_ifcopenshell():
    """Make sure ``ifcopenshell`` is importable, or raise a usable error.

    Shared by every frontend that needs IfcOpenShell (the IFC importer, the
    BEM IFC4 exporter) so the dependency check lives in one place (#1339).
    Two failure modes stay distinguishable:

    * not installed at all -> "install with pip install ifcopenshell"
    * installed but broken (bad binary / missing system library) ->
      "reinstall with pip install ifcopenshell --force-reinstall"

    Raises:
        PipelineDependencyError: IfcOpenShell is missing or fails to import.
    """
    if importlib.util.find_spec("ifcopenshell") is None:
        raise PipelineDependencyError(
            "IfcOpenShell is not installed; install with `pip install ifcopenshell`"
        )
    try:
        import ifcopenshell  # noqa: F401
    except OSError as e:
        raise PipelineDependencyError(
            "ifcopenshell is installed but failed to import (broken binary or missing "
            "system library). Reinstall with: pip install ifcopenshell --force-reinstall"
        ) from e


class PipelineValidationError(PipelineError):
    """Raised when validate.py finds a conservation-law violation."""

    pass


class StageError(Exception):
    """Structured error raised when a pipeline stage fails with a defined recovery contract."""

    def __init__(self, stage_name: str, stage_index: int, msg: str, hint: str = ""):
        self.stage_name = stage_name
        self.stage_index = stage_index
        self.hint = hint
        super().__init__(f"[{stage_name}] {msg}")
