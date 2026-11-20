# Native building proposal schema, version 1

A **proposal** is one JSON document that describes a building: the format an
agent (or a person) writes, and the format Matchline compiles into the
canonical `BuildingModel`.

It is not a model. A proposal is validated, compiled, and then discarded; it
survives only as provenance on the facts it produced. There is no second
canonical model, and the validator needs nothing beyond the parsed document.

| Piece | Where | What it does |
|---|---|---|
| Field tables | `proposal/schema.py` | the one source of truth for the document shape |
| Validator | `proposal/validate.py` | pure function from a parsed document to diagnostics |
| Mapper | `proposal/mapper.py` | valid proposal -> canonical `BuildingModel` + mapping report |
| JSON Schema | `proposal/schema.py` (`proposal_json_schema()`) | draft 2020-12, generated from the same tables |

```python
from proposal import validate_proposal, map_proposal, proposal_json_schema

result = validate_proposal(doc)  # pure: no I/O, no mutation, no model
if result.ok:
    model, report = map_proposal(doc)  # raises ValueError on any error diagnostic
```

## 1. Document shape

One JSON document per building. All lengths in metres, all plan points
`[x, y]` in the canonical drawing frame (y growing downward). Authoring
directly in the canonical frame is a deliberate difference from Floorkit's
Y-up `[x, z]`: a native proposal needs no axis conversion at mapping time,
and the north-up flip stays where it already lives, in BEM export.

```json
{
  "schema": "matchline.building-proposal",
  "version": 1,
  "id": "two-room-house",
  "units": "meters",
  "wallTypes": { "<type-id>": { "name": "...", "layers": [
      { "material": "...", "thickness_m": 0.1, "function": "structure" } ] } },
  "levels": [ {
    "id": "ground", "name": "Ground", "elevation_m": 0.0, "height_m": 3.0,
    "walls": [ {
      "id": "outer-walls", "points": [[0,0],[8,0],[8,5],[0,5]], "closed": true,
      "thickness_m": 0.2, "height_m": 3.0,
      "openings": [ { "id": "front-door", "kind": "door",
                      "offset_m": 18.55, "width_m": 0.9, "height_m": 2.1,
                      "sill_m": 0.0, "tag": "D1" } ]
    } ],
    "spaces": [ { "number": "101", "name": "Living", "usage": "living",
                  "polygon": [[0.1,0.1],[4.95,0.1],[4.95,4.9],[0.1,4.9]] } ],
    "slabs": [ { "id": "ground-slab", "polygon": [[0,0],[8,0],[8,5],[0,5]],
                 "thickness_m": 0.2, "base_offset_m": -0.2 } ]
  } ],
  "metadata": {}
}
```

Field rules:

- **Header**: `schema` and `version` are required and exact. `units` must be
  `"meters"` in version 1; anything else is refused, not converted. `id` is
  the proposal's identity and becomes the provenance sheet stem
  (`proposal:{id}`).
- **Level**: `id`, `elevation_m`, `height_m`, `walls` and `spaces` are
  required (`walls` and `spaces` may be empty lists); `name` and `slabs` are
  optional. Levels are the storeys; there is no building wrapper (the
  canonical model has none), and a multi-building proposal is multiple
  documents.
- **Wall run**: `id` required and unique in the document. `points` is the
  centreline, at least 2 points; `closed: true` adds the closing segment and
  forbids repeating the first point at the end. Thickness comes from
  `thickness_m` or from `type_ref` into `wallTypes` (layers must sum to a
  positive total); one of the two is required. `height_m` defaults to the
  level height. `arcs`, curtain walls, and bays are out of version 1.
