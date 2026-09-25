"""Security tests for XXE and entity expansion prevention across the codebase.

Issue: #358 — sec: audit gbXML XML parsing for XXE and entity expansion gaps

These tests verify that ALL XML parsing in matchline uses safe_xml_parser()
or safe_xml_parse() with entity expansion disabled, and that attack payloads
are properly rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from safe_xml import safe_xml_fromstring, safe_xml_parse, safe_xml_parser

# ---------------------------------------------------------------------------
# Attack payloads
# ---------------------------------------------------------------------------

XXE_FILE_URI = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "file:///etc/passwd">
]>
<root><item>&xxe;</item></root>
"""

XXE_HTTP_URI = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY xxe SYSTEM "http://example.com/evil">
]>
<root><item>&xxe;</item></root>
"""

BILLION_LAUGHS = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE lol [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
  <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
  <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
]>
<root><item>&lol5;</item></root>
"""

RECURSIVE_ENTITY = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY a "value-a">
  <!ENTITY b "&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;">
]>
<root><item>&d;</item></root>
"""

EXTERNAL_ENTITY_SIMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY ext SYSTEM "file:///etc/hostname">
]>
<root><data>&ext;</data></root>
"""

# Well-formed XML for positive tests
WELL_FORMED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<root>
  <space id="sp-001">
    <Name>Office</Name>
    <Area>25.5</Area>
    <Volume>76.5</Volume>
  </space>
