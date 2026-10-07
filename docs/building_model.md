# Canonical Building Model + Cross-Sheet Linker

Date: 2026-09-19. Status: prototype validated on synthetic multi-discipline
buildings. Real detector front-ends are not yet wired.

## What this is

The architectural floor plan is the **canonical reference frame**. Every
other discipline sheet (lighting plan, mechanical plan, elevations) is
*registered into* architectural coordinates, and every cross-discipline
link is an auditable record: sheet ID + revision, source bbox, method,
confidence. Links below **0.80 confidence** go to a review queue — they
are flagged, never silently accepted or dropped.

**Rooms/spaces are the primary entities** (`L1-101`; number is stable,
name is human-facing). Zones ↔ spaces are **many-to-many** (an open
office can span two AHUs/VAVs; a VAV can serve several rooms).

## Files

| File | Role |
|---|---|
| `building_model.py` | Versioned canonical schema: `BuildingModel`, `Space`, `Zone`, `Level`, `EnvelopeWall`, `SpaceOpening`, `FixtureInstance`, `ComponentRef`, `Provenance`, review queue, revision log, JSON round-trip. |
| `registration.py` | `Affine2D` plan-sheet registration from title-block origin+scale; facade elevation registration via **grid bubbles** (0.95 conf) or **geometric fallback** from a facade reference corner + scale (0.65 conf, always review-flagged); point-in-polygon; along-wall interval-overlap matching. |
| `link.py` | The linker. Ingests a building's sheets, builds the model, computes rollups. |
| `roof_geometry.py` | Tilt, azimuth, sloped and plan area of planar roof facets (`RoofPlane`, roadmap item 2). Newell normal; non-planar facets rejected, never fitted. |
| `synth/multidiscipline.py` | Coordinated synthetic building generator (arch + lighting + mech + two south elevations, one gridded, one not; opt-in `north_elevation=True` adds two north elevations) with ground truth. |
| `run_multidiscipline.py` | Validation harness: 3 buildings × 2 elevation paths, scored against GT. |

## Linking pipeline (link.py)

1. **Arch plan** → `Space` per room (polygon in meters, name, number, area,
   volume), 4 envelope wall runs from the footprint. Revision logged.
2. **Lighting plan** → title-block origin+scale registers sheet px → m;
   fixtures assigned to spaces by **point-in-polygon**; tag joined to the
   lighting schedule (tag → watts); per-space total watts and LPD
   (W/m² and W/ft²). Unassigned fixtures and schedule misses → review.
3. **Mech plan** → sheet registered; components → canonical `ComponentRef`s;
   **zones attach by diffuser positions only** — no cross-sheet id matching.
   A zone's spaces = union of its diffusers' spaces; each space's
   `zone_ids` = zones containing its diffusers. Terminal units (VAVs),
   sensors, and duct-run length ride on the zone. Many-to-many is
   emergent, not special-cased.
4. **Elevation** → facade registration (grid path preferred, geometric
   fallback required), window u-intervals → facade meters → south wall
   segments (one per room touching the facade) → owning room; tag joined
   to the window schedule for dims; per-space window area. Ambiguous spans
   (window crosses a room boundary) attach to the majority-overlap room
   and are flagged; windows matching no segment become unlinked review
   items — nothing is silently dropped.

## Validation (2026-09-19)

`python3 run_multidiscipline.py` — 3 buildings (3/5/8 rooms, incl. one
open-office-spanning-two-zones case), each linked twice (grid + geometric):

| Link | Grid path | Geometric path |
|---|---|---|
| fixture→room | 58/58 | 58/58 |
| sensor→room | 17/17 | 17/17 |
| diffuser→room | 17/17 | 17/17 |
| diffuser→zone | 17/17 | 17/17 |
| zone→spaces (set) | 6/6 | 6/6 |
| space→zones (set) | 16/16 | 16/16 |
| window→room | 12/12 | 12/12 |
| LPD rollup exact | 3/3 bldgs | 3/3 |
| window-area rollup exact | 3/3 | 3/3 |
| JSON round-trip | 3/3 | 3/3 |

Confidence behavior: grid window links 0.95 → no review items; geometric
0.65 → **all flagged for review** (threshold 0.80). Boundary-straddling
windows are marked ambiguous; off-facade intervals unlinked-but-reported.
Revision supersession verified: old facts retained in history, new
revision logged.

## Roof planes (roadmap item 2, #613)

`BuildingModel.roof_planes` holds sloped roof facets as `RoofPlane`s: 3-D
corners in the canonical frame (plan x east, plan y down the sheet, z up from
the level floor), `tilt_deg` from horizontal, `azimuth_deg` as the compass
bearing of the upward normal (clockwise from north, north being -y), and the
true sloped `area_m2`. A flat facet has tilt 0 and azimuth None.

