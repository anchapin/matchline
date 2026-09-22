"""Safe XML parsing utilities for untrusted input.

Per AGENTS.md untrusted-input policy: all XML from external gbXML/IFC/dataset
files must be parsed with entity expansion disabled to prevent XXE attacks.
"""

from __future__ import annotations

import lxml.etree


def safe_xml_parser() -> lxml.etree.XMLParser:
    """Return an lxml XMLParser hardened against entity-expansion/XXE.

    resolve_entities=False  - prevents expansion of external entities
    no_network=True        - blocks network access during parsing
    """
    return lxml.etree.XMLParser(resolve_entities=False, no_network=True)
