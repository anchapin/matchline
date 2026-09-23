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

### Tier 1 completion criteria

Tier 1 is done when all of the following are true.

#### Minimum viable feature set

| # | Criterion | What must be true |
|---|---|---|
| 1 | `infer_adjacency(model)` no longer raises `NotImplementedError` | the function is implemented and called by `import_ifc` after Tier 0 |
| 2 | Every `Space` with an exterior wall has `space.openings` populated | each `BimOpening` on an exterior wall is matched to its parent `Space` and attached as a `SpaceOpening` |
| 3 | `SpaceOpening.host_facade` is set on every attached opening | the cardinal facade (`"north"`, `"south"`, `"east"`, `"west"`) is derived from the wall's outward normal in the canonical frame |
| 4 | `SpaceOpening.host_interval_m` is set on every attached opening | the `[s0, s1]` interval along the wall centerline is computed from opening geometry + wall length |
| 5 | `EnvelopeWall.facade` is non-empty on every envelope wall | all 4 envelope segments are classified; walls with no dominant cardinal direction are flagged for review |
| 6 | All inferred facts carry `Provenance` with method `"ifc_import:tier1:*"` | every attachment and classification decision is traceable to its source `GlobalId`(s) and method |
| 7 | Ambiguous cases go to the review queue | openings whose center falls within tolerance of two spaces' boundaries, or walls whose orientation is indeterminate, are flagged with a named review kind (e.g. `adjacency_ambiguous`) |

**Algorithm note:** The fallback `_attach_openings_to_spaces` (currently at the end of `import_ifc`) implements a basic polygon-containment test: the opening's along-wall center point is projected into world coordinates and tested against each space polygon. Tier 1 replaces this with a proper proximity/clash query using IfcOpenShell geometry but the same data-flow: `BimOpening` → `SpaceOpening` on the correct `Space`.

Facade classification: derive the wall's 2-D outward normal from its `RefDirection` in the canonical frame; project onto the four cardinal axes; assign the axis with the largest absolute dot product. Walls whose largest projection is below a documented threshold (e.g. |dot| < 0.7 — within ~45° of diagonal) are flagged `facade_unclear` and left empty.

#### Test buildings

| Building | Purpose | Why |
|---|---|---|
| `test_ifc_import.py` fixture (3-room, 6 openings, 4 walls) | Primary Tier 1 validation | Openings A/B are on the two 12m exterior walls; D1/D2 are on the shared interior wall — tests interior vs exterior discrimination |
| `bldg_3room` (synth, pipeline fixture) | IFC-import + link + validate round-trip | Confirms that Tier 1 output feeds the full pipeline (simplify → validate → export) without regression |

The IFC fixture is preferred for unit-level assertions (exact opening counts per space, exact `host_facade` values). The `bldg_3room` model is preferred for integration-level checks (validation battery passes end-to-end after Tier 1).

Both buildings have openings on shared/interior walls; this is the minimum realistic configuration. A building with purely rectangular perimeter rooms and only exterior openings would not exercise the ambiguity-resolution logic.

#### validate.py guards

Tier 1 is blocked from shipping if any of these fire at error severity:

| Check | What it catches |
|---|---|
| `facade_opening_closure` | `SUM(space openings per facade) > gross wall area` — detects double-attachment or misclassified facade |
| `takeoff_counts_reconcile` | `count × schedule dims ≠ sum of recorded opening areas` — detects geometry-derived area vs schedule mismatches from incorrect attachment |
| *(new) `space_opening_attachment`* | spaces that should have openings but have `space.openings == []` — detects completely missed attachments on exterior walls |
| *(new) `facade_classification_complete`* | any `EnvelopeWall.facade == ""` after `infer_adjacency` — enforces criterion #5 above |
| *(new) `adjacency_review_acknowledged`* | any `adjacency_ambiguous` review item that is not `acknowledged` — enforces criterion #7 above |

The two new checks are added to `validate.py` and `N_CHECKS` incremented before Tier 1 is merged. `facade_opening_closure` and `takeoff_counts_reconcile` already exist and will start passing once `Space.openings` is populated; they serve as regression guards without requiring new code.

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
