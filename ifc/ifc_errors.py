"""Coded error taxonomy for IFC import failures (roadmap gate #1161).

Every refusal the IFC frontend raises for bad input carries a stable,
machine-readable ``code`` plus a human remediation line, so callers and
CI can pin behavior instead of matching prose. Nothing raises a bare
traceback for malformed input: every code is an ``IfcImportError``, which
subclasses both ``StageError`` (so the pipeline's stage handler prints it
cleanly and exits 1) and ``ValueError`` (backwards compatibility: size and
element-limit callers pin ``ValueError``).

The code strings are a frozen contract; ``tests/test_ifc_robustness.py``
pins the full set. Adding a code is a minor change; renaming one needs a
deprecation cycle like any API change (docs/api.md).
"""

from pipeline_exceptions import StageError

#: Every stable code the IFC frontend may raise, pinned by tests.
IFC_ERROR_CODES = (
    "ifc.path_outside_workspace",
    "ifc.file_too_large",
    "ifc.element_limit_exceeded",
    "ifc.unreadable_file",
    "ifc.invalid_header",
    "ifc.truncated_file",
    "ifc.unsupported_schema",
    "ifc.missing_units",
    "ifc.broken_length_unit",
    "ifc.storey_elevation",
    "ifc.parse_error",
)


class IfcImportError(StageError, ValueError):
    """One IFC import refusal, with a stable machine-readable code.

    Attributes:
        code: Stable slug from :data:`IFC_ERROR_CODES`, e.g.
            ``"ifc.truncated_file"``. Pinned by tests; never reworded.
        remediation: One line telling the user how to fix the input.
    """

    def __init__(self, code: str, msg: str, remediation: str = ""):
        self.code = code
        self.remediation = remediation
        super().__init__(
            stage_name="ifc_import",
            stage_index=1,
            msg=f"{code}: {msg}",
            hint=remediation,
        )
