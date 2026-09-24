"""Tests for detector.ifc — IFC entity type tracking in _read_ifc_line_data."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from detector.ifc import (
    IFCParseResult,
    UnrecognizedIFCEntityError,
    _read_ifc_line_data,
)


class TestReadIfcLineData:
    def _write_ifc(self, step: str) -> Path:
        path = Path(tempfile.mktemp(suffix=".ifc"))
        path.write_text(step)
        return path

    def _minimal_ifc_header(self) -> str:
        return (
            "ISO-10303-21;\n"
            "HEADER;\n"
            "FILE_DESCRIPTION(('ViewDefinition []'),'2;1');\n"
            "FILE_NAME('test.ifc','2024-01-01T00:00:00',(' '),(' '),'IFC4','IFC4',' ');\n"
            "FILE_SCHEMA(('IFC4'));\n"
            "ENDSEC;\n"
            "DATA;\n"
        )

    def _minimal_ifc_footer(self) -> str:
        return "ENDSEC;\nEND-ISO-10303-21;\n"

    def test_known_entities_are_not_skipped(self):
        """Known IFC4 entity types are not added to skipped_lines."""
        ifc = (
            self._minimal_ifc_header()
            + "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
            + "#2=IFCSIUNIT(*,AREAUNIT,,.SQUARE_METRE.);\n"
            + "#3=IFCPERSON($,$,$,$,$,$,$,$);\n"
            + self._minimal_ifc_footer()
        )
        result = _read_ifc_line_data(ifc)
        assert result.skipped_lines == []
        assert "IFCSIUNIT" in result.known_entities
        assert "IFCPERSON" in result.known_entities

    def test_unknown_entity_is_skipped(self):
        """An unrecognized entity type is added to skipped_lines (normal mode)."""
        ifc = (
            self._minimal_ifc_header()
            + "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
            + "#2=IFCBUILDINGELEMENT('UnknownType',$,'Unknown',$,'Unknown');\n"
            + "#3=IFCPERSON($,$,$,$,$,$,$,$);\n"
            + self._minimal_ifc_footer()
        )
        result = _read_ifc_line_data(ifc)
        assert len(result.skipped_lines) == 1
        line_no, line_text = result.skipped_lines[0]
        assert line_no == 9
        assert "IFCBUILDINGELEMENT" in line_text
        assert "IFCSIUNIT" in result.known_entities
        assert "IFCPERSON" in result.known_entities

    def test_unknown_entity_strict_mode_raises(self):
        """In strict mode, an unrecognized entity type raises UnrecognizedIFCEntityError."""
        ifc = (
            self._minimal_ifc_header()
            + "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
            + "#2=IFCBUILDINGELEMENT('UnknownType',$,'Unknown',$,'Unknown');\n"
            + self._minimal_ifc_footer()
        )
        with pytest.raises(UnrecognizedIFCEntityError) as exc_info:
            _read_ifc_line_data(ifc, strict=True)
        assert exc_info.value.entity_type == "IFCBUILDINGELEMENT"
        assert exc_info.value.line_number == 9
        assert "IFCBUILDINGELEMENT" in exc_info.value.line_text

    def test_unknown_entity_strict_mode_raises_first(self):
        """In strict mode, the first unrecognized entity triggers the exception."""
        ifc = (
            self._minimal_ifc_header()
            + "#1=IFCBUILDINGELEMENT('U1',$,'U',$,'U');\n"
            + "#2=IFCBUILDINGELEMENT('U2',$,'U',$,'U');\n"
            + self._minimal_ifc_footer()
        )
        with pytest.raises(UnrecognizedIFCEntityError) as exc_info:
            _read_ifc_line_data(ifc, strict=True)
        assert exc_info.value.line_number == 8

    def test_warning_is_logged_when_unknown_entities_skipped(
        self, caplog: pytest.LogCaptureFixture
    ):
        """A warning is logged when unknown entities are skipped in normal mode."""
        ifc = (
            self._minimal_ifc_header()
            + "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
            + "#2=IFCBUILDINGELEMENT('U',$,'U',$,'U');\n"
            + self._minimal_ifc_footer()
        )
        with caplog.at_level(logging.WARNING):
            result = _read_ifc_line_data(ifc)
        assert result.skipped_lines != []
        assert any(
            "Skipped" in record.message and "unrecognized" in record.message.lower()
            for record in caplog.records
        )

    def test_no_warning_when_all_entities_known(self, caplog: pytest.LogCaptureFixture):
        """No warning is emitted when all entity types are recognized."""
        ifc = (
            self._minimal_ifc_header()
            + "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
            + "#2=IFCPERSON($,$,$,$,$,$,$,$);\n"
            + self._minimal_ifc_footer()
        )
        with caplog.at_level(logging.WARNING):
            result = _read_ifc_line_data(ifc)
        assert result.skipped_lines == []
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records == []

    def test_extra_known_entities_extends_recognition(self):
        """extra_known_entities allows project-specific entity types to be recognized."""
        ifc = (
            self._minimal_ifc_header()
            + "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
            + "#2=IFCMYPROJECTENTITY($,$,$);\n"
            + self._minimal_ifc_footer()
        )
        result = _read_ifc_line_data(ifc, extra_known_entities={"IFCMYPROJECTENTITY"})
        assert result.skipped_lines == []
        assert "IFCMYPROJECTENTITY" in result.known_entities

    def test_empty_data_section(self):
        """An empty DATA section returns an empty result with no warnings."""
        ifc = self._minimal_ifc_header() + self._minimal_ifc_footer()
        result = _read_ifc_line_data(ifc)
        assert result.skipped_lines == []
        assert result.total_lines == 0

    def test_total_lines_counts_data_section_only(self):
        """total_lines only counts lines inside the DATA section."""
        ifc = (
            "ISO-10303-21;\n"
            "HEADER;\n"
            "ENDSEC;\n"
            "DATA;\n"
            "#1=IFCSIUNIT(*,LENGTHUNIT,,.METRE.);\n"
            "ENDSEC;\n"
            "END-ISO-10303-21;\n"
        )
        result = _read_ifc_line_data(ifc)
        assert result.total_lines == 1

    def test_parse_result_dataclass_fields(self):
        """IFCParseResult exposes the expected fields."""
        result = IFCParseResult(
            skipped_lines=[(1, "#1=IFCFOO($,$)")],
            known_entities={"IFCFOO"},
            total_lines=1,
        )
        assert result.skipped_lines == [(1, "#1=IFCFOO($,$)")]
        assert result.known_entities == {"IFCFOO"}
        assert result.total_lines == 1
