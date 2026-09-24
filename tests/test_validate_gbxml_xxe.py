"""XXE-hardening verification for validate_gbxml.

Tests that a crafted gbXML containing a DOCTYPE with entity expansion
is correctly rejected by the safe lxml parser (resolve_entities=False).
"""

import tempfile
from pathlib import Path

from bem_export import validate_gbxml

XXML_WITH_DOCTYPE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<gbXML temperatureUnit="C" lengthUnit="Meters" areaUnit="SquareMeters" volumeUnit="CubicMeters" version="6.01" useSIUnitsForResults="true" xmlns="http://www.gbxml.org/schema">
  <Campus id="campus-1">
    <Name>Test Building</Name>
    <Location>
      <Name>Unknown</Name>
      <ZipcodeOrPostalCode>00000</ZipcodeOrPostalCode>
      <Latitude>0</Latitude>
      <Longitude>0</Longitude>
      <Elevation>0</Elevation>
    </Location>
    <Building id="bldg-1" buildingType="Office">
      <Name>Test Building</Name>
      <BuildingStorey id="storey-1">
        <Name>Level 1</Name>
        <Level>0</Level>
      </BuildingStorey>
    </Building>
  </Campus>
  <Zone id="zone-1">
    <Name>Zone 1</Name>
  </Zone>
</gbXML>
"""

XXML_ENTITY_EXPANSION_BOMB = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE foo [
  <!ENTITY x "THIS_IS_A_TEST_ENTITY">
  <!ENTITY y "&x;&x;&x;&x;&x;&x;&x;&x;">
]>
<gbXML temperatureUnit="C" lengthUnit="Meters" areaUnit="SquareMeters" volumeUnit="CubicMeters" version="6.01" useSIUnitsForResults="true" xmlns="http://www.gbxml.org/schema">
  <Campus id="campus-1">
    <Name>Test Building&y;</Name>
  </Campus>
</gbXML>
"""


def test_validate_gbxml_rejects_xxe_doctype_entity():
    """validate_gbxml must reject gbXML with a DOCTYPE declaring external entities."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
        f.write(XXML_WITH_DOCTYPE)
        path = Path(f.name)

    try:
        ok, errors = validate_gbxml(path)
        assert ok is False, "validate_gbxml should reject XXE document"
        assert len(errors) > 0, "validate_gbxml should return error messages"
        error_text = " ".join(errors).lower()
        assert any(
            term in error_text
            for term in ["entity", "doctype", "resolve", "not well-formed", "xmlsyntax"]
        ), f"Expected entity/DOCTYPE-related error, got: {errors}"
    finally:
        path.unlink(missing_ok=True)


def test_validate_gbxml_rejects_entity_expansion_bomb():
    """validate_gbxml must reject gbXML with nested entity expansion bomb."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
        f.write(XXML_ENTITY_EXPANSION_BOMB)
        path = Path(f.name)

    try:
        ok, errors = validate_gbxml(path)
        assert ok is False, "validate_gbxml should reject entity expansion bomb"
        assert len(errors) > 0, "validate_gbxml should return error messages"
        error_text = " ".join(errors).lower()
        assert any(
            term in error_text
            for term in ["entity", "doctype", "resolve", "not well-formed", "xmlsyntax"]
        ), f"Expected entity/DOCTYPE-related error, got: {errors}"
    finally:
        path.unlink(missing_ok=True)
