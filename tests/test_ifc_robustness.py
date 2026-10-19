"""Malformed-input corpus for the IFC frontend (roadmap gate #1161).

Real IFCs are messy. This corpus pins a per-case outcome for the classes
the gate issue names: truncated files, wrong schema declarations, missing
required entities, and degenerate geometry. Two outcomes exist:

* coded refusal: ``IfcImportError`` with a stable machine-readable code
  and a remediation line (never a bare traceback), and
* degrade-with-warning: the file parses, the omission is announced
  (``warnings.warn``) and never silent.

CI runs this corpus on every PR, so behavior cannot drift silently. The
code strings are a frozen contract (``ifc_errors.IFC_ERROR_CODES``); the
taxonomy and the IFC2x3 vs IFC4 matrix are documented in
``docs/ifc_robustness.md``.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ifc_errors import IFC_ERROR_CODES, IfcImportError
from ifc_import import _ensure_ifc, import_ifc
from run_pipeline import StageError

_ensure_ifc()


def _write_valid_ifc(schema: str = "IFC4") -> str:
    """Build a minimal but complete IFC file (project -> site -> building -> storey + units)."""
    import ifcopenshell

    f = ifcopenshell.file(schema=schema)
    proj = f.createIfcProject(Name="Proj")
    site = f.createIfcSite(Name="Site")
    bldg = f.createIfcBuilding(Name="Bldg")
    storey = f.createIfcBuildingStorey(Name="L1", Elevation=0.0)
    f.createIfcRelAggregates(RelatingObject=proj, RelatedObjects=[site])
    f.createIfcRelAggregates(RelatingObject=site, RelatedObjects=[bldg])
    f.createIfcRelAggregates(RelatingObject=bldg, RelatedObjects=[storey])
    ua = f.createIfcUnitAssignment(Units=[f.createIfcSIUnit(Name="METRE", UnitType="LENGTHUNIT")])
    f.add(ua)
    path = Path(tempfile.mktemp(suffix=".ifc"))
    f.write(str(path))
    text = path.read_text()
    path.unlink()
    return text


def _corpus_file(name: str, text: str) -> Path:
    path = Path(tempfile.mktemp(suffix=f"_{name}.ifc"))
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# truncated files: refuse, coded (ifcopenshell would silently import a
# partial building -- the exact failure class #417 exists for)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fraction", [0.5, 0.9, 0.99])
def test_corpus_truncated_file_refuses_with_code(fraction):
    step = _write_valid_ifc()
    path = _corpus_file(f"truncated_{fraction}", step[: int(len(step) * fraction)])
    try:
        with pytest.raises(IfcImportError) as excinfo:
            import_ifc(path)
        assert excinfo.value.code == "ifc.truncated_file"
        assert excinfo.value.remediation
    finally:
        path.unlink()


def test_corpus_truncated_file_is_stage_error_and_value_error():
    """Coded refusals keep both legacy contracts: StageError handling and ValueError."""
    step = _write_valid_ifc()
    path = _corpus_file("truncated_half", step[: len(step) // 2])
    try:
        with pytest.raises(IfcImportError) as excinfo:
            import_ifc(path)
        assert isinstance(excinfo.value, StageError)
        assert isinstance(excinfo.value, ValueError)
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# empty / non-STEP payloads
# ---------------------------------------------------------------------------


def test_corpus_empty_file_refuses():
    path = _corpus_file("empty", "")
    try:
        with pytest.raises(IfcImportError) as excinfo:
            import_ifc(path)
        assert excinfo.value.code == "ifc.unreadable_file"
    finally:
        path.unlink()


def test_corpus_non_step_garbage_refuses():
    path = _corpus_file("garbage", "\x00\x01\x02not an ifc at all")
    try:
        with pytest.raises(IfcImportError) as excinfo:
            import_ifc(path)
        assert excinfo.value.code == "ifc.invalid_header"
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# wrong schema declarations
# ---------------------------------------------------------------------------


def test_corpus_unsupported_schema_variant_refuses():
    """A declared variant outside {IFC4, IFC2X3} is refused, never guessed."""
    step = _write_valid_ifc().replace("FILE_SCHEMA(('IFC4'));", "FILE_SCHEMA(('IFC2X3_TC1'));")
    assert "IFC2X3_TC1" in step
    path = _corpus_file("unsupported_schema", step)
    try:
        with pytest.raises(IfcImportError) as excinfo:
            import_ifc(path)
        assert excinfo.value.code == "ifc.unsupported_schema"
        assert "IFC2X3_TC1" in str(excinfo.value)
    finally:
        path.unlink()


def test_corpus_mislabeled_schema_declaration_degrades_with_schema_recorded():
    """Declared schema is trusted: an IFC4 body labeled IFC2X3 imports through the
    IFC2X3 path and records the source schema, never guessing a correction."""
    step = _write_valid_ifc().replace("FILE_SCHEMA(('IFC4'));", "FILE_SCHEMA(('IFC2X3'));")
    path = _corpus_file("schema_lie", step)
    try:
        model = import_ifc(path)
        assert model.source_schema.startswith("IFC2X3")
        assert len(model.levels) == 1
    finally:
        path.unlink()


def test_corpus_valid_ifc2x3_parses_with_schema_note():
    """A real IFC2X3 file upgrades in memory (#929) and imports; the source schema is recorded."""
    path = _corpus_file("valid_2x3", _write_valid_ifc(schema="IFC2X3"))
    try:
        model = import_ifc(path)
        assert model.source_schema == "IFC2X3"
        assert len(model.levels) == 1
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# missing required entities
# ---------------------------------------------------------------------------


def test_corpus_missing_ifcproject_degrades_with_warning():
    step = _write_valid_ifc()
    project_line = next(l for l in step.splitlines() if "IFCPROJECT" in l and "REL" not in l)
    path = _corpus_file("no_project", step.replace(project_line + "\n", ""))
    try:
        with pytest.warns(UserWarning, match="no IfcProject"):
            model = import_ifc(path)
        assert model.name == path.stem  # fallback name, not a crash
        assert len(model.levels) == 1
    finally:
        path.unlink()


def test_corpus_missing_unit_assignment_refuses():
    step = _write_valid_ifc().replace("IFCUNITASSIGNMENT", "IFCUNITASSIGNMENTQ")
    path = _corpus_file("no_units", step)
    try:
        with pytest.raises(IfcImportError) as excinfo:
            import_ifc(path)
        assert excinfo.value.code == "ifc.missing_units"
        assert "IfcUnitAssignment" in str(excinfo.value)
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# resource limits and workspace guard (coded, and still ValueError-compatible)
# ---------------------------------------------------------------------------


def test_corpus_size_limit_is_coded(tmp_path, monkeypatch):
    step = _write_valid_ifc()
    path = tmp_path / "big.ifc"
    path.write_text(step)
    monkeypatch.setenv("MATCHLINE_MAX_IFC_SIZE_MB", "0")
    with pytest.raises(IfcImportError) as excinfo:
        import_ifc(path)
    assert excinfo.value.code == "ifc.file_too_large"
    assert isinstance(excinfo.value, ValueError)


def test_corpus_element_limit_is_coded(tmp_path, monkeypatch):
    step = _write_valid_ifc()
    path = tmp_path / "many.ifc"
    path.write_text(step)
    monkeypatch.setenv("MATCHLINE_MAX_IFC_ELEMENTS", "1")
    with pytest.raises(IfcImportError) as excinfo:
        import_ifc(path)
    assert excinfo.value.code == "ifc.element_limit_exceeded"


def test_corpus_path_outside_workspace_is_coded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(IfcImportError) as excinfo:
        import_ifc("../escape.ifc")
    assert excinfo.value.code == "ifc.path_outside_workspace"


# ---------------------------------------------------------------------------
# taxonomy contract: the code set itself is frozen
# ---------------------------------------------------------------------------


def test_taxonomy_code_set_is_pinned():
    """The corpus pins the full code set: adding is a change, removing breaks the corpus."""
    assert set(IFC_ERROR_CODES) == {
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
    }


def test_no_bare_traceback_on_bad_input():
    """Bad input exits 1 with the code and remediation, never a Python traceback."""
    path = _corpus_file("garbage_cli", "not an ifc")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = Path(tempfile.mkdtemp())
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(Path(repo) / "run_pipeline.py"),
                "--ifc",
                str(path),
                "--out-dir",
                str(out_dir),
            ],
            capture_output=True,
            text=True,
            cwd=repo,
            timeout=300,
        )
    finally:
        path.unlink()
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "ifc.invalid_header" in proc.stderr
    assert "Verify the file is an IFC STEP" in proc.stderr  # the remediation line
