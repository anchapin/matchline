"""Pipeline-specific exception hierarchy."""


class PipelineError(Exception):
    """Base exception for all pipeline errors."""

    pass


class PipelineDependencyError(PipelineError):
    """Raised when an optional dependency (e.g. ifcopenshell) is unavailable or broken."""

    pass


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