The list is empty unless something populated it, and empty means the flat
roof at wall height every export used before. Nothing reads it yet: IFC import
(#614), the solar-aperture metric (#615), the roof simplifier (#616),
validation (#617) and the gbXML/IFC exports (#618, #619) land in later waves.

`roof_geometry.plane_orientation` uses Newell's method, so winding and vertex
count do not matter. Corners more than 5 mm off one plane raise
`RoofGeometryError`: a warped facet is a modelling error to report, not a
plane to fit.

## Design decisions recorded

- **Versioned JSON first** (diffs cleanly); gbXML/IFC export reads from
  this model.
- **New revisions supersede, never silently erase** (`supersede()` keeps
  the old fact in history + logs the event).
- **One elevation per model run.** Two elevations of the same facade are
  linked in separate runs; cross-sheet observation dedup (two sightings,
  one window) is now implemented in elevation_windows.py
  (merge_elevation_observations: interval-overlap merge, conflicts
  flagged) -- see docs/elevation_windows.md.
- **Confidence is nominal, not calibrated.** 0.95/0.65 are engineering
  judgments; calibrate against measured front-end errors before trusting
  the review threshold.
- **Synthetic realism limits.** Mechanical sheets are filled-bar ducts, not
  double-line; no risers, leaders, or overlapping systems. Real duct
  tracing is the hard part and remains open (see hvac_trace.py status).
- **All four facades** link (#699): each elevation sheet's `facade` meta picks the
  wall segments (`link._elevation.wall_segments`). `generate_building(...,
  north_elevation=True)` adds north elevations drawn as seen from the north, so
  the mirrored grid and no-grid paths are tested end to end (#702).
- **Arch-plan windows** (if drawn) are not yet reconciled against
  elevation windows — same dedup problem as above.
- **Space.zone_ids via diffusers, sensors co-located.** A sensor in a
  spanning room links to the space; its zones come from the space, not a
  single-zone label.
- `window_area_m2` counts gross schedule area per window; IFC/gbXML export
  should reconcile openings against opaque wall area before simulation
  (see bem_export.py gaps).

## Open questions for Alex

1. Review threshold 0.80 and nominal confidences — acceptable until we
   calibrate, or should fallback links block export instead of flagging?
2. Multi-storey + risers: is the zone model per-level with riser links,
   or a true 3D zone graph?
  3. **RESOLVED (Issue #11) — Observation reconciliation via typed-decision
     candidates**: The dedup rule uses `same_object` + `prefer` adjudication.
     `assign_window_tag_candidates()` returns all schedule tags matching the
     measured window size within 0.15 m. `adjudicate_tag()` resolves
     conflicts using a configurable `prefer` strategy:
       - `"grid"` (default): prefer grid-registered observations; ties go to nearest size
       - `"geometric"`: prefer geometric-registered observations
       - `"keep_both"`: if candidates disagree on identity (different tags),
         return both as a comma-joined string (e.g. `"A,B"`); if same tag, return it
       - `"nearest"`: original behavior — closest by size distance only
     See `elevation_windows.py::adjudicate_tag` for the full implementation.
     Non-room polygons (shafts, closets, elevator_cores) are classified by
     `polygon_classify.py` using area, aspect ratio, label text, and
     adjacency evidence, and excluded from area rollups before computation.
4. Interior partitions are still omitted from BEM export; do you need them
   for zone adjacency, or is space→zone mapping sufficient for now?

## Limitations

- **Cross-sheet linkage is probabilistic**: `link.py` uses room label matching and polygon adjacency, but does not parse actual door swing or connectivity annotations. Spaces separated by an unlabeled partition may not be linked.
- **No multi-storey vertical reasoning**: The linker treats each floor as an independent graph; stairwell and riser connections are not yet modeled.
- **Polygon classification is heuristic**: `polygon_classify.py` excludes shafts, elevator cores, and closets by area/aspect/label heuristics; unusual room shapes or mislabeled spaces may be misclassified.
- **Confidence is not calibrated**: The 0.80 threshold and nominal confidence values have not been validated against a corpus of real drawings.
- **No zone boundary reconciliation**: Adjacent spaces assigned to different zones are not checked for thermal boundary consistency.

## Solar-weighted roof aperture (roadmap item 2, #615)

`solar_aperture.py` gives the one number the roof simplifier (#616) and the
roof checks (#617) use to judge a roof by what BEM will see, not by area
alone: the sum over sun positions of area × max(0, cos incidence).

- Sun positions are fixed so the result is deterministic: every hour on the
  hour (solar time) on 21 Mar, 21 Jun, 21 Sep and 21 Dec, sun above the
  horizon only. Declinations are fixed at 0, +23.44, 0, -23.44 deg; there is
  no equation of time and no refraction. The unit is m² × sun position, for
  comparing roofs at one latitude, not an energy figure.
- No default latitude. Pass one, or use `BuildingModel.site_latitude_deg`,
  which IFC import fills from `IfcSite.RefLatitude` (decimal degrees, north
  positive). `model_aperture` raises when neither gives one.
- `by_orientation` splits the total into flat, N, E, S and W (90° quarters
  centred on the compass points), so a check can say which side changed.
- `facet_aperture` works from a facet's corners via the Newell normal, so it
  does not depend on how the facet is triangulated or wound.

## Roof simplification (roadmap item 2, #616)

`roof_simplify.simplify_roof(planes, latitude_deg)` is the roof mode of
geometry simplification. It merges adjacent facets greedily, the merge that
moves solar aperture least first, under two hard budgets measured against the
original roof: true sloped area (`area_tol`, default 2%) and solar aperture
(`aperture_tol`, default 2%, from `solar_aperture.py`).

- Candidates: same level, a shared plan edge of at least 0.1 m on which both
  planes agree in height within 0.05 m (stepped roofs stay apart), and either
  normals within `angle_tol_deg` (default 5°) or the smaller facet at most
  `small_facet_frac` (default 5%) of the roof area, so dormers and small
  hip ends can be absorbed by their host.
- The merged plane covers the plan union, with the area-weighted mean normal,
  through the area-weighted centroid. A union that is not one polygon, or
  that would leave a hole, is refused.
- A merge past either budget is refused and listed in `skipped` with both
  deltas. Hip ends stay separate by default: folding an east end into the
  south side costs about 2.7% area and 1.3% aperture at 40° N.
- Near-coplanar merges keep the outline, so aperture moves far less than area.
- The input list is never changed. The result holds new planes (merged ones
  carry `roof_simplify:merge` provenance naming their sources) and
  `roof_simplify_report()` gives both deltas, every merge and every refusal.
  Output and report are the same for any input order.

## gbXML export of sloped roofs (#618)

When the model carries at least one tilted roof plane, `write_gbxml` writes:

- One `Roof` surface per plane, clipped to the envelope ring, with its true 3-D
  PolyLoop (CCW from above, outward normal up) and its tilt and azimuth in
  `RectangularGeometry`.
- Walls whose top meets the roof as a real outline: a wall under a gable end
  gets a peaked `PlanarGeometry` loop, a wall under an eave keeps its
  rectangle. Wall openings stay under the wall's lowest top point.
- Each space's `ClosedShell` built from floor, roof-following walls and the
  roof pieces over it, and its `Volume` from that closed shell
  (`bem_roof.shell_volume`), not area x wall height.
- Skylights laid out one roof surface at a time, so each sits wholly on one
  plane, lifted onto that plane.

`bem_volume_conservation` now compares the space volumes against the ring's
closed shell under the roof planes. Models with no roof planes, or only flat
ones, export byte for byte as before.

Known gaps: roof overhang past the walls is dropped (noted in the file
comment), not written as shading; the BEM writers are single-storey, so roof
planes on upper levels are skipped with a note. Roof planes are top-of-roof
faces, so shells run to the outside of the roof.

## IFC export of sloped roofs and the round trip (#619)

When the model carries at least one tilted roof plane, `write_ifc4` writes one
`IfcRoof` (contained in the storey) that aggregates one `IfcSlab` ROOF per
plane. Each slab is a prism whose top face is the plane's full outline
(overhang included) and which runs `ROOF_SLAB_THICKNESS_M` down along the
plane normal, so `ifc_roof_planes` reads back the same plane. The roof U goes
on every slab as `Pset_SlabCommon.ThermalTransmittance`.

Skylights on a sloped roof use one shared layout (`bem_roof.place_skylights_on_pieces`)
in both writers: one plane at a time, largest first, each skylight a true
width x height rectangle in its plane (width along the eave, height up the
slope). This replaces #618's lift of the plan rectangle, which stretched
skylight area by 1/cos(tilt) on the slope. In IFC each gets a void through its
slab and an `IfcWindow` SKYLIGHT in the plane's frame; on import
`orient_skylights` gives it that plane's tilt and azimuth.

Export, import, export again gives the same planes: count, tilt, azimuth,
area and canonical vertices (gable, hip and shed tested). Models with no
roof planes, or only flat ones, export as before.
