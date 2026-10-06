# BEM Export — `bem_export.py`

Exports Jesse-Vision takeoff results to formats that building energy
modeling (BEM) software can import: **gbXML 6.01** (primary) and **IFC4**
(via IfcOpenShell). Demo: `run_bem_export.py`.

## Pipeline position

```
takeoff (detections -> schedule -> rollup) ─┐
labeled rooms (room_labels)                 ├─> model_from_takeoff() ─> BEMModel ─┬─> write_gbxml() ─> validate_gbxml()
simplified envelope (geometry_simplify) ────┘                                    └─> write_ifc4()  ─> validate_ifc4()
```

`model_from_takeoff(takeoff, labeled, sres, wall_height_m=3.0)` assembles a
unit-clean intermediate model (meters, x=east / y=north / z=up). It requires
`takeoff.scale.m_per_px` — the count×dims path needs no scale, but BEM
geometry does. The simplifier's area-preservation report
(`area_delta_pct`, tolerance) is carried through on the model and embedded
in the gbXML file comment.

## Schema choices (gbXML 6.01)

- Validated with lxml against the real 6.01 XSD
  (`schemas/GreenBuildingXML_Ver6.01.xsd`, fetched from gbxml.org).
- Structure: `gbXML > Campus > (Location, Building > (BuildingStorey,
  Space*), Surface*)`, plus root-level `Zone` and `Construction` elements.
  (Non-obvious per the XSD: `Space` is a child of `Building`, `Zone` is a
  child of the `gbXML` root, and `Location` requires `ZipcodeOrPostalCode`.)
- Walls: `ExteriorWall` with `RectangularGeometry` — 4 CartesianPoints
  starting at the bottom-left corner **when facing from outside** (per the
  schema doc), with `Azimuth` (outward normal, degrees clockwise from north)
  and `Tilt=90`.
- Roof: `PlanarGeometry` PolyLoop, CCW from above (right-hand rule →
  outward +z), `surfaceType="Roof"`. Ground floor: reversed loop,
  `SlabOnGrade`. (`PlanarGeometry` takes no `Tilt` per the XSD.)
- `Surface` requires `constructionIdRef`: v1 uses three placeholder
  constructions (generic wall/roof/slab with nominal U-values 0.50/0.30/
  0.40 W/m²K). Real assemblies are a v2 enrichment — drawings don't carry
  them.
- Per-space wall constructions (roadmap item 6): when a space carries an
  area-weighted wall U (`Space.wall_u_value_w_m2k`, written by
  `constructions.apply_wall_u_rollup`), the export adds a
  `const-wall-<space id>` construction with that U-value and every exterior
  wall assigned to the space references it. Spaces without one keep
  `const-wall`. The IFC4 writer puts the same value on each wall as
  `Pset_WallCommon.ThermalTransmittance`.
- Spaces get a full closed `ShellGeometry` (floor + roof + wall quads with
  outward normals), `Area`, `Volume` (= area × wall height), and
  `CADObjectId` = room number. All spaces share one `Zone` ("Zone 1") —
  HVAC zoning is the planned feature #1 and will refine this.

## Mapping decisions

| Takeoff concept | gbXML | IFC4 |
|---|---|---|
| Room (name, number, polygon, area) | `Space` (+ shell) | `IfcSpace` (+ `Qto_SpaceBaseQuantities.GrossFloorArea`) |
| Simplified envelope edge | `ExteriorWall` surface | `IfcWall` (SweptSolid, 0.2 m thick) |
| Roof / ground | `Roof` / `SlabOnGrade` | — (v1 gap) |
| Window/door unit (tag × schedule dims) | `Opening` (`FixedWindow` / `NonSlidingDoor` — the enum has no generic "Door") | `IfcOpeningElement` → `IfcRelVoidsElement` → `IfcWindow`/`IfcDoor` (`IfcRelFillsElement`) |
| Storey | `BuildingStorey` | `IfcBuildingStorey` (+ Site/Building/Project hierarchy) |

Coordinate mapping (documented assumption): drawing pixel (x right, y down)
→ meters (x east, y north = −y·scale, z up). I.e. "up" on a north-up sheet
is north; azimuths follow.

## Opening placement assumptions

Drawings give counts per type, not positions, so placement is deterministic
but synthetic:

