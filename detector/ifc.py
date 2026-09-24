"""detector.ifc: IFC entity type validation and line-level parsing utilities.

Provides low-level STEP text parsing for IFC files, tracking unknown/unrecognized
entity types so they can be flagged rather than silently dropped.

Coordinate frame: IFC uses Z-up (y-down is the canonical model used elsewhere
in matchline). Provenance is attached to every extracted fact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class UnrecognizedIFCEntityError(Exception):
    """Raised when an unrecognized IFC entity type is encountered in strict mode.

    Attributes:
        entity_type: The entity type name that was not recognized.
        line_number: Line number in the source file where the entity appears.
        line_text: The raw line text of the unrecognized entity.
    """

    entity_type: str
    line_number: int
    line_text: str

    def __str__(self) -> str:
        return f"Unrecognized IFC entity '{self.entity_type}' at line {self.line_number}"


@dataclass
class IFCParseResult:
    """Result of parsing an IFC STEP text.

    Attributes:
        skipped_lines: List of (line_number, line_text) tuples for entities
            that could not be parsed or were unrecognized.
        known_entities: Set of entity type names that were successfully parsed.
        total_lines: Total number of data lines processed.
    """

    skipped_lines: list[tuple[int, str]] = field(default_factory=list)
    known_entities: set[str] = field(default_factory=set)
    total_lines: int = 0


# Known IFC4 entity types that we consider "recognized" for parsing purposes.
# This is a conservative allowlist for entity types that appear in valid IFC4
# files produced by common authoring tools.  Unknown entity types outside this
# list are tracked in skipped_lines.
_IFC4_KNOWN_ENTITIES: frozenset[str] = frozenset(
    {
        "IFCSIUNIT",
        "IFCUNITASSIGNMENT",
        "IFCPERSON",
        "IFCORGANIZATION",
        "IFCAPPLICATION",
        "IFCOWNERHISTORY",
        "IFCAXIS2PLACEMENT3D",
        "IFCCARTESIANPOINT",
        "IFCCARTESIANPOINTLIST2D",
        "IFCCARTESIANPOINTLIST3D",
        "IFCLOCALPLACEMENT",
        "IFCBUILDING",
        "IFCBUILDINGSTOREY",
        "IFCRELAGGREGATES",
        "IFCRELCONTAINEDINSPATIALSTRUCTURE",
        "IFCRELASSIGNS",
        "IFCRELASSIGNSTOGROUP",
        "IFCRELASSOCIATESMATERIAL",
        "IFCRELASSOCIATES",
        "IFCRELDEFINESBYPROPERTIES",
        "IFCRELDEFINESBYTYPE",
        "IFCRELVOIDSELEMENT",
        "IFCRELFILLSELEMENT",
        "IFCRELCOVERBLDGELEMENTS",
        "IFCRELSPACEBOUNDARY",
        "IFCRELBUILDINGELEMENTPARTTOCOMPLEXELEMENT",
        "IFCRELBUILDINGELEMENTPARTTOELEMENT",
        "IFCPROJECT",
        "IFCSITE",
        "IFCWALL",
        "IFCWALLSTANDARDCASE",
        "IFCSLAB",
        "IFCSLABSTANDARDELEMENT",
        "IFCROOF",
        "IFCCOLUMN",
        "IFCBEAM",
        "IFCCURTAINWALL",
        "IFCDOOR",
        "IFCWINDOW",
        "IFCOPENINGELEMENT",
        "IFCWINDOWSTANDARDCASE",
        "IFCDOORSTANDARDELEMENT",
        "IFCDUCTSEGMENT",
        "IFCDUCTFITTING",
        "IFCCABLESEGMENT",
        "IFCCABLEFITTING",
        "IFCPIPESEGMENT",
        "IFCPIPEFITTING",
        "IFCELEMENTASSEMBLY",
        "IFCFURNITURE",
        "IFCFURNISHINGELEMENT",
        "IFCBUILDINGELEMENTPROXY",
        "IFCMATERIAL",
        "IFCMATERIALLAYER",
        "IFCMATERIALLAYERSET",
        "IFCMATERIALLAYERSETUSAGE",
        "IFCMATERIALCONSTITUENTSET",
        "IFCMATERIALCONSTITUENT",
        "IFCCOLOURSPECIFICATION",
        "IFCSURFACESTYLE",
        "IFCPRESENTATIONSTYLEASSIGNMENT",
        "IFCSTYLEDITEM",
        "IFCSHAPE",
        "IFCREPRESENTATIONMAP",
        "IFCMAPPEDITEM",
        "IFCPILE",
        "IFCFUNDATION",
        "IFCRAILING",
        "IFCRAMP",
        "IFCRAMPGLOBALGLOBAL",
        "IFCSTAIR",
        "IFCSTAIRFLIGHT",
        "IFCPLANE",
        "IFCBOUNDINGBOX",
        "IFCEXTRUDEDAREASOLID",
        "IFCREVOLVEDAREASOLID",
        "IFCAPPROVAL",
        "IFCDOCUMENTINFORMATION",
        "IFCDOCUMENTREFERENCE",
        "IFCLABEL",
        "IFCTEXT",
        "IFCID",
        "IFCDATE",
        "IFCDATETIME",
        "IFCTIMESTAMP",
        "IFCCLASSIFICATION",
        "IFCCLASSIFICATIONREFERENCE",
        "IFCORGANIZING",
        "IFCOWNERINFORMATION",
        "IFCRELDECLARES",
        "IFCPOLYLINE",
        "IFCPOINTLIST",
    }
)

# Regex to match an IFC entity line: #id=IFCENTITYNAME(...)
_ENTITY_LINE_RE = re.compile(
    r"^\s*#(?P<id>\d+)\s*=\s*(?P<entity>[A-Z][A-Z0-9]*)\s*\("
)


def _read_ifc_line_data(
    text: str,
    strict: bool = False,
    extra_known_entities: Optional[set[str]] = None,
) -> IFCParseResult:
    """Parse IFC STEP text line by line, tracking unrecognized entity types.

    Reads the IFC STEP text and identifies entity type names on data lines
    (lines that start with ``#<id>=<EntityType>(``).  Entity types that are
    not in the known allowlist are added to ``skipped_lines``.

    Args:
        text: The full IFC STEP file contents as a string.
        strict: If True, raise ``UnrecognizedIFCEntityError`` on the first
            unrecognized entity type.  If False (default), log a warning
            when any skipped lines are present at the end of parsing.
        extra_known_entities: Optional set of additional entity type names to
            treat as known beyond the built-in allowlist.  Useful for
            extension schemas or project-specific entity types.

    Returns:
        IFCParseResult containing:
        - ``skipped_lines``: List of (line_number, line_text) for unrecognized
          or unparseable entities.
        - ``known_entities``: Set of entity type names that were recognized.
        - ``total_lines``: Total number of data lines processed.

    Raises:
        UnrecognizedIFCEntityError: Only when ``strict=True`` and an
            unrecognized entity type is encountered.

    Example:
        >>> result = _read_ifc_line_data(ifc_step_text)
        >>> if result.skipped_lines:
        ...     logger.warning("Skipped %d unrecognized entities", len(result.skipped_lines))
    """
    known = set(_IFC4_KNOWN_ENTITIES)
    if extra_known_entities:
        known.update(extra_known_entities)

    skipped_lines: list[tuple[int, str]] = []
    known_entities: set[str] = set()
    total_lines = 0

    in_data = False
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped == "DATA;":
            in_data = True
            continue
        if stripped == "ENDSEC;":
            in_data = False
            continue
        if not in_data:
            continue

        total_lines += 1
        match = _ENTITY_LINE_RE.match(stripped)
        if match:
            entity_type = match.group("entity")
            if entity_type in known:
                known_entities.add(entity_type)
            else:
                skipped_lines.append((line_no, raw_line))
                if strict:
                    raise UnrecognizedIFCEntityError(
                        entity_type=entity_type,
                        line_number=line_no,
                        line_text=raw_line,
                    )

    if skipped_lines and not strict:
        logger.warning(
            "Skipped %d unrecognized IFC entity type(s) while parsing IFC text. "
            "First few skipped: %s",
            len(skipped_lines),
            skipped_lines[:3],
        )

    return IFCParseResult(
        skipped_lines=skipped_lines,
        known_entities=known_entities,
        total_lines=total_lines,
    )
