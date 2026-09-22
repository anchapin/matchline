"""Defect-injection tests for IFC import robustness.

Tests that import_ifc raises or handles gracefully when the IFC file is
corrupted, malformed, or contains degenerate geometry.
"""

import tempfile
from pathlib import Path

import pytest

from ifc_import import _ensure_ifc, import_ifc

_ensure_ifc()
import ifcopenshell  # noqa: E402


def _write_ifc(step: str) -> Path:
    """Write a minimal IFC4 file from an IFCSTEP string."""
    path = Path(tempfile.mktemp(suffix=".ifc"))
    path.write_text(step)
    return path


# ---------------------------------------------------------------------------
# schema violations
# ---------------------------------------------------------------------------


def test_wrong_schema_ifc2x3_raises():
    """IFC2X3 file raises ValueError (IFC4 required)."""
    ifc = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('ViewDefinition []'),'2;1');\n"
        "FILE_NAME('test.ifc','2024-01-01T00:00:00',(' '),(' '),'IFC2X3','EXPRESS',' ');\n"
        "FILE_SCHEMA(('IFC2X3'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
    path = _write_ifc(ifc)
    with pytest.raises(ValueError, match="IFC4"):
        import_ifc(path)


def test_unsupported_schema_raises():
    """Unsupported schema variant raises Exception from ifcopenshell."""
    ifc = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('ViewDefinition []'),'2;1');\n"
        "FILE_NAME('test.ifc','2024-01-01T00:00:00',(' '),(' '),'IFC2X3_TC1','EXPRESS',' ');\n"
        "FILE_SCHEMA(('IFC2X3_TC1'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
    path = _write_ifc(ifc)
    with pytest.raises(Exception, match="IFC2X3_TC1|Unsupported schema"):
        import_ifc(path)


# ---------------------------------------------------------------------------
# empty / minimal files
# ---------------------------------------------------------------------------


def test_empty_file_raises():
    """Empty file raises an exception from ifcopenshell."""
    path = _write_ifc("")
    with pytest.raises(Exception):
        import_ifc(path)


# ---------------------------------------------------------------------------
# degenerate geometry — zero-length wall does not crash the importer
# ---------------------------------------------------------------------------


def test_zero_length_wall_does_not_crash():
    """A wall with no actionable geometry does not produce an unhandled exception."""
    ifc = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('ViewDefinition []'),'2;1');\n"
        "FILE_NAME('test.ifc','2024-01-01T00:00:00',(' '),(' '),'IFC4','IFC4',' ');\n"
        "FILE_SCHEMA(('IFC4'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
        "#2=IFCSIUNIT(*,AREAUNIT,,.SQUARE_METRE.);\n"
        "#3=IFCSIUNIT(*,VOLUMEUNIT,,.CUBIC_METRE.);\n"
        "#4=IFCUNITASSIGNMENT((#1,#2,#3));\n"
        "#5=IFCPERSON($,$,$,$,$,$,$,$);\n"
        "#6=IFCORGANIZATION($,'org',$,$,$);\n"
        "#7=IFCAPPLICATION(#6,'1.0','app','');\n"
        "#8=IFCOWNERHISTORY(#5,#6,$,.ADDED.,$,#5,#6,0);\n"
        "#10=IFCAXIS2PLACEMENT3D(#11,$,$,$);\n"
        "#11=IFCCARTESIANPOINT((0.,0.,0.));\n"
        "#12=IFCLOCALPLACEMENT($,#10);\n"
        "#13=IFCBUILDING('TestBldg',#8,'Test',$,$,#12,$,$,$,$,$,$,$);\n"
        "#14=IFCBUILDINGSTOREY(#8,'L1',$,'Storey',$,#12,$,$,.F.);\n"
        "#15=IFCRELAGGREGATES(#8,'rel','agg',$,#13,(#14));\n"
        "#16=IFCWALL(#8,'ZeroWall',$,'ZeroLen',$,#12,$,$);\n"
        "#17=IFCRELCONTAINEDINSPATIALSTRUCTURE(#8,'rc',$,$,(#16),#14);\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
    path = _write_ifc(ifc)
    model = import_ifc(path)
    walls = [e for e in model.bim_elements if e.ifc_class == "IfcWall"]
    assert len(walls) == 1


# ---------------------------------------------------------------------------
# unknown entity in STEP data — ifcopenshell skips it silently
# ---------------------------------------------------------------------------


def test_unknown_entity_is_skipped():
    """A file containing an unknown entity type does not crash import."""
    ifc = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('ViewDefinition []'),'2;1');\n"
        "FILE_NAME('test.ifc','2024-01-01T00:00:00',(' '),(' '),'IFC4','IFC4',' ');\n"
        "FILE_SCHEMA(('IFC4'));\n"
        "ENDSEC;\n"
        "DATA;\n"
        "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
        "#2=IFCSIUNIT(*,AREAUNIT,,.SQUARE_METRE.);\n"
        "#3=IFCSIUNIT(*,VOLUMEUNIT,,.CUBIC_METRE.);\n"
        "#4=IFCUNITASSIGNMENT((#1,#2,#3));\n"
        "#5=IFCPERSON($,$,$,$,$,$,$,$);\n"
        "#6=IFCORGANIZATION($,'org',$,$,$);\n"
        "#7=IFCAPPLICATION(#6,'1.0','app','');\n"
        "#8=IFCOWNERHISTORY(#5,#6,$,.ADDED.,$,#5,#6,0);\n"
        "#10=IFCAXIS2PLACEMENT3D(#11,$,$,$);\n"
        "#11=IFCCARTESIANPOINT((0.,0.,0.));\n"
        "#12=IFCLOCALPLACEMENT($,#10);\n"
        "#13=IFCBUILDING('TestBldg',#8,'Test',$,$,#12,$,$,$,$,$,$,$);\n"
        "#14=IFCBUILDINGSTOREY(#8,'L1',$,'Storey',$,#12,$,$,.F.);\n"
        "#15=IFCRELAGGREGATES(#8,'rel','agg',$,#13,(#14));\n"
        "#16=IFCBUILDINGELEMENT('UnknownType',#8,'Unknown',$,'Unknown');\n"
        "#17=IFCRELCONTAINEDINSPATIALSTRUCTURE(#8,'rc',$,$,(#16),#14);\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
    path = _write_ifc(ifc)
    model = import_ifc(path)
    assert model is not None


# ---------------------------------------------------------------------------
# wrong file type (text masquerading as IFC)
# ---------------------------------------------------------------------------


def test_text_file_as_ifc_raises():
    """A plain text file raises when parsed as IFC."""
    path = _write_ifc("this is not an IFC file\njust some plain text\n")
    with pytest.raises(Exception):
        import_ifc(path)