1. Openings are apportioned to walls by **largest remainder** proportional
   to wall length, per category (window/door totals exact).
   An opening that knows its space and facade goes only on that space's
   share of that facade (so a facade split per room keeps each room's
   windows on its own wall); with only a facade known it goes on that
   facade's walls; with neither, on all walls.
2. On a wall, units are evenly spaced (centers at (j+0.5)·L/k), sorted by
   (category, tag).
3. Sills: windows 0.9 m, doors 0.0 m. Heights/widths clamped to fit the
   wall; every clamp is logged (never silent).
4. gbXML openings use **local 2-D coordinates** from the parent surface's
   bottom-left corner, no Azimuth/Tilt (per the schema doc). The IFC writer
   reuses the same `_place_openings_on_wall` helper, so both files agree.

Each exterior wall is assigned to the space containing its inward-offset
midpoint (nearest-centroid fallback) for `AdjacentSpaceId`.

## Validation performed

- **gbXML**: `validate_gbxml()` — lxml XSD validation against the official
  6.01 schema, plus semantic checks the XSD can't express (id uniqueness,
  idRef integrity). Demo sheets 007/008/009: **PASS** (3/3).
- **IFC4**: `validate_ifc4()` — round-trip parse; checks schema version,
  wall SweptSolid geometry, space aggregation under the storey, and that
  every opening voids a wall and is filled. Demo: **PASS** (3/3).

## Demo results (synthetic sheet GT)

| sheet | spaces | gbXML surfaces | openings (win/door) | envelope Δarea | floor area vs GT |
|---|---|---|---|---|---|
| 007 | 8 (8/8 labeled) | 6 (4 walls+roof+slab) | 8 (4/4) | +0.000% | 419.3 m² = GT |
| 008 | 7 (7/7) | 6 | 12 (5/7) | +0.000% | 360.3 m² = GT |
| 009 | 8 (8/8) | 8 (6 walls) | 9 (4/5) | +0.000% | 318.7 m² = GT |

Surface reduction from simplification: 60–67%. Outputs in `bem_out/`.

## Gaps (v2)

1. **Interior partitions omitted** — spaces are bounded only by the
   envelope; no `InteriorWall` surfaces between rooms. Fine for loads, wrong
   for zoning/adjacency.
2. **Single storey, uniform 3.0 m height** — no multi-storey or
   floor-to-floor variation; roof/slab adjacency points at the largest
   space (approximation).
3. **Placeholder constructions** — generic U-values unless a space has an
   area-weighted wall U from its wall constructions. The roof uses the
   model's `roof_construction_id` U-value when one is set (gbXML
   `const-roof` "Exterior roof (from model)", IFC4
   `Pset_SlabCommon.ThermalTransmittance` on the `IfcSlab ROOF`), else the
   generic 0.30. The ground slab likewise uses `slab_construction_id` when
   set (gbXML `const-slab` "Slab on grade (from model)", IFC4 `IfcSlab
   BASESLAB` with `Pset_SlabCommon`), else the generic 0.40. Drawing-derived
   assemblies still need spec/schedule input.
4. **Opening positions are synthetic** (counts are real, positions are
   apportioned) — true positions need elevation-view parsing.
5. **IFC roof written only when it carries something**: an `IfcSlab ROOF`
   is emitted when skylights need a host or the model has a roof U-value;
   an `IfcSlab BASESLAB` (nominal 0.15 m, top at z=0) only when the model
   has a ground slab U-value.
6. **IfcSpace has no solid geometry** in v1 (placement + quantities only).
7. Window/door opening areas are not yet reconciled against the simplified
   envelope (double-count risk noted in `docs/geometry_simplify.md`).

## Limitations

- **Incomplete schema coverage**: The IDF template covers residential and small commercial U-values, window SHGC, and lighting power density only. Large commercial HVAC systems (VAV, RTU, chiller) are not yet templated.
- **No load balancing or thermal zone network solving**: EnergyPlus is called in direct-simulation mode only; no iterative zone-balance passes are made.
- **Geometry assumptions**: All spaces are treated as rectangular with uniform internal gains. Non-rectangular rooms, indentations, and re-entrant corners are approximated, which affects envelope area and aspect-ratio-dependent infiltration estimates.
- **Weather data**: Simulations use a single representative climate file; mixed-mode or adaptive comfort strategies are not modeled.
- **No occupancy schedule derivation**: Schedules are synthetic defaults; actual operational patterns are not extracted from drawings.
- **Confidence reflects template fidelity, not ground truth**: A high-confidence envelope does not mean the BEM matches the as-built building—it means the extraction pipeline found sufficient inputs to populate the template.

