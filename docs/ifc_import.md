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

### Roof U-value

`IfcRoof` (`Pset_RoofCommon`) and `IfcSlab` with PredefinedType ROOF
(`Pset_SlabCommon`) are read for a stated `ThermalTransmittance`; the value
is never coerced from another property. When every roof element that states
one agrees (within 1e-6), one `IFC-RU<value>` construction is made
(`ifc_import:tier0:roof_u`, confidence 0.9) and set as
`BuildingModel.roof_construction_id`. Disagreeing values are not averaged:
the roof stays generic and the import revision note says why.

With no stated roof U anywhere, it is derived from roof layer sets with the
same rules as walls but ISO 6946 upward heat flow (Rsi 0.10, Rse 0.04; air
layers from the Table 2 upward column, 0.16 m²K/W at 25-300 mm):
`IFC-RUL<value>` (`roof_u_layers`, 0.8) from the file's own conductivities,
`IFC-RUM<value>` (`roof_u_lookup`, 0.6) when any layer needed the materials
table. Every roof element that has a layer set must yield a value and all
must agree, otherwise the roof stays generic with a note: one roof
construction would otherwise guess for the element it could not derive.
Roof elements without a layer set (an `IfcRoof` aggregating its slabs) are
skipped. Upward flow is the heating-season case; ISO 6946 Table 1 note 1
suggests horizontal values when a direction-independent U is wanted.

### Ground slab U-value

`IfcSlab` with PredefinedType BASESLAB is read for a stated
`Pset_SlabCommon.ThermalTransmittance`. Agreeing values make one
`IFC-SU<value>` construction (`ifc_import:tier0:slab_u`, confidence 0.9) set
as `BuildingModel.slab_construction_id`; disagreeing values leave the slab
generic with a revision note. FLOOR slabs are not assumed to touch the
ground. Slab U is never derived from layers: a slab on grade's U depends on
the ground and its perimeter (ISO 13370), which ISO 6946 layer sums ignore.

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
  Wall U-values are read when the wall carries
  `Pset_WallCommon.ThermalTransmittance`: one `IFC-U<value>` construction per
  distinct positive U (`ifc_import:tier0:wall_u`, conf 0.9), each envelope
  segment names it, and `apply_wall_u_rollup` runs after Tier 1 assigns
  spaces. Zero, negative or non-numeric values are ignored, never coerced.
  When the pset is absent, U is derived from the wall's single
  `IfcMaterialLayerSet` as 1 / (Rsi + Σ tᵢ/kᵢ + Rse), ISO 6946 Rsi 0.13 and
  Rse 0.04 m²K/W, using `Pset_MaterialThermal.ThermalConductivity` per layer
  material: one `IFC-UL<value>` construction per distinct U
  (`ifc_import:tier0:wall_u_layers`, conf 0.8). Any layer without a
  conductivity, a ventilated layer, or more than one layer set means no value
  (never guessed); a stated `ThermalTransmittance` always wins.
  When a layer has no `Pset_MaterialThermal`, its conductivity falls back to
  `materials.lookup_conductivity` by material name (ASHRAE HOF 2005 values as
  published in EnergyPlus `datasets/ASHRAE_2005_HOF_Materials.idf`): one
  `IFC-UM<value>` construction per distinct U (`ifc_import:tier0:wall_u_lookup`,
  conf 0.6, the note lists each looked-up material and its table entry). A
  file-stated conductivity always wins per layer. Matching is all-keywords,
  most-specific-wins; names that match entries with different conductivities
  or match nothing give no value.
  Air layers without a stated conductivity (`IsVentilated` UNKNOWN, which IFC4
  defines as an air gap without air exchange, or a material/layer name saying
  air/cavity/void/gap) get the ISO 6946:2007 Table 2 resistance for
  unventilated layers, horizontal heat flow (0.11 at 5 mm rising to 0.18 m²K/W
  from 25 mm, linearly interpolated), on the same `IFC-UM` tier; the note cites
  the standard. `IsVentilated` TRUE (open to outside air) and air layers
  thicker than 0.3 m (no single U per ISO 6946 5.3.1) give no value. The table
  assumes high-emissivity faces (≥ 0.8); reflective foil-faced cavities would
  be overstated in U terms and are not detected.
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

