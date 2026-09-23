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
3. **Placeholder constructions** — generic U-values; real assemblies need
   spec/schedule input.
4. **Opening positions are synthetic** (counts are real, positions are
   apportioned) — true positions need elevation-view parsing.
5. **IFC roof/slab missing** — walls + spaces + openings only; roof as
   `IfcSlab`/`IfcRoof` is straightforward to add.
6. **IfcSpace has no solid geometry** in v1 (placement + quantities only).
7. Window/door opening areas are not yet reconciled against the simplified
   envelope (double-count risk noted in `docs/geometry_simplify.md`).
