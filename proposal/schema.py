"""The proposal contract, version 1: one field table, two consumers.

This module is the single source of truth for the native building-proposal
document shape (``matchline.building-proposal``, version 1). Two consumers
read the tables below:

* ``proposal.validate`` checks a parsed document against them and reports
  machine codes, and
* ``proposal_json_schema`` (this module) builds the JSON Schema (draft
  2020-12) an agent can be handed, so the agent-facing contract cannot
  drift from the validator.

A ``Node`` describes one field of one object type:

``type``
    JSON type name: ``string``, ``number``, ``integer``, ``boolean``,
    ``object``, ``array`` or ``any``.
``fields``
    the members of an object type. A key that is not listed is an
    ``unknown-field`` error, except inside ``metadata`` and
    ``property_sets`` (``free_keys``).
``values``
    the value type of a map whose keys are free (``wallTypes``).
``item``
    the element type of an array.
``enum`` / ``enum_code``
    the allowed values of a field and the diagnostic code raised when a
    value is outside them (the header fields use their own codes:
    ``unsupported-version`` and ``unsupported-units``).
``min_items`` / ``min_items_code``
    the minimum length of an array and the code raised when it is short
    (a polygon with fewer than three points is ``bad-polygon``).
``minimum`` / ``exclusive_min`` / ``range_code``
    the numeric range of a field.
``one_of_required``
    groups of fields of which at least one must be present (a wall needs
    ``thickness_m`` or ``type_ref``).
``conditional_required``
    ``(field, value, required_field)`` triples: ``sill_m`` is required
    when ``kind`` is ``window``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

#: The only document schema this build accepts.
SCHEMA_ID = "matchline.building-proposal"
#: The only proposal version this build accepts.
PROPOSAL_VERSION = 1
#: The only unit system this build accepts. Anything else is refused, not converted.
UNITS = "meters"

#: The JSON Schema dialect the generated schema declares.
JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
#: The canonical identifier of the generated schema.
JSON_SCHEMA_ID = "https://matchline.build/schemas/building-proposal-1.json"


@dataclass(frozen=True)
class Node:
    """One field of one object type in the proposal contract."""

    name: str = ""
    type: str = "any"
    required: bool = False
    enum: Optional[Tuple[Any, ...]] = None
    enum_code: str = ""
    item: Optional["Node"] = None
    fields: Tuple["Node", ...] = ()
    values: Optional["Node"] = None
    free_keys: bool = False
    min_items: Optional[int] = None
    min_items_code: str = "wrong-type"
    minimum: Optional[float] = None
    exclusive_min: Optional[float] = None
    range_code: str = "wrong-type"
    conditional_required: Tuple[Tuple[str, Any, str], ...] = ()
    one_of_required: Tuple[Tuple[str, ...], ...] = ()
    def_name: str = ""
    description: str = ""


# --- leaves ----------------------------------------------------------------

#: A plan point: [x, y] in the canonical frame (metres, y growing downward).
POINT = Node(
    name="point",
    type="array",
    item=Node(type="number"),
    min_items=2,
    min_items_code="wrong-type",
    def_name="point",
    description="A plan point [x, y] in canonical metres (y grows downward).",
)

#: A closed outline: at least three points, simple, non-zero area.
POLYGON = Node(
    name="polygon",
    type="array",
    item=POINT,
    min_items=3,
    min_items_code="bad-polygon",
    def_name="polygon",
    description="A closed outline of at least three plan points.",
)

# --- wall types ------------------------------------------------------------

LAYER = Node(
    name="layer",
    type="object",
    fields=(
        Node("material", "string", required=True, description="Material name, free text."),
        Node(
            "thickness_m",
            "number",
            required=True,
            minimum=0.0,
            description="Layer thickness in metres (0 for a membrane).",
        ),
        Node(
            "function",
            "string",
            required=True,
            enum=("structure", "substrate", "thermal", "finish", "membrane"),
            description="What the layer does in the assembly.",
        ),
    ),
    def_name="layer",
    description="One layer of a wall type.",
)

WALL_TYPE = Node(
    name="wallType",
    type="object",
    fields=(
        Node("name", "string", required=True, description="Human label for the type."),
        Node(
            "layers",
            "array",
            required=True,
            min_items=1,
            item=LAYER,
            description="The assembly, outside layer first.",
        ),
    ),
    def_name="wallType",
    description="A named wall assembly: layers and their thicknesses.",
)

# --- elements --------------------------------------------------------------

OPENING = Node(
    name="opening",
    type="object",
    fields=(
        Node("id", "string", required=True, description="Unique id in the document."),
        Node(
            "kind",
            "string",
            required=True,
            enum=("door", "window", "void"),
            description="What the opening is.",
        ),
        Node(
            "offset_m",
            "number",
            required=True,
            description="Distance along the host run from its first point, in metres.",
        ),
        Node("width_m", "number", required=True, exclusive_min=0.0, description="Opening width."),
        Node("height_m", "number", required=True, exclusive_min=0.0, description="Opening height."),
        Node(
            "sill_m",
            "number",
            description=("Height of the opening base above the wall base; 0 for doors and voids."),
        ),
        Node(
            "tag",
            "string",
            description="Schedule tag (D1, W1, ...). Schedules are keyed by tag.",
        ),
        Node("property_sets", "object", free_keys=True, description="Custom data, free-form."),
    ),
    conditional_required=(("kind", "window", "sill_m"),),
    def_name="opening",
    description="An opening hosted by the wall run it is nested in.",
)

WALL = Node(
    name="wall",
    type="object",
    fields=(
        Node("id", "string", required=True, description="Unique id in the document."),
        Node(
            "points",
            "array",
            required=True,
            min_items=2,
            item=POINT,
            description="The wall centreline, at least two points.",
        ),
        Node(
            "closed",
            "boolean",
            description="True adds the closing segment; do not repeat the first point at the end.",
        ),
        Node("thickness_m", "number", exclusive_min=0.0, description="Wall thickness in metres."),
        Node("type_ref", "string", description="Key into the document's wallTypes."),
        Node(
            "height_m",
            "number",
            exclusive_min=0.0,
            description="Wall height; defaults to the level height.",
        ),
        Node("openings", "array", item=OPENING, description="Openings hosted by this run."),
        Node("property_sets", "object", free_keys=True, description="Custom data, free-form."),
    ),
    one_of_required=(("thickness_m", "type_ref"),),
    def_name="wall",
    description="A wall run (centreline), split at its vertices when mapped.",
)

SPACE = Node(
    name="space",
    type="object",
    fields=(
        Node(
            "number",
            "string",
            required=True,
            description="Room number, unique per level; the canonical space id is level-number.",
        ),
        Node("name", "string", description="Human label, e.g. Living."),
        Node("usage", "string", description="Free-text use of the space, e.g. living."),
        Node(
            "polygon",
            "array",
            required=True,
            min_items=3,
            item=POINT,
            min_items_code="bad-polygon",
            description="The inner-face outline of the space.",
        ),
        Node(
            "id",
            "string",
            description="Proposal-local id; carried in provenance, never the canonical id.",
        ),
        Node(
            "height_m",
            "number",
            exclusive_min=0.0,
            description="Space height; defaults to the level height.",
        ),
        Node("property_sets", "object", free_keys=True, description="Custom data, free-form."),
    ),
    def_name="space",
    description="One space (room) of a level, drawn to inner faces.",
)

SLAB = Node(
    name="slab",
    type="object",
    fields=(
        Node("id", "string", required=True, description="Unique id in the document."),
        Node(
            "polygon",
            "array",
            required=True,
            min_items=3,
            item=POINT,
            min_items_code="bad-polygon",
            description="The slab outline in plan.",
        ),
        Node(
            "thickness_m",
            "number",
            required=True,
            exclusive_min=0.0,
            description="Slab thickness.",
        ),
        Node(
            "base_offset_m",
            "number",
            required=True,
            description="Slab base offset from the level elevation, in metres (negative below).",
        ),
    ),
    def_name="slab",
    description="A slab, mapped to a BIM inventory record.",
)

LEVEL = Node(
    name="level",
    type="object",
    fields=(
        Node("id", "string", required=True, description="Unique level id in the document."),
        Node("name", "string", description="Human label for the level."),
        Node("elevation_m", "number", required=True, description="Level elevation in metres."),
        Node(
            "height_m",
            "number",
            required=True,
            exclusive_min=0.0,
            description="Level height in metres; the default wall and space height.",
        ),
        Node("walls", "array", required=True, item=WALL, description="The level's wall runs."),
        Node("spaces", "array", required=True, item=SPACE, description="The level's spaces."),
        Node("slabs", "array", item=SLAB, description="The level's slabs."),
    ),
    def_name="level",
    description="One storey of the building.",
)

DOCUMENT = Node(
    name="proposal",
    type="object",
    fields=(
        Node(
            "schema",
            "string",
            required=True,
            enum=(SCHEMA_ID,),
            enum_code="unsupported-version",
            description="The document schema id; must be exact.",
        ),
        Node(
            "version",
            "integer",
            required=True,
            enum=(PROPOSAL_VERSION,),
            enum_code="unsupported-version",
            description="The proposal version; must be exact.",
        ),
        Node("id", "string", required=True, description="The proposal's identity."),
        Node(
            "units",
            "string",
            required=True,
            enum=(UNITS,),
            enum_code="unsupported-units",
            description="The unit system; must be meters. Other units are refused, not converted.",
        ),
        Node(
            "wallTypes",
            "object",
            values=WALL_TYPE,
            description="Named wall assemblies, keyed by type id.",
        ),
        Node(
            "levels",
            "array",
            required=True,
            min_items=1,
            item=LEVEL,
            description="The levels (storeys) of the building.",
        ),
        Node("metadata", "object", free_keys=True, description="Custom data, free-form."),
    ),
    description="A native building proposal.",
)


# --- JSON Schema generation ------------------------------------------------


def _node_body(node: Node, defs: dict) -> dict:
    """The JSON Schema fragment for one node (no ``$ref`` indirection)."""
    if node.type == "object":
        if node.free_keys:
            return {"type": "object"}
        if node.values is not None:
            return {"type": "object", "additionalProperties": _node_ref(node.values, defs)}
        schema: dict = {"type": "object", "additionalProperties": False}
        properties = {f.name: _node_ref(f, defs) for f in node.fields}
        schema["properties"] = properties
        required = [f.name for f in node.fields if f.required]
        if required:
            schema["required"] = required
        extras = []
        for group in node.one_of_required:
            extras.append({"anyOf": [{"required": [name]} for name in group]})
        for cond_field, cond_value, required_field in node.conditional_required:
            extras.append(
                {
                    "if": {
                        "properties": {cond_field: {"const": cond_value}},
                        "required": [cond_field],
                    },
                    "then": {"required": [required_field]},
                }
            )
        if extras:
            schema["allOf"] = extras
        return schema
    if node.type == "array":
        schema = {"type": "array", "items": _node_ref(node.item, defs) if node.item else {}}
        if node.min_items is not None:
            schema["minItems"] = node.min_items
        return schema
    if node.type == "number":
        schema = {"type": "number"}
        if node.minimum is not None:
            schema["minimum"] = node.minimum
        if node.exclusive_min is not None:
            schema["exclusiveMinimum"] = node.exclusive_min
        if node.enum is not None:
            schema["enum"] = list(node.enum)
        return schema
    if node.type == "integer":
        schema = {"type": "integer"}
        if node.enum is not None:
            schema["enum"] = list(node.enum)
        return schema
    if node.type == "boolean":
        return {"type": "boolean"}
    if node.type == "string":
        schema = {"type": "string"}
        if node.enum is not None:
            schema["enum"] = list(node.enum)
        return schema
    return {}


def _node_ref(node: Node, defs: dict) -> dict:
    """The JSON Schema fragment for one node, registering ``$defs`` entries."""
    if node.def_name:
        if node.def_name not in defs:
            defs[node.def_name] = {}
            defs[node.def_name] = _node_body(node, defs)
        return {"$ref": f"#/$defs/{node.def_name}"}
    return _node_body(node, defs)


def proposal_json_schema() -> dict:
    """The JSON Schema (draft 2020-12) of the version 1 building proposal.

    Built programmatically from the field tables above, which are the same
    tables the validator reads, so the schema cannot drift from the
    validator. The schema states the document shape (types, required
    fields, enums, ranges, closed objects); the geometric rules
    (polygon validity, space overlap, opening placement) live in the
    validator, because JSON Schema cannot state them.
    """
    defs: dict = {}
    body = _node_ref(DOCUMENT, defs)
    schema = {
        "$schema": JSON_SCHEMA_DIALECT,
        "$id": JSON_SCHEMA_ID,
        "title": "Matchline building proposal, version 1",
        "description": (
            "A native building proposal: one JSON document per building, "
            "all lengths in metres, plan points in the canonical y-down frame."
        ),
    }
    schema.update(body)
    if defs:
        schema["$defs"] = defs
    return schema