**Host-wall axis resolution (#505).** Projecting the along-wall center needs the host wall's own axis, resolved in this order:

1. `_wall_direction_from_envelope` — the wall is matched to its envelope edge by *position*: an edge qualifies only if one of its endpoints coincides with the wall's placement, which is exactly how envelope edges are built from walls. Length only breaks ties between edges that share an endpoint, and the returned direction is oriented to start at the wall's placement, so its sign is geometric rather than a function of export order.
2. `_wall_direction_from_entity` — the wall's own `RefDirection`, used when no envelope edge coincides.
3. Neither → the openings are left **unattached** and the wall is raised to the review queue as `opening_attachment`. The opening's world position is unknown in that case, and attaching it to whichever space contains a guessed point would silently mis-attribute it; openings feed the BEM takeoff via `datasets_adapter.rollup_takeoff`, so a wrong attachment becomes a wrong schedule.

Two defects fixed together here, because they mask each other:

- **Length-only matching.** The previous matcher took the first envelope edge within 0.1 m of the wall's length. Length is not an identity: every rectangle has two pairs of equal edges, so the winner depended on `model.envelope` ordering, and `ifc_export` writes walls in a nondeterministic set-derived order. A wrong edge yields a wrong direction, which puts the reconstructed point on the wrong side of the wall.
- **Off-by-half-a-wall projection.** `s_center_m` is measured from the wall's own origin along its local X axis — `_read_opening` validates it against `0 <= s <= length_m` — and `BimElement.placement_m` is that same origin. The reconstruction nevertheless subtracted `length_m / 2`, displacing every opening by half a wall along its own axis. On a 12 m wall that is 6 m, which dropped openings outright and could land them in a neighbouring space. This affected *every* wall, not only ambiguous ones.

The combination is what the #504 fixture was really measuring: with both present, the offset alone put the reconstructed point outside its space, and the ambiguity alone flipped which side it landed on.

Facade classification: derive the wall's 2-D outward normal from its `RefDirection` in the canonical frame; project onto the four cardinal axes; assign the axis with the largest absolute dot product. Walls whose largest projection is below a documented threshold (e.g. |dot| < 0.7 — within ~45° of diagonal) are flagged `facade_unclear` and left empty.

#### Test buildings

| Building | Purpose | Why |
|---|---|---|
| `test_ifc_import.py` fixture (3-room, 6 openings, 4 walls) | Primary Tier 1 validation | Openings A/B are on the two 12m exterior walls; D1/D2 are on the shared interior wall — tests interior vs exterior discrimination |
| `bldg_3room` (synth, pipeline fixture) | IFC-import + link + validate round-trip | Confirms that Tier 1 output feeds the full pipeline (simplify → validate → export) without regression |

The IFC fixture is preferred for unit-level assertions (exact opening counts per space, exact `host_facade` values). The `bldg_3room` model is preferred for integration-level checks (validation battery passes end-to-end after Tier 1).

Both buildings have openings on shared/interior walls; this is the minimum realistic configuration. A building with purely rectangular perimeter rooms and only exterior openings would not exercise the ambiguity-resolution logic.

#### validate/ guards

Tier 1 is blocked from shipping if any of these fire at error severity:

| Check | What it catches |
|---|---|
| `facade_opening_closure` | `SUM(space openings per facade) > gross wall area` — detects double-attachment or misclassified facade |
| `takeoff_counts_reconcile` | `count × schedule dims ≠ sum of recorded opening areas` — detects geometry-derived area vs schedule mismatches from incorrect attachment |
| *(new) `space_opening_attachment`* | spaces that should have openings but have `space.openings == []` — detects completely missed attachments on exterior walls |
| *(new) `facade_classification_complete`* | any `EnvelopeWall.facade == ""` after `infer_adjacency` — enforces criterion #5 above |
| *(new) `adjacency_review_acknowledged`* | any `adjacency_ambiguous` review item that is not `acknowledged` — enforces criterion #7 above |

The two new checks are added to `validate/` and `N_CHECKS` incremented before Tier 1 is merged. `facade_opening_closure` and `takeoff_counts_reconcile` already exist and will start passing once `Space.openings` is populated; they serve as regression guards without requiring new code.

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

## Limitations

- **Schema coverage is partial**: The importer handles `IfcSpace`, `IfcWall`, `IfcDoor`, `IfcWindow`, `IfcSlab`, `IfcZone`, and basic material layer sets. Structural elements (beams, columns, foundations), MEP systems, and custom property sets are not parsed.
- **Polygon-based adjacency inference**: `infer_adjacency` uses 2D polygon containment for opening attachment and space adjacency. Non-planar walls, curved geometry, and complex openings may produce incorrect attachments.
- **Facade classification threshold is heuristic**: Walls within ~45° of diagonal are flagged `facade_unclear`. The 0.7 dot-product threshold is not validated against real buildings with oblique facade orientations.
- **No IfcOpenShell geometry for Tier 1**: Opening attachment in Tier 1 uses a fallback polygon-containment test; proper 3D clash detection is deferred to future tiers.
- **Host-wall matching relies on an import-side invariant**: the positional envelope match assumes an envelope edge endpoint coincides with the wall's placement, which holds because `_read_bim_elements` builds envelope edges from walls and openings attach before segments move onto centrelines (#579). After that, a wall's own segment is matched by GlobalId with its placement within the wall thickness of one end. A model whose envelope was populated some other way — with edges offset from wall centrelines, or split into sub-segments — will not match positionally, and those openings fall through to `RefDirection` or are left unattached and flagged. The 0.1 m endpoint tolerance is also a tolerance on that assumption, not a measured survey tolerance.
- **Refusing to guess loses openings**: when several same-length edges share a wall's origin, or none coincides, the wall direction resolves to `None` and its openings are dropped rather than mis-attributed. This trades completeness for correctness; the count of such walls is not yet reported as a metric, so the loss is only visible through the `opening_attachment` review queue.
- **Geometry-less spaces are not handled**: Spaces without geometry are currently skipped; they do not appear in the BIM model output.
- **No IFC4 multi-level spatial structure**: The importer flattens the spatial hierarchy into a single building model; site, building, and floor levels are not preserved as separate entities.


## Daylight area under skylights

After skylights are attached to the space under them, each space with
skylights gets `daylight.toplit` zones (roadmap item 3, toplighting), per
ASHRAE 90.1 Section 3.2 "daylight area under skylights" as revised by the
addenda package t ... co to 90.1-2016 (Figure 3.2-2): the opening beneath the
skylight, extended horizontally in each direction by the smaller of 0.7 x
ceiling height and the distance to an opaque obstruction. `toplit_m2` is the
union of the zones, so overlaps are not double counted. Code:
`daylight_skylights.compute_skylight_daylight`.

- Ceiling height is the storey wall height (IFC spaces here carry no
  separate ceiling).
- Obstructions: only full-height ones, i.e. the space's own boundary walls
  (the zone is clipped to the space polygon). Partial-height obstructions
  inside a space are not in the model, so zones may be optimistic there.
- Multistory spaces (Figure 3.2-5) are not handled.
- A skylight with no plan position or size goes in
  `daylight.unplaced_skylights`, never placed by guess. On the drawing path,
  takeoff skylights carry a count and no position, so nothing is computed yet.

## Wall centrelines (#579)

An IFC wall's axis (its placement and RefDirection) is a reference line
the author chose: Revit puts it on the centre, a face, or a core face, and
matchline's own exporter puts it on the exterior face with the body
inward. After openings attach, each Tier 0 envelope segment is moved onto
the wall's **body centreline**, the line every later step assumes (end
joins #575, linings #577, unclaimed loops #581).

- The centre comes from the body profile's extents across the wall
  (geometry), or else from `IfcMaterialLayerSetUsage` (only when
  `LayerSetDirection` is `AXIS2`; POSITIVE spans `[offset, offset+total]`,
  NEGATIVE `[offset−total, offset]`). Geometry wins; a disagreement over
  1 mm is noted on the segment. With neither, the axis is kept and noted.
- Moved segments are counted in the import summary
  (`wall centrelines: N segments moved off the reference line`).
- Exterior-face walls lose half a wall at each corner once the joins close
  them, so envelope areas on a round trip of our own export come back at
  centreline length (a 20 m face wall of 0.2 m returns 19.8 m).
- Shading devices are hosted after the move; `ShadingSurface.offset_m`
  keeps the gap from the centreline out to the face the plate touches, so
  `depth_m` stays measured from the face and re-export puts the plate back
  on the same quad.
- `envelope_area_matches_perimeter` compares against the footprint offset
  by ±t/2 as well, since spaces may be drawn to either face.

## Junction splits and in-line continuations (#576)

After end joins, two passes run on the Tier 0 IFC segments of each level
(`ifc_wall_split.py`, idea from Pascal's `joinInLineEnds` and
`splitCrossingWalls`, MIT):

- **In-line continuations.** A thinner wall continuing a thicker one flush
  with one face ends beside the other's end, offset sideways by half the
  thickness difference. Its end moves onto the thicker wall's end so the
  two form one continuous run: near-parallel (sine ≤ 0.03), lateral offset
  within the thicker half-thickness + 0.01 m, along-axis gap ≤ 0.1 m, move
  ≤ 10% of the wall's length. Only the thinner wall moves; unknown
  thickness or two equally near candidates leave it as-is (tie noted).
- **Junction splits.** Two segments crossing mid-span (X) both split at the
  crossing; a segment end landing mid-span on another (T) splits the
  through segment. Junctions within the thicker half-thickness + 0.05 m of
  an end are corners, not splits. Pieces are `<id>.1`, `<id>.2`, … and keep
  the wall's GlobalId, construction and provenance, so facade
  classification names one space behind each piece (a 20 m exterior wall
  behind two rooms now gives each its own `space_id`). The BimElement
  inventory is untouched.
- Summary lines: `in-line walls: N ends joined`, `wall splits: N at X/T junctions`.
- Interior pieces still leave the envelope at classification, so interior
  shared-wall bookkeeping has no home in the model yet.

## Elements outside any storey (#585)

Elements are normally read from their storey's
`IfcRelContainedInSpatialStructure`. Walls, slabs, roofs, columns, beams,
curtain walls and duct/distribution elements contained in the IfcBuilding
or IfcSite instead, or not contained anywhere, are now placed by height
(idea from Pascal's `storey-semantics.ts`):

- A storey's band runs from its elevation less 0.1 m up to the next
  storey's elevation less 0.1 m; the top band is open.
- When exactly one storey fits, the element goes there with
  `storey_method="elevation"`, confidence at most 0.6 and a provenance
  note. Elements already in a storey keep `storey_method=""`.
- Below the lowest band, or two storeys at the fitting elevation, the
  element is not read and is listed as unassigned with its GlobalId and
  reason. There is no fallback to the lowest storey.
- Summary line: `storey fallback: N by elevation, M unassigned: <GlobalId (reason)>, ...`.
- Elements contained in an IfcSpace are out of scope here.

## Determinism (#586)

The same IFC file imports to byte-identical model JSON in every process.
Ties are broken by GlobalId (or another stable key), never by object id or
set/dict order. `tests/test_ifc_determinism.py` imports each IFC fixture in
separate interpreters with different `PYTHONHASHSEED` values and compares
the JSON. All fixtures passed with no code change when the test was added;
any new tie-break must keep it green.

## Fragmented walls and doubled openings (#578)

Defect-injection tests (`tests/test_ifc_fragment_parity.py`, cases modelled
on Pascal's `cleanup.test.ts`) compare the fixture with versions whose
south wall is exported as three collinear fragments, as fragments of
different height, or with a window doubled on itself.

- Fragments already conserve wall area, opening counts and opening
  attachment, so there is **no fragment merge**. Fragments of different
  height keep their own heights and areas.
- A doubled window did not: it counted twice. Openings in one host wall
  that share category and tag and agree on width, height, sill and position
  along the wall within 0.05 m are now copies; the one whose GlobalId sorts
  first is kept. Any of those values missing means never a copy. Summary
  line: `duplicate openings: N doubled copies dropped`.

## Lining and hidden walls (#577)

Exporters often write tiles, skirting, rainscreen panels and furring as their
own `IfcWall` on a real wall's face, or leave a short wall buried inside a
thicker one. Before wall ends are joined, `ifc_wall_linings.exclude_linings`
takes such walls out of the envelope, so they add no opaque area, take no part
in facade classification, U averaging or wall-loop detection, and cannot be
joined onto. A wall on the same level, parallel to a host, is a lining when:

- **embedded**: the host is thicker and the wall's body lies inside it;
- **face to face**: the wall is shorter than the host and lies against its face
  (or runs through its body) over at least 90% of its length;
- **cladding**: the wall is at most 35 mm thick, the host more than twice as
  thick, and at least half its length lies on such hosts' faces.

The `BimElement` stays in the inventory with `role="lining"` and a provenance
note naming the host and the test that matched. Never guessed: a wall with
unknown thickness is neither lining nor host, and a lining that hosts openings
stays in the envelope with a note. The import summary reports
`linings: N walls kept out of the envelope`. A lining's own material layers are
not added to its host's U-value yet. Tests and tolerances follow
`redundantWallIds` in the Pascal editor IFC converter (MIT, commit 67f8041).