## OpenStudio round-trip gate (#628)

`tests/test_openstudio_roundtrip.py` writes a flat, gable, hip and shed model, each with a window, a door, a skylight and an overhang. Each file goes through OpenStudio's gbXML reverse translator, and the test fails on any translation error, on a missing surface or sub-surface, on a space that is not an enclosed volume, or on a space volume that differs from ours. The only message allowed through is OpenStudio's note that a 6.01 file skips its 7.03 schema check; we validate against the 6.01 XSD ourselves. CI installs the `openstudio` extra (`pip install -e ".[test,openstudio]"`). Without it, the tests skip.

The gate's first finding (#627): OpenStudio reads only `PlanarGeometry`, so walls written with `RectangularGeometry` alone were dropped together with their windows and doors. Every wall now carries a PolyLoop (its true outline under a sloped roof), and wall openings carry an absolute PolyLoop alongside their local rectangle.

## Appendix G perimeter/core thermal blocks (#630)

`thermal_zoning.py` splits a floor plate into ASHRAE 90.1 Appendix G (G3.1.1) thermal blocks for drawings that define no HVAC zones: perimeter zones 15 ft (4.572 m) deep, one per compass orientation of the exterior walls (north 315° to <45°, east 45° to <135°, and so on), plus a core.

- Each wall claims the wedge between it and the angle bisectors at its two corners. Where two wedges overlap, the overlap goes to the wall whose line is nearer. This divides corners proportionately, gives triangles and a ridge on a plate too narrow for a core, and splits a reflex corner on its bisector.
- Walls in the same bucket merge into one block. A plate with no wall facing a direction gets no block for it.
- The blocks tile the plate exactly; `ZoningError` is raised if they don't.
- `assign_spaces` gives each space its share of area in every block and its majority block (ties go to the block listed first).

Exports are unchanged. Whether the gbXML `Zone` / IFC `IfcZone` output splits rooms on block lines (Appendix G's "divided proportionately") or assigns whole rooms to their majority block is still open.

## Inter-story surfaces and shaft stacks (#632)

`interstory.match_interstory(model)` matches the horizontal surfaces between consecutive levels (ordered by `Level.elevation_z_m`). Each overlap of a lower-level space with an upper-level space is one `interior` surface carrying both space ids, so the lower space's ceiling and the upper space's floor are the same surface. Parts of an upper space over no lower space are `exposed_floor` (over outdoor air), parts of a lower space under no upper space are `roof` at the top of that level, the lowest level's floors are `ground`, and the top level's ceilings are `roof`. Every space's floor pieces and ceiling pieces each sum to its plan area, or the step raises `InterstoryError`; overlapping spaces on one level also raise.

Shafts and elevator cores on consecutive levels with footprint IoU >= 0.8 form one `ShaftStack`. A shaft that only partly lines up with a shaft on the next level, a stack that ends under or over an occupied room on an adjacent level, and a level that starts below the top of the level under it go to the review queue as `interstory` items. Nothing in the model is moved, merged or corrected. The single-storey writers do not use the result yet; it is the input for the multi-storey writer. Atria are out of scope until spaces carry a multi-storey flag.

## Below-grade storeys and boundary types (#634)

`Level.above_ground` is True or False only when a source states it; IFC import reads `Pset_BuildingStoreyCommon.AboveGround` (UNKNOWN or absent stays None, never inferred from elevation). `below_grade.boundary_types(model)` names a gbXML `surfaceType` for each envelope wall and each #632 horizontal surface: `UndergroundWall`/`ExteriorWall`; lowest floors `UndergroundSlab`/`SlabOnGrade`; between storeys `InteriorFloor`; floors over no lower space `UndergroundSlab` below grade, `RaisedFloor` above; roofs `Roof` above grade, `UndergroundCeiling` under another below-grade storey, and unresolved (review item) when a below-grade space has no storey over it, because only the drawings say whether that deck is earth-covered or open. Levels with the property unstated keep today's above-ground types; one sitting below 0 m or below a stated above-ground level is flagged as a possible basement, not reclassified. Walk-out (partially exposed) basement walls are out of scope.
