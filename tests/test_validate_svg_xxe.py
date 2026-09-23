"""XXE-hardening verification for SVG parsing in convert_cubicasa.

Tests that a crafted SVG containing a DOCTYPE with entity expansion
is correctly rejected by the safe lxml parser (resolve_entities=False).
"""

import tempfile
from pathlib import Path

from detector.convert_cubicasa import convert_plan

XXE_SVG_DOCTYPE = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <rect class="room" x="10" y="10" width="80" height="80"/>
</svg>
"""

ENTITY_EXPANSION_BOMB_SVG = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE svg [
  <!ENTITY x "TEST">
  <!ENTITY y "&x;&x;&x;&x;&x;&x;&x;&x;">
]>
<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
  <rect class="room" x="10" y="10" width="80" height="80"/>
</svg>
"""


def test_convert_plan_rejects_xxe_doctype():
    """convert_plan must return None when parsing SVG with a DOCTYPE declaring external entities."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".svg", delete=False) as f:
        f.write(XXE_SVG_DOCTYPE)
        path = Path(f.name)

    try:
        result = convert_plan(path)
        assert result is None, (
            f"convert_plan should return None for XXE SVG document, got {result!r}"
        )
    finally:
        path.unlink(missing_ok=True)


def test_convert_plan_rejects_entity_expansion_bomb():
    """convert_plan must return None when parsing SVG with nested entity expansion bomb."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".svg", delete=False) as f:
        f.write(ENTITY_EXPANSION_BOMB_SVG)
        path = Path(f.name)

    try:
        result = convert_plan(path)
        assert result is None, (
            f"convert_plan should return None for entity-expansion-bomb SVG, got {result!r}"
        )
    finally:
        path.unlink(missing_ok=True)
