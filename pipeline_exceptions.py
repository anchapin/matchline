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
