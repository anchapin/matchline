"""Tests for safe_xml.py — XXE hardening utilities."""

from io import BytesIO

import pytest
from lxml import etree

from safe_xml import safe_xml_parser


def _make_parser():
    return safe_xml_parser()


def test_safe_xml_parser_returns_lxml_parser():
    """safe_xml_parser returns an lxml.etree.XMLParser instance."""
    p = _make_parser()
    assert isinstance(p, etree.XMLParser)


def test_safe_xml_parser_parses_well_formed_xml():
    """Parser accepts well-formed XML without error."""
    xml = b"<root><item>value</item></root>"
    doc = etree.fromstring(xml, _make_parser())
    assert doc.tag == "root"
    assert doc[0].text == "value"


def test_safe_xml_parser_parses_large_valid_document():
    """Parser handles a larger well-formed XML document."""
    doc = etree.parse(
        BytesIO(
            b"<?xml version='1.0'?><root>"
            b"<space id='sp-001'><name>Office</name></space>"
            b"<space id='sp-002'><name>Conference</name></space>"
            b"</root>"
        ),
        _make_parser(),
    )
    assert doc.getroot().tag == "root"
    assert len(doc.getroot()) == 2


def test_safe_xml_parser_rejects_malformed_xml():
    """Parser rejects malformed XML with XMLSyntaxError."""
    malformed = b"<root><unclosed>"
    with pytest.raises(etree.XMLSyntaxError):
        etree.fromstring(malformed, _make_parser())
