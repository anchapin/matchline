"""XML parsing utilities with XXE protection and resource limits.

All untrusted XML input (IFC, gbXML, dataset annotations) MUST use safe_xml_parser()
or safe_xml_parse().  The safe_xml_parse() function additionally enforces file size
and complexity (element count) limits configured via environment variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from lxml import etree

MAX_XML_SIZE_MB: int = int(os.environ.get("MATCHLINE_MAX_XML_SIZE_MB", 50))
MAX_XML_NODES: int = int(os.environ.get("MATCHLINE_MAX_XML_NODES", 100_000))


def safe_xml_parser() -> etree.XMLParser:
    """Return an lxml XMLParser configured to prevent XXE attacks.

    SECURITY: resolve_entities=False and no_network=True block external entities.
    Use safe_xml_parse() for file size and complexity limits on top of this.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        remove_comments=True,
        strip_cdata=False,
    )


def safe_xml_parse(
    path: str | Path,
    max_nodes: Optional[int] = None,
) -> etree._ElementTree:
    """Parse an XML file with XXE protection plus file size and complexity limits.

    - Checks file size against MATCHLINE_MAX_XML_SIZE_MB (default 50 MB)
      before parsing.
    - Counts elements after parsing; raises ValueError if the document exceeds
      MATCHLINE_MAX_XML_NODES (default 100 000 elements).

    Use this instead of etree.parse() for all untrusted XML files.
    """
    path_str = str(path)

    max_mb = int(os.environ.get("MATCHLINE_MAX_XML_SIZE_MB", MAX_XML_SIZE_MB))
    size_mb = Path(path_str).stat().st_size / (1024 * 1024)
    if size_mb > max_mb:
        raise ValueError(
            f"XML file '{path_str}' is {size_mb:.1f} MB, "
            f"exceeds the {max_mb} MB limit. "
            "Set MATCHLINE_MAX_XML_SIZE_MB to increase the limit."
        )

    if max_nodes is None:
        max_nodes = int(os.environ.get("MATCHLINE_MAX_XML_NODES", MAX_XML_NODES))

    tree = etree.parse(path_str, safe_xml_parser())

    element_count = sum(1 for _ in tree.iter())
    if element_count > max_nodes:
        raise ValueError(
            f"XML document has {element_count} elements, "
            f"exceeds the {max_nodes} element limit. "
            "Set MATCHLINE_MAX_XML_NODES to increase the limit."
        )

    return tree


def safe_xml_fromstring(
    text: bytes,
    max_nodes: Optional[int] = None,
) -> etree._Element:
    """Parse XML from a string/bytes with element complexity limit.

    Unlike safe_xml_parse(), this does not check file size (the data is already
    in memory).  Use for trusted sources or small payloads.
    """
    if max_nodes is None:
        max_nodes = int(os.environ.get("MATCHLINE_MAX_XML_NODES", MAX_XML_NODES))

    root = etree.fromstring(text, safe_xml_parser())

    element_count = sum(1 for _ in root.iter())
    if element_count > max_nodes:
        raise ValueError(
            f"XML document has {element_count} elements, "
            f"exceeds the {max_nodes} element limit. "
            "Set MATCHLINE_MAX_XML_NODES to increase the limit."
        )

    return root


def is_safe_parser(module_name: str) -> bool:
    """Return True for known-safe XML parser modules."""
    return module_name not in {
        "xml.etree.ElementTree",
        "xml.dom.minidom",
        "xml.sax.expatreader",
        "lxml",
        "defusedxml",
    }