- **Opening**: nested in its wall run, which is the host relationship (the
  #1415 finding). `id` unique in the document, `kind` one of door, window,
  void. `offset_m` runs along the run from its first point, through every
  segment including the closing segment of a closed run. `width_m` and
  `height_m` are required and positive. `sill_m` is required for windows and
  defaults to 0 for doors and voids. `tag` is optional (see decision 2).
- **Space**: `number` required and unique per level, because the canonical id
  is derived from it. `polygon` is the inner-face outline (see decision 1), at
  least 3 points, simple, non-zero area. A proposal-local space `id` may be
  present for the author's convenience; the mapper never uses it for
  canonical identity, it survives only in provenance.
- **Wall type layers**: `material`, `thickness_m` (positive, or 0 for a
  membrane), `function` one of structure, substrate, thermal, finish,
  membrane. Layers carry no thermal properties, so a mapped wall informs
  geometry and layer order only, never a BEM construction by itself. Same
  limit as #1415 found in Floorkit, kept honestly.
- **Slab**: `id`, `polygon`, `thickness_m` and `base_offset_m` are required.
  Slabs are carried in version 1 and mapped to `BimElement` inventory records
  (the ifc_import pattern). The BEM path builds floors from spaces, so slabs
  do not become BEM surfaces in version 1; mapping them to inventory keeps
  the data instead of dropping it.
- **Extensions**: document `metadata` and per-element `property_sets` are the
  only places custom data may live. Unknown keys anywhere else are refused
  (validator code `unknown-field`).
- **Deliberately absent**: zones, schedules, lighting, HVAC. Those arrive
  through the existing linking paths, as they do for every other source.

Versioning: the proposal version is its own integer, independent of
`MODEL_VERSION`. A version bump requires a stated migration or an explicit
refusal of older documents; the validator names the versions it accepts.

## 2. Validator contract

A pure function from parsed JSON to a result. No geometry kernel, no model
construction, no file or network access, no mutation of the input.

```json
{ "ok": false,
  "errors":   [ { "path": "levels[0].walls[0].openings[2].offset_m",
                  "code": "opening-straddles-vertex",
                  "severity": "error",
                  "message": "opening 'front-door' spans 7.500 to 8.700 m along the run, crossing the vertex at 8.000 m; an opening must sit inside one segment.",
                  "details": { "offset_m": 7.5, "width_m": 1.2, "vertex_m": 8.0, "run_length_m": 26.0 } } ],
  "warnings": [ ] }
```

- Every diagnostic carries a JSON path into the document, a stable machine
  code, a severity (`error` or `warning` in version 1), and a one-sentence
  message with the numbers in it. `details` carries machine-usable numbers
  where they exist (for `opening-does-not-fit`: the fitting range and the
  nearest fitting offset), so an agent can correct a proposal without parsing
  prose.
- **Error codes in version 1**: `unknown-field`, `missing-field`,
  `wrong-type`, `unsupported-version`, `unsupported-units`, `duplicate-id`,
  `duplicate-room-number`, `bad-polygon` (self-intersecting, fewer than 3
  points, or zero area), `space-overlap`, `wall-too-short` (a run shorter than
  an opening it hosts), `opening-out-of-range`, `opening-straddles-vertex`,
  `opening-overlap` (two openings on one run whose intervals intersect),
  `opening-does-not-fit` (sill plus height above the wall height),
  `unknown-type-ref`, `layer-sum-mismatch`.
- **Warning codes in version 1**: `opening-tag-absent`,
  `wall-height-defaulted` (recorded when a wall takes the level height).
  "space without openings" is not a warning (ordinary).
- **Boundaries**: the validator checks document correctness, including the
  geometric facts that are local to the document (polygon validity, space
  overlap, opening placement along runs). Placement into the canonical model
  (which side of a wall a space borders, facade naming) is the mapper's job
  and is reported by the mapper, not the validator. The validator never
  repairs, never drops an element, never invents an id, and a document with
  any error is not mapped at all.

Code notes, for the cases the list above does not spell out:

- A value outside a field's enum is `wrong-type`, except the header fields,
  which use their own codes: `units` is `unsupported-units`, and `schema`
  and `version` are `unsupported-version`.
- A number outside a field's range (a negative thickness, a zero height) is
  `wrong-type`.
- A closed run that repeats its first point at the end is `wrong-type` at
  `...walls[i].points`: the closing segment is added by the mapper, so the
  point must not be repeated.
- A wall with neither `thickness_m` nor `type_ref` is `missing-field` at the
  wall.
- A window without `sill_m` is `missing-field` at `...sill_m`.

## 3. The mapper laws

The mapper is a pure function from a valid proposal to a canonical
`BuildingModel` plus a mapping report. These invariants are the test
surface:

- **L1 Determinism.** The same proposal bytes produce byte-identical
  canonical JSON. Element order follows document order; every derived id
  comes from a stated rule and nothing else.
- **L2 No silent loss.** Every proposal element is either mapped or named in
  the mapping report with a reason. Counts reconcile: proposed walls,
  spaces, openings, and slabs equal mapped plus reported, per level.
- **L3 Provenance completeness.** Every mapped fact carries `Provenance` with
  sheet_id `"proposal:{proposal id}"`, method `"proposal_mapping"`,
  confidence 1.0, and a note naming the source proposal element id. A mapped
  fact without provenance is a mapper bug, and the property tests treat it as
  one.
- **L4 Validator gate.** The mapper runs only on documents the validator
  accepted (`ok` true). It does not re-check and does not fix; a document
  with an error raises `ValueError` and is not mapped at all.
- **L5 Proposal is not a model.** The mapper's output is a canonical
  `BuildingModel`. No code path accepts a proposal where a `BuildingModel` is
  expected; the proposal itself is archived as provenance, never as a
  parallel source of truth.
- **L6 Frame fidelity.** Proposal coordinates are canonical frame metres. The
  mapper performs no axis flips, no scaling, no unit conversion.
- **L7 Host integrity.** After splitting, every opening has exactly one host
  segment. Straddles cannot reach the mapper (validator error), so the mapper
  never guesses a host.
- **L8 Space identity.** Canonical space id is `"{level}-{number}"`.
  Proposal space ids, when present, appear only in provenance notes.
- **L9 Envelope rule.** Walls split at vertices into straight segments; each
  segment becomes one `BimElement` (`"{wall id}#{segment index}"`, material
  layers from the wall type when present). A segment mirrors into
  `EnvelopeWall` when spaces border exactly one side of it and no space
  borders the other (the side test, sampled along the segment).
  `EnvelopeWall` ids are level-prefixed (`"{level}-{wall id}#{segment
  index}"`), the convention the battery's envelope check sums by.
  `EnvelopeWall.space_id` is the bordering space with the largest share of
  the segment length. Facades are named from the level boundary for
  axis-aligned segments (minimum y is north, maximum y south, maximum x
  east, minimum x west, in the canonical frame); a non-axis-aligned exterior
  segment gets facade `""` and a mapping-report flag, never a guessed
  facade.
- **L10 Interior openings.** An opening on an interior segment is stored
  once, on the lower-id bordering space, with `adjacent_space_id` naming the
  other space when both sides border one, and `None` when only one side does.
  An opening whose segment borders no space at the opening's position is
  reported and left unattached (a `BimOpening` still records it on its host
  segment).

Two mapping details the laws leave open, decided here and applied
consistently:

- A space's free-text `usage` is carried in the space's provenance note. It
  is not written into `Space.use.space_type`, because the canonical model's
  space-type keys are a closed vocabulary
  (`space_use_defaults_data.SPACE_USE_DEFAULTS`) and a proposal's free text is
  not one of them.
- A slab maps to a `BimElement` with `ifc_class` `"IfcSlab"`: plan area,
  volume (area x thickness), perimeter, thickness, and a placement whose z is
  the level elevation plus `base_offset_m`.

## 4. The three decisions from #1415

**Decision 1, polygon convention: spaces stay inner-face outlines, and the
fix belongs in the battery, not the mapper.** Authored spaces are drawn to
inner faces, so rooms on a level are disjoint islands separated by wall
thickness. The export path already handles that correctly through the
multipart footprint (`footprint_multipart_from_regions`). The battery's
footprint derivation keeps only the largest part of a disjoint union, which
is a derivation bug for any multi-room authored model, not a property of this
proposal format. The rejected alternative, expanding proposal polygons until
they tile, corrupts the two things #1415 proved exact: mapped areas would no
longer equal the authored polygons (failing the space area vs polygon check
and the 1% area gate), and wall thickness would be double-counted into floor
area.

**Decision 2, opening tags: the proposal carries an optional `tag`, and the
validator warns when it is absent.** Schedules are keyed by tag and the
battery errors on an empty tag, so a format with nowhere to put a tag
guarantees a battery error on every opening (#1415 hit exactly this). The
mapper copies `tag` through to `SpaceOpening.tag`. Absence stays a warning,
not an error: a tag is a schedule fact, and a proposal may legitimately be
authored before its schedule exists. When a proposal does carry tags, tag
coverage passes.

**Decision 3, interior openings: follow the existing Tier 1 pattern (L10).**
`ifc_import` already solved this the same way, so authored models and IFC
models behave identically downstream (construction assignment and vestibule
detection both read `adjacent_space_id`). #1415 did not exercise an interior
opening, so the golden tests add one (an interior door in the divider wall of
the worked example, as a second fixture variant) so L10 is tested, not just
stated.

## 5. The worked example

The #1415 fixture restated in the native schema: level ground, one closed
outer wall run `[0,0]` to `[8,5]` at 0.2 thick with `living-window` (offset
1.9), `bedroom-window` (offset 9.9), and `front-door` (offset 18.55); one
divider at x = 5, 0.1 thick; spaces 101 Living and 102 Bedroom drawn to inner
faces; one ground slab. Machine-checked: polygon areas 23.28 and 13.68,
opening intervals inside the 26 m run, no straddles. The document is
`tests/fixtures/proposal_example.json`, the ground truth is
`tests/fixtures/proposal_ground_truth.json`.

Expected canonical output: spaces `ground-101` (23.28 m2) and `ground-102`
(13.68 m2); four envelope segments `ground-outer-walls#0` to `#3` (north,
east, south, west; lengths 8, 5, 8, 5); five wall `BimElement`s
(`outer-walls#0`..`#3`, `divider#0`) plus one slab `BimElement`
(`ground-slab`, `ifc_class` `"IfcSlab"`); and three openings:

| Opening | Space | Facade | Host segment | Interval (m) | s_center (m) | Sill (m) | Head (m) | Area (m2) |
|---|---|---|---|---|---|---|---|---|
| living-window | ground-101 | north | `outer-walls#0` | 1.9 to 3.1 | 2.5 | 0.9 | 2.1 | 1.44 |
| bedroom-window | ground-102 | east | `outer-walls#1` | 1.9 to 3.1 | 2.5 | 0.9 | 2.1 | 1.44 |
| front-door | ground-101 | south | `outer-walls#2` | 5.55 to 6.45 | 6.0 | 0.0 | 2.1 | 1.89 |

The interior-door variant adds a door to the divider wall (offset 2.0, width
0.9, height 2.1). It maps to one `SpaceOpening` on `ground-101` with
`adjacent_space_id` `"ground-102"`, stored nowhere else.

## 6. The JSON Schema

`proposal_json_schema()` returns a draft 2020-12 schema built
programmatically from the same field tables the validator reads, so the
agent-facing contract cannot drift from the validator. It states the document
shape: types, required fields, enums, numeric ranges, closed objects (with
`metadata` and `property_sets` open), the wall rule "thickness_m or
type_ref", and the window rule "sill_m required when kind is window". The
geometric rules (polygon validity, space overlap, opening placement along
runs) live in the validator, because JSON Schema cannot state them.