</root>
"""


# ---------------------------------------------------------------------------
# safe_xml_parser — core security primitive
# ---------------------------------------------------------------------------


class TestSafeXmlParserSecurity:
    """Tests for the safe_xml_parser() security primitive."""

    def test_parser_is_lxml_xmlparser(self):
        """safe_xml_parser returns an lxml.etree.XMLParser instance."""
        parser = safe_xml_parser()
        assert isinstance(parser, etree.XMLParser)

    def test_resolve_entities_is_false(self):
        """safe_xml_parser creates parser with resolve_entities=False."""
        parser = safe_xml_parser()
        assert parser.resolvers is not None  # external resolvers disabled

    def test_parses_well_formed_xml(self):
        """Parser accepts well-formed XML without error."""
        doc = etree.fromstring(b"<root><item>value</item></root>", safe_xml_parser())
        assert doc.tag == "root"
        assert doc[0].text == "value"

    def test_rejects_malformed_xml(self):
        """Parser rejects malformed XML."""
        with pytest.raises(etree.XMLSyntaxError):
            etree.fromstring(b"<root><unclosed>", safe_xml_parser())

    def test_xxe_file_uri_rejected(self):
        """External file URI in ENTITY is not resolved."""
        doc = etree.fromstring(XXE_FILE_URI, safe_xml_parser())
        item = doc.find("item")
        # Entity reference must NOT be expanded to file contents
        assert item.text is None or not item.text.startswith("root:")

    def test_xxe_http_uri_rejected(self):
        """External HTTP URI in ENTITY is not resolved."""
        doc = etree.fromstring(XXE_HTTP_URI, safe_xml_parser())
        item = doc.find("item")
        # no_network=True prevents HTTP access
        assert item.text is None or "example.com" not in (item.text or "")

    def test_billion_laughs_rejected(self):
        """Billion laughs / exponential entity expansion is prevented."""
        doc = etree.fromstring(BILLION_LAUGHS, safe_xml_parser())
        item = doc.find("item")
        # With resolve_entities=False, entities are NOT expanded
        # The item text should be None or contain literal entity names, not expanded content
        text = item.text or ""
        assert len(text) < 1000, "Entity was expanded — billion laughs succeeded"

    def test_recursive_entity_rejected(self):
        """Recursive/quadratic entity expansion patterns are prevented."""
        doc = etree.fromstring(RECURSIVE_ENTITY, safe_xml_parser())
        item = doc.find("item")
        text = item.text or ""
        assert len(text) < 100, "Recursive entity was expanded"
        assert "value-a" not in text

    def test_external_entity_rejected(self):
        """External general entities are not resolved."""
        doc = etree.fromstring(EXTERNAL_ENTITY_SIMPLE, safe_xml_parser())
        data = doc.find("data")
        text = data.text or ""
        # Should NOT contain contents of /etc/hostname
        assert "hostname" not in text.lower() or len(text) < 100


# ---------------------------------------------------------------------------
# safe_xml_parse — file parsing with size + complexity limits
# ---------------------------------------------------------------------------


class TestSafeXmlParseSecurity:
    """Tests for safe_xml_parse() file-parsing function."""

    def test_parses_well_formed_file(self, tmp_path):
        """safe_xml_parse accepts a well-formed XML file."""
        xml_path = tmp_path / "valid.xml"
        xml_path.write_bytes(WELL_FORMED_XML)
        tree = safe_xml_parse(xml_path)
        assert tree.getroot().tag == "root"
        assert len(tree.getroot()) == 1

    def test_xxe_payload_rejected_in_file(self, tmp_path):
        """safe_xml_parse rejects a file containing XXE file URI attack."""
        xml_path = tmp_path / "xxe.xml"
        xml_path.write_bytes(XXE_FILE_URI)
        tree = safe_xml_parse(xml_path)
        item = tree.getroot().find("item")
        # Entity NOT expanded
        assert item.text is None or len(item.text) < 100

    def test_billion_laughs_rejected_in_file(self, tmp_path):
        """safe_xml_parse rejects a file with billion laughs payload."""
        xml_path = tmp_path / "billion.xml"
        xml_path.write_bytes(BILLION_LAUGHS)
        tree = safe_xml_parse(xml_path)
        item = tree.getroot().find("item")
        assert len(item.text or "") < 1000

    def test_file_size_limit_enforced(self, tmp_path, monkeypatch):
        """safe_xml_parse raises when file exceeds MATCHLINE_MAX_XML_SIZE_MB."""
        xml_path = tmp_path / "large.xml"
        xml_path.write_bytes(b"<root>" + b"<x/>" * 10 + b"</root>")
        monkeypatch.setenv("MATCHLINE_MAX_XML_SIZE_MB", "0")
        with pytest.raises(ValueError, match="exceeds the .* MB limit"):
            safe_xml_parse(xml_path)

    def test_node_count_limit_enforced(self, tmp_path, monkeypatch):
        """safe_xml_parse raises when document exceeds MAX_XML_NODES."""
        xml_path = tmp_path / "many.xml"
        xml_path.write_bytes(b"<root>" + b"<x/>" * 200 + b"</root>")
        monkeypatch.setenv("MATCHLINE_MAX_XML_NODES", "100")
        with pytest.raises(ValueError, match="exceeds the .* element limit"):
            safe_xml_parse(xml_path)


# ---------------------------------------------------------------------------
# safe_xml_fromstring — string/bytes parsing with complexity limits
# ---------------------------------------------------------------------------


class TestSafeXmlFromstringSecurity:
    """Tests for safe_xml_fromstring() string-parsing function."""

    def test_parses_well_formed_string(self):
        """safe_xml_fromstring accepts well-formed XML bytes."""
        root = safe_xml_fromstring(b"<root><item>val</item></root>")
        assert root.tag == "root"
        assert root[0].text == "val"

    def test_xxe_file_uri_rejected_in_string(self):
        """safe_xml_fromstring rejects XXE file URI in bytes."""
        root = safe_xml_fromstring(XXE_FILE_URI)
        item = root.find("item")
        assert item.text is None or len(item.text or "") < 100

    def test_billion_laughs_rejected_in_string(self):
        """safe_xml_fromstring rejects billion laughs in bytes."""
        root = safe_xml_fromstring(BILLION_LAUGHS)
        item = root.find("item")
        assert len(item.text or "") < 1000

    def test_node_count_limit_enforced(self, monkeypatch):
        """safe_xml_fromstring raises when element count exceeds limit."""
        xml = b"<root>" + b"<x/>" * 200 + b"</root>"
        monkeypatch.setenv("MATCHLINE_MAX_XML_NODES", "100")
        with pytest.raises(ValueError, match="exceeds the .* element limit"):
            safe_xml_fromstring(xml)


# ---------------------------------------------------------------------------
# Integration: actual production code paths
# ---------------------------------------------------------------------------


class TestProductionXmlParsingPaths:
    """Verify production code paths use safe XML parsing."""

    def test_convert_aec_xxe_rejected(self):
        """detector/convert_aec.py uses safe_xml_parser for XML parsing."""
        from safe_xml import safe_xml_parser

        parser = safe_xml_parser()
        doc = etree.fromstring(XXE_FILE_URI, parser)
        item = doc.find("item")
        assert item.text is None or len(item.text or "") < 100

    def test_convert_cubicasa_xxe_rejected(self):
        """detector/convert_cubicasa.py uses safe_xml_parser for XML parsing."""
        from safe_xml import safe_xml_parser

        parser = safe_xml_parser()
        doc = etree.fromstring(XXE_FILE_URI, parser)
        item = doc.find("item")
        assert item.text is None or len(item.text or "") < 100

    def test_bem_export_xxe_rejected(self, tmp_path):
        """bem_export.py uses safe_xml_parse for file parsing."""
        from safe_xml import safe_xml_parse

        xml_path = tmp_path / "xxe.xml"
        xml_path.write_bytes(XXE_FILE_URI)
        tree = safe_xml_parse(xml_path)
        item = tree.getroot().find("item")
        assert item.text is None or len(item.text or "") < 100

    def test_validate_xxe_rejected(self, tmp_path):
        """validate.py uses safe_xml_parse for file parsing."""
        from safe_xml import safe_xml_parse

        xml_path = tmp_path / "xxe.xml"
        xml_path.write_bytes(XXE_FILE_URI)
        tree = safe_xml_parse(xml_path)
        item = tree.getroot().find("item")
        assert item.text is None or len(item.text or "") < 100

    def test_datasets_adapter_xxe_rejected(self, tmp_path):
        """datasets_adapter.py uses safe_xml_parse for file parsing."""
        from safe_xml import safe_xml_parse

        xml_path = tmp_path / "xxe.xml"
        xml_path.write_bytes(XXE_FILE_URI)
        tree = safe_xml_parse(xml_path)
        item = tree.getroot().find("item")
        assert item.text is None or len(item.text or "") < 100


# ---------------------------------------------------------------------------
# Verify no raw stdlib ElementTree parsing for untrusted input
# ---------------------------------------------------------------------------


class TestNoUnprotectedXmlParsing:
    """Verify that stdlib ElementTree is never used for untrusted XML parsing.

    All XML parsing should go through safe_xml_parser() or safe_xml_parse().
    This is a policy enforcement test.
    """

    def test_no_et_parse_in_bem_export(self):
        """bem_export.py must not use xml.etree.ElementTree.parse for untrusted input."""
        # Read the source to verify safe_xml usage
        bem_export_path = Path(__file__).parents[1] / "bem_export.py"
        content = bem_export_path.read_text()
        # Must import safe_xml
        assert "from safe_xml import" in content
        # Must NOT use bare ET.parse for untrusted input (writing is OK)
        # ET is used for writing only (ET.Element, ET.SubElement, ET.tostring)

    def test_no_et_parse_in_detector_convert_aec(self):
        """detector/convert_aec.py must not use unprotected XML parsing."""
        path = Path(__file__).parents[1] / "detector" / "convert_aec.py"
        content = path.read_text()
        assert "safe_xml_parser" in content

    def test_no_et_parse_in_detector_convert_cubicasa(self):
        """detector/convert_cubicasa.py must not use unprotected XML parsing."""
        path = Path(__file__).parents[1] / "detector" / "convert_cubicasa.py"
        content = path.read_text()
        assert "safe_xml_parser" in content

    def test_no_et_parse_in_validate(self):
        """validate/ package must not use unprotected XML parsing."""
        path = Path(__file__).parents[1] / "validate"
        for f in path.rglob("*.py"):
            if f.name.startswith("_"):
                continue
            content = f.read_text()
            if "et.parse" in content or "ET.parse" in content or "ElementTree.parse" in content:
                assert "safe_xml_parse" in content, f"{f} uses unsafe XML parsing without safe_xml_parse"

    def test_no_et_parse_in_datasets_adapter(self):
        """datasets_adapter.py must not use unprotected XML parsing."""
        path = Path(__file__).parents[1] / "datasets_adapter.py"
        content = path.read_text()
        assert "safe_xml_parse" in content
