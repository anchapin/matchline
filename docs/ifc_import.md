# IFC import frontend (Tier 0)

`ifc_import.py`: `import_ifc(path) -> BuildingModel`. Reads an IFC4 file into
the canonical building model **without** `IfcRelSpaceBoundary` (ROADMAP.md
item 7). The drawing pipeline and the IFC pipeline converge on the same
`BuildingModel`; everything downstream (simplify → validate → export) is
shared.

## What Tier 0 recovers

| IFC source | Recovered as | Notes |
|---|---|---|
| `IfcProject`→`IfcSite`→`IfcBuilding`→`IfcBuildingStorey` via `IfcRelAggregates` | `Level` per storey (id `L1..Ln` by elevation) | model name from `IfcBuilding`/`IfcProject` |
| `IfcSpace` + own solid geometry | `Space` + footprint polygon, area, volume | polygon from `IfcExtrudedAreaSolid`/`IfcArbitraryClosedProfileDef`; room number parsed from trailing token of `Name` (`"OPEN OFFICE 101"` → name/number) |
| `IfcSpace` without geometry | `Space` with area from `Qto_SpaceBaseQuantities.GrossFloorArea` | confidence 0.6 + `space_no_geometry` review item |
| `IfcWall` (+StandardCase) | `BimElement` **and** `EnvelopeWall` (centerline segment) | dims from rectangle profile, else local extents via geometry kernel |
| `IfcSlab` / `IfcRoof` / `IfcColumn` / `IfcBeam` / `IfcCurtainWall` / `IfcDuctSegment` | `BimElement` inventory | placement-only elements recorded with confidence 0.5 |
| `IfcOpeningElement` + `IfcRelVoidsElement` + `IfcRelFillsElement` | `BimOpening` on the host wall's `BimElement` | width/height/sill/`s_center` from the opening solid mapped into the wall frame; category from fill class; tag parsed from fill `Name` |
| `IfcRelAssociatesMaterial` → `IfcMaterialLayerSet` | `BimElement.material_layers` + total thickness | the analytical wall-thickness answer (roadmap item 1, BIM path) |
| `IfcRelContainedInSpatialStructure` | element → storey assignment | |
| `IfcZone` via `IfcRelAssignsToGroup` | `Zone` with `space_ids` | opportunistic; skipped when absent |
| `IfcUnitAssignment` | length → meters scale | SI prefixes + `IfcConversionBasedUnit` (e.g. feet); defaults to 1.0 |

Every fact carries `Provenance(method="ifc_import:tier0:<aspect>")` and the
source `GlobalId` in its note, so IFC → model → gbXML/IFC round-trips stay
traceable. `model.log_revision(..., "ingest", ...)` records the import.

Deliberately **not** attached in Tier 0: openings to spaces, and facade
(N/S/E/W) classification of envelope walls — both need adjacency, which is
Tier 1.

## Coordinate frame

IFC is Z-up. The canonical model is y-down (drawing frame); `bem_export`
flips y on the way out (north-up), so the importer mirrors it back:
`canonical = (x_ifc, -y_ifc, z_ifc)`. For foreign IFC files this is a
documented handedness choice, recorded in provenance.

## Confidence rationale

| Aspect | Confidence | Why |
|---|---|---|
| Spatial hierarchy / containment | 1.0 | explicit relationships, no inference |
| Space identity from solid | 0.95 | authored geometry |
| Space number from `Name` parse | 0.85 | convention-dependent heuristic |
| Space area from quantity fallback | 0.6 + review | no geometry; quantity may be stale |
| Element dims (profile or kernel) | 0.95 | measured from the solid |
| Element placement-only | 0.5 | inventory, no dims |
| Material layers | 0.95 | authored; thickness is exact |
| Openings (void+fill+solid) | 0.95 | measured in the wall frame |
| Opening tag from fill `Name` | 0.8 | convention-dependent parse |
| Zones from group assignment | 0.9 | explicit, but grouping intent varies |

## Known gaps

- **Thermal properties** (`Pset_MaterialThermal`) are usually absent in
  practice — the same material→property lookup table the drawing path needs.
- **MEP quality varies wildly**: ducts/zones are opportunistic, never
  load-bearing.
- **Multi-storey**: the hierarchy walk handles N storeys; the fixture only
  covers one (test-coverage gap, not a code gap).
- **Openings in curtain walls**, non-rectangular openings, `IfcBooleanClippingResult`
  / mapped representations: skipped in v1 (placement-level record only).
- **Space name→number** parsing is convention-dependent (`"101"`,
  `"A101"` work; exotic schemes won't).
- Facade classification and opening→space attachment need Tier 1.

## Tier 1 / Tier 2 plan

**Tier 1 — geometric adjacency inference** (`infer_adjacency()`, currently a
stub raising `NotImplementedError`): for each space solid, find wall faces
within tolerance of its boundary (proximity/clash queries); classify
interior vs exterior via outward ray tests; attach `BimOpening`s to `Space`s
as `SpaceOpening`s; classify envelope facades. Every inference carries method
+ confidence; ambiguous cases go to the review queue. The Tier-0 pieces this
builds on are all in place: wall segments with known transforms, opening
solids in wall frames, space footprint polygons.

**Tier 2 — authored boundaries as cross-check**: when `IfcRelSpaceBoundary`
exists, run Tier 1 independently and flag disagreements for review.
Inferred-vs-authored agreement becomes a validation check, not a
load-bearing dependency.

## IfcOpenShell notes (0.8.5, vendored)

- `create_shape` returns **product-local** vertices (placements not applied);
  the importer composes `IfcLocalPlacement` chains itself
  (`_placement_transform` / `_compose` / `_invert` / `_apply`).
- `add_wall_representation` emits `IfcArbitraryClosedProfileDef`, not
  `IfcRectangleProfileDef` — dims come from local extents via the kernel,
  with the rectangle-profile path kept as a fast path for other tools.
- Geometry-kernel vertices are in **file units**; the unit scale is applied
  by callers.
- `assign_representation` takes the `IfcShapeRepresentation` (it wraps the
  `IfcProductDefinitionShape` itself).

## Tests

`tests/test_ifc_import.py` (11 tests): the fixture is our own
`bem_export.write_ifc4` output enriched in test code (space solids, material
layer sets, opening/fill solids, an `IfcZone`, a placement-only duct, one
geometry-less space). Round-trip fidelity: space areas/volumes exact to
1e-6, wall lengths exact, opening dims/sills/tags exact, material layers
exact, zone membership exact.
