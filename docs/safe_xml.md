# safe_xml: hardened XML parser for XXE prevention

`safe_xml.py` provides `safe_xml_parser()`, a thin wrapper around `lxml.etree.XMLParser` configured to prevent XML External Entity (XXE) attacks.

## Background

XXE attacks exploit XML parsers that resolve external entities (file reads, network requests) during parsing. Architectural drawings and BIM files are often distributed as XML-based formats (IFC, gbXML), making parser hardening essential for safe processing.

## API

### `safe_xml_parser() -> lxml.etree.XMLParser`

Returns a configured `XMLParser` instance with entity resolution and network access disabled.

```python
from safe_xml import safe_xml_parser
from lxml import etree

parser = safe_xml_parser()
doc = etree.fromstring(xml_bytes, parser)
tree = etree.parse(file_handle, parser)
```

## What is protected

| Threat | Protection |
|--------|-----------|
| External entity expansion (`<!ENTITY xxe SYSTEM "file:///etc/passwd">`) | Entity resolution disabled |
| Document Type Declaration with external subsets | Rejected at parse time |
| Network access during parsing | Blocked (`no_network=True`) |

## Limitations

- **lxml version dependency**: Parser behavior may vary across lxml versions. Tested against lxml ≥ 4.9.
- **No runtime policy enforcement**: The safe parser only controls parse-time behavior. Malformed-but-valid XML after parsing is not caught.
- **No schema validation**: This module does not enforce any schema (DTD/XSD/RelaxNG). Combine with schema validation for defense-in-depth.
- **Python-only**: The hardening is specific to lxml's Python API; other XML libraries in the pipeline may have their own parsing paths.
- **XXE in non-XML formats**: This module does not protect against XXE vectors in other formats (e.g., SVG, Office documents, PDF).
