"""Tests for safe_xml.py — XXE hardening utilities."""

from io import BytesIO

import pytest
from lxml import etree

from safe_xml import safe_xml_fromstring, safe_xml_parse, safe_xml_parser


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


# ---------------------------------------------------------------------------
# XXE attack rejection tests
# ---------------------------------------------------------------------------

XXML_ENTITY_EXPANSION = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY x "THIS_IS_A_TEST">
  <!ENTITY y "&x;&x;&x;&x;&x;&x;&x;&x;">
]>
<root><item>&y;</item></root>
"""

XXML_RECURSIVE_ENTITY = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY a "value-a">
  <!ENTITY b "&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;">
]>
<root><item>&c;</item></root>
"""

XXML_BILLION_LAUGHS = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE lol [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<root><item>&lol3;</item></root>
"""


def test_xxe_entity_expansion_rejected():
    """Parser prevents entity expansion from DOCTYPE declarations."""
    doc = etree.fromstring(XXML_ENTITY_EXPANSION, _make_parser())
    # With resolve_entities=False, entity references are NOT expanded.
    # The text of <item> should be None or the entity name, not the expanded value.
    item = doc.find("item")
    # Entity not expanded means text is None or contains literal '&y;'
    assert item.text is None or "&y;" in item.text or "&x;" in item.text


def test_xxe_recursive_entity_rejected():
    """Parser prevents recursive entity expansion patterns."""
    doc = etree.fromstring(XXML_RECURSIVE_ENTITY, _make_parser())
    item = doc.find("item")
    # Entity references must not be resolved to their expanded text
    assert item.text is None or "&c;" in item.text or "&b;" in item.text or "&a;" in item.text


def test_xxe_billion_laughs_rejected():
    """Parser prevents the billion laughs attack (exponential entity expansion)."""
    doc = etree.fromstring(XXML_BILLION_LAUGHS, _make_parser())
    item = doc.find("item")
    # The entity lol3 would expand to a huge string; it must not be resolved
    assert item.text is None or "&lol3;" in item.text or "&lol2;" in item.text


# ---------------------------------------------------------------------------
# File size and complexity limit tests
# ---------------------------------------------------------------------------


def test_safe_xml_parse_rejects_oversized_file(tmp_path, monkeypatch):
    """safe_xml_parse raises ValueError when file exceeds MATCHLINE_MAX_XML_SIZE_MB."""
    small_xml = b"<root><item>value</item></root>"
    xml_path = tmp_path / "small.xml"
    xml_path.write_bytes(small_xml)

    monkeypatch.setenv("MATCHLINE_MAX_XML_SIZE_MB", "0")
    with pytest.raises(ValueError, match="exceeds the .* MB limit"):
        safe_xml_parse(xml_path)


def test_safe_xml_parse_accepts_sized_file(tmp_path, monkeypatch):
    """safe_xml_parse accepts file within size limit."""
    small_xml = b"<root><item>value</item></root>"
    xml_path = tmp_path / "small.xml"
    xml_path.write_bytes(small_xml)

    monkeypatch.setenv("MATCHLINE_MAX_XML_SIZE_MB", "100")
    tree = safe_xml_parse(xml_path)
    assert tree.getroot().tag == "root"


def test_safe_xml_parse_rejects_excessive_nodes(tmp_path, monkeypatch):
    """safe_xml_parse raises ValueError when document has too many elements."""
    # Generate an XML with many elements
    num_items = 200
    xml_content = b"<root>" + (b"<item id='i-%d'/>" % 0) * num_items + b"</root>"
    xml_path = tmp_path / "many_items.xml"
    xml_path.write_bytes(xml_content)

    monkeypatch.setenv("MATCHLINE_MAX_XML_NODES", "100")
    with pytest.raises(ValueError, match="exceeds the .* element limit"):
        safe_xml_parse(xml_path)


def test_safe_xml_parse_accepts_few_nodes(tmp_path, monkeypatch):
    """safe_xml_parse accepts document within node limit."""
    num_items = 10
    xml_content = b"<root>" + (b"<item id='i-%d'/>" % 0) * num_items + b"</root>"
    xml_path = tmp_path / "few_items.xml"
    xml_path.write_bytes(xml_content)

    monkeypatch.setenv("MATCHLINE_MAX_XML_NODES", "100")
    tree = safe_xml_parse(xml_path)
    assert len(list(tree.getroot())) == num_items


def test_safe_xml_fromstring_rejects_excessive_nodes(monkeypatch):
    """safe_xml_fromstring raises ValueError when document has too many elements."""
    num_items = 200
    xml_content = b"<root>" + (b"<item id='i-%d'/>" % 0) * num_items + b"</root>"

    monkeypatch.setenv("MATCHLINE_MAX_XML_NODES", "100")
    with pytest.raises(ValueError, match="exceeds the .* element limit"):
        safe_xml_fromstring(xml_content)
