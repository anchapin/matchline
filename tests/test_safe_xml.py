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


def test_safe_xml_parser_resolve_entities_false():
    """Parser has resolve_entities=False to prevent entity expansion."""
    p = _make_parser()
    assert p.resolve_entities is False


def test_safe_xml_parser_no_network_true():
    """Parser has no_network=True to block network access."""
    p = _make_parser()
    assert p.no_network is True


def test_safe_xml_parser_parses_well_formed_xml():
    """Parser accepts well-formed XML without error."""
    xml = b"<root><item>value</item></root>"
    doc = etree.fromstring(xml, _make_parser())
    assert doc.tag == "root"
    assert doc[0].text == "value"


def test_safe_xml_parser_rejects_doctype_with_external_entity():
    """Parser rejects DOCTYPE declaring external entities (XXE prevention)."""
    xxe_xml = b"""<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<root><item>&xxe;</item></root>"""
    with pytest.raises(etree.XMLSyntaxError):
        etree.fromstring(xxe_xml, _make_parser())


def test_safe_xml_parser_rejects_entity_expansion_bomb():
    """Parser rejects deeply nested entity expansion bombs."""
    bomb = b"""<?xml version="1.0"?>
<!DOCTYPE foo [
  <!ENTITY x "REPEAT">
  <!ENTITY y "&x;&x;&x;&x;&x;&x;&x;&x;">
]>
<root><item>&y;</item></root>"""
    with pytest.raises(etree.XMLSyntaxError):
        etree.fromstring(bomb, _make_parser())


def test_safe_xml_parser_roundtrip_valid_document():
    """A valid XML document round-trips cleanly through the safe parser."""
    doc = etree.parse(
        BytesIO(
            b"<?xml version=\"1.0\"?><root><space id='sp-001'><name>Office</name></space></root>"
        ),
        _make_parser(),
    )
    assert doc.getroot().tag == "root"
    assert doc.getroot()[0].attrib["id"] == "sp-001"
