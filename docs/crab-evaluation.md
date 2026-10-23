# CRAB cuboid BIM: evaluation against Matchline's actual code (#1257)

Issue #1257 asks whether the CRAB cuboid approach (arXiv:2610.29257,
*CRAB: Cuboid-based Representation for As-built BIM from Point Clouds*) is a
better geometry-simplification substrate for Matchline. This document answers
the issue's four questions against the code in this repository, not in the
abstract.

## How to read this document

- **What was verified by reading code**: every statement about Matchline cites
  the file and the function/constant it came from.
- **What was verified from the paper**: the CRAB claims below are quoted from
  the arXiv abstract and the paper body (sections 3, 4.1, 4.4, 5.4) of
  arXiv:2610.29257. They are cited as *paper §x.y* / *abstract*.
- **What was not verified**: no CRAB code, model weights, or CrabSet data exist
  in this repository, and CRAB cannot be run here. **This document therefore
  contains no benchmark numbers of our own** — no measured CRAB accuracy, no
  measured Matchline-vs-CRAB comparison, and no timings. The only quantitative
  figures are (a) the ones the issue and the paper state (2.80 cm mean
  interior-wall thickness error, 79.50 % of matched walls within 3 cm —
  *abstract*), and (b) constants measured from Matchline's own source, always
  cited with file and line.
- Where the paper is silent, this document says **"not addressed in the
  source"** and does not speculate.

## What CRAB actually is (from the source)

CRAB reconstructs as-built BIM from point clouds as a set of **semantic
cuboids** with explicit relations (paper §4.1). Each cuboid's geometry is
`g_i = [x1, y1, z1, x2, y2, z2, w, h]`: the **bottom central axis** connecting
two endpoints, plus a thickness `w` and a height `h`, with a semantic category
(paper §4.1). Components covered are **walls, doors, windows, beams and
columns** (abstract; §4.1). Walls, doors and windows are the volumetric
components; the representation is deliberately *centreline-based* rather than a
generic oriented box, because "for long and thin walls, the centerline-based
cuboid explicitly separates wall extent from wall thickness" (paper §5,
ablation "Effect of Centerline-based Parameterization").

The pipeline has three steps (paper §4.4):

1. **ProposalNet** predicts coarse semantic cuboids from the point cloud.
2. **AssemblyNet** refines geometry and predicts relations (wall adjacency,
   door/window affiliation) via relation-aware Flow Matching.
3. **BIM conversion**: cuboids and relations become editable IFC components.
   For walls, adjacency relations resolve junctions by trimming or extending
   each wall **along its longitudinal axis only**, preserving direction,
   thickness and centreline offset. The wall-adjacency graph is then searched
   for **closed cycles of at least four wall segments**, which are assembled
   into **rooms bottom-up**, and can be further converted into 2D floor plans
   or 3D planar representations. Door/window affiliation places openings on
   their host wall (paper §4.4, Step 3).

CRAB's headline number, per the abstract, is a **mean interior-wall thickness
error of 2.80 cm**, with **79.50 % of matched walls within 3 cm**
(`T-Acc@3`); cuboid-based AssemblyNet lifts that to 81.27 % (paper §5,
Table 4). The issue quotes the same 2.8 cm figure.

---

## Q1. Can CRAB cuboids feed directly into the area-gated simplifier?

**No. Not directly — a cuboid set is not a valid input, and the adapter is
not a one-liner.**

### What the simplifier accepts

`geometry/geometry_simplify.py` is strictly 2-D and strictly ring-shaped:

| Function | Line | Input contract |
|---|---|---|
| `footprint_from_regions(polygons)` | `geometry/geometry_simplify.py:54` | list of vertex lists (`polygon_px`-style floor polygons) |
| `envelope_area(ring, wall_height=None)` | `geometry/geometry_simplify.py:84` | one closed ring of `(x, y)` vertices |
| `simplify_ring(ring, tol=0.02, min_feature=None, wall_height=None, dp_eps=None, max_single_step=0.0025)` | `geometry/geometry_simplify.py:192` | one closed ring of `(x, y)` vertices |
| `footprint_parts_from_regions(polygons)` | `geometry/geometry_simplify.py:494` | list of vertex lists → `FootprintPart`s (wings + courtyard holes) |
| `simplify_footprint(parts, tol=0.02, wall_height=None, **kwargs)` | `geometry/geometry_simplify.py:576` | `FootprintPart`s |
| `simplify_report(res)` | `geometry/geometry_simplify.py:459` | `SimplifyResult` → audit dict |

The area gate, as implemented:

- `tol` is the total allowed relative area change, default **0.02 (2 %)**,
  documented as **"Use 0.01-0.05"** (`geometry/geometry_simplify.py:205`,
  default at `:194` and `:182`).
- The budget test is `if (removed_area + c) / orig_area > tol:` — a removal is
  skipped and logged when it would breach the budget
  (`geometry/geometry_simplify.py:315`).
- A per-removal cap blocks single large bites: `max_single_step: float =
  0.0025` (0.25 % of envelope area), `min_feature` overrides it
  (`geometry/geometry_simplify.py:198`, docstring `:208`).
- A hard backstop refuses any net area **growth** above `MAX_GROWTH = 1e-3`
  (0.1 %), rolling the ring back (`geometry/geometry_simplify.py:402-403`).
- Concave (reflex) vertices are never removed, because their removal would
  *grow* the polygon (`geometry/geometry_simplify.py:334-335`).
- The area metric is `2.0 * footprint_area + perimeter * wall_height` when a
  wall height is given, else footprint area alone
  (`geometry/geometry_simplify.py:84-95`). **There is no wall-thickness term
  anywhere in the gate.**
- Downstream, BEM export refuses models simplified above **3 %**
  (`MAX_SIMPLIFY_TOL = 3.0` in `bem/bem_geometry.py:330-336`).

### What a CRAB cuboid set is

A list of volumetric components: a centreline segment in 3-D, a thickness, a
height, a semantic category, plus relations. Not a closed planar ring, not a
set of floor polygons, and not 2-D at all.

### The adapter a cuboid intermediate would need

1. **Projection to plan.** The simplifier is 2-D (`(x, y)` vertices only;
   `geometry/geometry_simplify.py:220` casts every vertex to a float pair).
   Wall cuboids must be projected to their centreline segments in plan.
2. **Ring reconstruction.** The simplifier needs a *closed* ring per connected
   part; CRAB supplies *open* wall segments plus an adjacency graph. The
   adapter must rebuild closed rings — this is exactly the closed-cycle search
   CRAB itself performs in paper §4.4, Step 3 ("closed cycles containing at
   least four wall segments"). Matchline has no such routine today: its
   ring comes from the union of space polygons
   (`footprint_parts_from_regions`, `geometry/geometry_simplify.py:494`),
   driven from `run_pipeline.py:716-717`.
3. **A decision on thickness.** The gate has no thickness term
   (`envelope_area`, `geometry/geometry_simplify.py:84-95`): wall area is
   perimeter × height. CRAB's per-wall thickness `w` (with its cited 2.80 cm
   mean error) can only enter as a *pre-offset* of the ring (e.g. offsetting
   the centreline ring inward by `w/2` before simplification). That offset is
   itself an area change the gate would be measuring, so the adapter must
   state which ring — centreline or offset face — the budget applies to.
4. **Heights.** `wall_height` is a single scalar argument
   (`simplify_ring(..., wall_height=None)`, `geometry/geometry_simplify.py:196`);
   CRAB carries a height per cuboid, so a multi-storey or varying-height
   building needs the per-part path (`simplify_footprint`,
   `geometry/geometry_simplify.py:576`), not the scalar.
5. **Openings are out of scope for the gate.** Doors/windows are separate
   cuboids; the simplifier never sees them (documented gap: "No
   window/door area reallocation" in `docs/geometry_simplify.md`). They would
   have to be handled by the separate takeoff path, exactly as today.
6. **Units.** CRAB cuboids are metric; the drawing pipeline works in pixels
   and converts via `takeoff.scale.m_per_px` (`bem/bem_geometry.py:241`,
   `:268`). The gate is unit-agnostic (relative), but the resulting ring must
   be metres before it reaches `BEMModel.ring_m` (`bem/bem_geometry.py:286`).

### The deeper mismatch

Even with the adapter, the gate would be measuring the wrong thing. The
simplifier's unit of work is a **plan ring of the building footprint**; its
output is a ring whose edges become BEM wall surfaces (one surface per ring
edge, `bem/bem_export.py:501-573`). CRAB's unit of work is a **volumetric
component**. A CRAB wall set is *already* one surface per component — i.e. it
is the output of simplification, not its input. Feeding cuboids into
`simplify_ring` would mean simplifying a footprint that was reconstructed from
those same cuboids: the gate would then police the round-trip error of the
adapter, not reduce the surface count of the source model.

---

## Q2. Does the cuboid representation preserve the space boundaries BEM needs?

**Partly. CRAB supplies the two ingredients Matchline's envelope model wants
(wall centreline + thickness) but not the ingredient the BEM writer actually
consumes (space polygons).**

### What BEM export actually consumes, item by item

The BEM writers take a `BEMModel` (`bem/bem_geometry.py:150-236`). The
geometry that matters for space boundaries is:

| # | Data the BEM path needs | Where it comes from |
|---|---|---|
| 1 | **Per-space plan polygons** (`BEMSpace.polygon_m`, CCW metres) | `bem/bem_geometry.py:12-49` (field), built from labelled spaces at `bem/bem_geometry.py:268` |
| 2 | **Envelope ring(s) in metres** (`BEMModel.ring_m`, `BEMModel.rings_m`) | `bem/bem_geometry.py:150-236`; from the simplifier result at `bem/bem_geometry.py:286` |
| 3 | **Space ↔ exterior-wall association** | *Computed*: point-in-polygon with a 0.3 m inward nudge, centroid fallback — `_assign_wall_to_space`, `bem/bem_helpers.py:43`; per-edge owner list at `bem/bem_helpers.py:113` |
| 4 | **Interior walls between two spaces** | *Computed* from shared space-polygon boundaries — `shared_walls`, `bem/bem_space_split.py:125`; written as `InteriorWall` surfaces at `bem/bem_export.py:719-737` |
| 5 | **Openings with dimensions, count, and (optionally) host space/facade/sill** | `BEMOpeningUnit` (`bem/bem_geometry.py:50-86`); expanded from schedule lines at `bem/bem_geometry.py:290-326`; placed on ring edges by `_distribute_openings`, `bem/bem_helpers.py:121` |
| 6 | **Floors / roofs / grade** | per-space polygon floors (`bem/bem_export.py:697-712`), roof planes (`bem/bem_roof.py`), terrain grade split (`bem/bem_export.py:525`) |
| 7 | **Space tiling check** | `covers_footprint` — the union of space polygons must tile the footprint within tolerance (`bem/bem_space_split.py:102-122`) |
| 8 | **Thermal attributes** | per-space wall U-value (`BEMSpace.wall_u_value_w_m2k`, `bem/bem_geometry.py:22`), constructions, opening U/SHGC (`bem/bem_geometry.py:182-186`) |
| 9 | **Loads / lighting / zones / HVAC** | `BEMSpace.people_per_m2` etc., `BEMModel.zones`, `BEMModel.zone_terminals` — not geometry |

At the canonical-model level the same picture holds: `building_model.Space`
carries `polygon_m` (canonical y-down metres) and `openings`
(`building_model.py:324-336`); `SpaceOpening` carries `host_facade`,
`host_interval_m`, `s_center_m`, `sill_m`, `head_m`
(`building_model.py:138-154`); `EnvelopeWall` is a centreline run —
`from_m`, `to_m`, `length_m`, `height_m`, `area_m2`, `construction_id`,
`space_id` (`building_model.py:593-610`); `BimElement` carries `thickness_m`
and `material_layers` (`building_model.py:658-683`).

### CRAB's cuboid output judged against that list

| # | Requirement | CRAB provides | Verdict |
|---|---|---|---|
| 1 | Space plan polygons | **Rooms are not cuboids.** Rooms are derived bottom-up from closed cycles of ≥ 4 wall segments in the wall-adjacency graph (paper §4.4, Step 3) and "can be further converted into 2D floor plans or 3D planar representations". | **Partial.** A room-derivation step exists in the paper but is not part of the cuboid contract, and the paper does not publish a room-polygon schema. Matchline would have to re-derive it. |
| 2 | Envelope ring in metres | Wall centrelines + thickness + height, with junctions resolved by trimming/extending along the longitudinal axis (paper §4.4, Step 3) | **Yes.** This is the strongest fit in the whole evaluation. |
| 3 | Space ↔ wall association | Derived from rooms (item 1) | **Partial**, inherits item 1. |
| 4 | Interior walls | Wall components exist; which are interior vs. exterior is decided by the adjacency graph | **Partial.** Matchline derives interior walls from *shared space-polygon boundaries* (`bem/bem_space_split.py:125-164`), so it still needs item 1's room polygons. |
| 5 | Openings (dims, host wall) | Door/window cuboids with explicit **opening–wall affiliation** relations (paper §4.1, §4.4) | **Yes — richer than Matchline's current path.** Matchline's openings come from schedule lines with dimensions, and are *placed* on ring edges by length apportionment (`bem/bem_helpers.py:95-121`); a CRAB affiliation gives an explicit host wall and along-wall position, which is what `SpaceOpening.host_interval_m` / `s_center_m` (`building_model.py:149-150`) wants. |
| 6 | Floors / roofs / grade | Not in CRAB's component set (walls, doors, windows, beams, columns — abstract, §4.1) | **No.** Matchline derives floors from space polygons and roofs from its own roof geometry (`bem/bem_roof.py`). |
| 7 | Space tiling check | n/a | **Depends on item 1.** |
| 8 | Thermal attributes | Wall **thickness** is geometric only; no material layers, no U-values | **No.** Matchline wants a construction per space (`BEMSpace.wall_u_value_w_m2k`, `bem/bem_geometry.py:22`); CRAB's 2.80 cm thickness error is a geometric error, not a construction. |
| 9 | Loads / zones / HVAC | Not modelled | **No** (out of scope for CRAB). |

**Summary.** The cuboid representation preserves the *envelope* side of the
space-boundary data (items 2, and partly 3/4) and improves the *opening* side
(item 5). It does **not** preserve the *space* side (item 1), which is the
side Matchline's BEM writer is built around: exterior walls are attached to
spaces by point-in-polygon against space polygons
(`bem/bem_helpers.py:43`), interior walls are found by intersecting two space
polygons (`bem/bem_space_split.py:152-163`), and floors are per-space polygons
(`bem/bem_export.py:697-712`). A cuboid-only intake therefore forces Matchline
to reconstruct room polygons before the BEM path can run at all — exactly the
room-assembly step the paper describes, but which Matchline does not have today.

---

## Q3. How does CRAB handle non-rectangular rooms and angled walls?

**Angled walls: representable by construction; the paper's own limitation
section caps the guarantee. Non-rectangular rooms: derived, not modelled —
the paper's treatment is not addressed in the source.**

### Angled walls

- A wall cuboid is defined by a 3-D centreline plus thickness and height
  (`g_i = [x1, y1, z1, x2, y2, z2, w, h]`, paper §4.1). A wall at an arbitrary
  plan angle is therefore representable; nothing in the parameterisation
  forces axis alignment.
- CRAB's data does contain non-Manhattan material: CrabSet is "derived from
  Structured3D, which provides diverse residential layouts with room
  partitions, openings, non-Manhattan structures, and occlusions" (paper §3).
- The paper's own limitation (2), "Irregular non-Manhattan geometry", states:
  *"While CRAB handles standard Manhattan layouts and moderate non-Manhattan
  variations, highly irregular or curved structures remain challenging:
  cuboids can capture the dominant organization, but may oversimplify
  fine-scale deviations."* (paper §5.4)
- Future work is listed as "complex non-Manhattan geometry and richer
  primitives, including cylinders and curved surfaces" (paper §6).
- The paper reports that the cuboid representation is *more* robust than
  plane-based refinement in "incomplete, ambiguous, or non-Manhattan scenes",
  where floor-plan methods depend on room closure (paper §5, "Robustness to
  Ambiguity and Local Perturbations").

**How the paper handles the *interaction* of angled walls with room assembly
(the closed-cycle search over wall segments) is not addressed in the source**:
the paper does not say whether the cycle search requires orthogonal walls,
whether it validates cycle convexity, or how it handles a room whose walls
are not axis-aligned.

### Non-rectangular rooms

- Rooms are not cuboid objects at all. They are assembled bottom-up from
  closed cycles of ≥ 4 wall segments in the wall-adjacency graph, and then
  optionally converted to 2D floor plans (paper §4.4, Step 3). A cycle of five
  or more segments, or of non-orthogonal segments, is not excluded by the
  stated rule.
- **The paper does not address** room-shape constraints, room-area
  validation, self-intersection handling in the cycle search, or how
  L-shaped/angled rooms are represented in the IFC output. The only room-level
  statement is that CRAB outputs "are converted into 2D room polygons and 3D
  planes for evaluation" (paper §5, evaluation protocol).
- For CRAB's *evaluation*, the paper reports "notably higher Corner F1 and
  Angle F1 than CAGE, demonstrating more accurate boundary geometry" against
  RoomFormer's native 2D layout metrics (paper §5). These are the paper's
  numbers; no equivalent measurement exists for Matchline's own geometry path
  in this repository.

### Consequence for Matchline

Matchline's own simplifier is likewise footprint-polygon based and refuses to
remove reflex vertices (`geometry/geometry_simplify.py:334-335`), and its
documented hard cases include oblique/shallow-angle boundaries, where the
Douglas-Peucker pass is refused and logged when it would breach `tol`
(`docs/geometry_simplify.md`, "Hard-case coverage"). So Matchline and CRAB
share a weakness: both are weakest exactly where the geometry is irregular.
CRAB's cuboids would, per the paper's own limitation (2), "oversimplify
fine-scale deviations" in exactly those cases — and unlike Matchline's
simplifier, that oversimplification is **not** subject to an area gate.

---

## Q4. IFC compatibility — can Matchline export CRAB-style cuboids as an intermediate format?

**Yes for the *export* side (Matchline can write cuboid-shaped walls today,
because a CRAB cuboid is exactly the `IfcWall` shape Matchline already reads);
no for the *round-trip* side without extra work (Matchline's writer emits one
`IfcWall` per simplified-ring edge, and its reader explicitly ignores
`IfcRelSpaceBoundary`).**

### What Matchline's IFC writer produces today

`ifc/ifc_export.py` is a thin adapter: `BuildingModel → BEMModel`
(`_bem_from_model`, `ifc/ifc_export.py:87`) and then `write_ifc4`
(`ifc/ifc_export.py:385-424`) — all IFC geometry is written by
`bem/bem_ifc4.py`. Entities created there:

| Entity | Where | Notes |
|---|---|---|
| `IfcProject` / `IfcSite` / `IfcBuilding` / `IfcBuildingStorey` | `bem/bem_ifc4.py:157-219` | spatial hierarchy |
| `IfcWall` | `bem/bem_ifc4.py:266` | one per **ring edge**, body via `_Gm.add_wall_representation` with `length`, `height`, `thickness=wall_thickness_m`, `offset=-wall_thickness_m/2` (`:274-280`) — i.e. the wall body is **centred on the ring centreline** |
| `IfcOpeningElement` + `IfcWindow`/`IfcDoor` | `bem/bem_ifc4.py:310-370` | voided into the host wall, `IfcRelVoidsElement` + `IfcRelFillsElement` |
| `IfcSpace` | `bem/bem_ifc4.py:674` | placement at centroid; **no solid body** — the footprint survives as a 2-D `IfcGeometricCurveSet` (`:695-700`) |
| `IfcRelSpaceBoundary` | `bem/bem_ifc4.py:805` | for air walls (virtual boundaries) |
| `IfcRelSpaceBoundary2ndLevel` | `bem/bem_ifc4.py:884-951` | per wall part, `EXTERNAL`/`EXTERNAL_EARTH` |
| `IfcShadingDevice` | `bem/bem_ifc4.py:586-607` | shading plates |

A CRAB cuboid (centreline + thickness + height) maps onto `IfcWall` with an
`IfcRectangleProfileDef` swept along the centreline — the same representation
Matchline already writes and already reads.

### What Matchline's IFC reader accepts

`ifc/ifc_import.py` is "Tier 0": it reads "everything recoverable **without**
`IfcRelSpaceBoundary`" (docstring, `ifc/ifc_import.py:3-4`; restated at
`:3788`). It reads:

- element classes `IfcWall`, `IfcWallStandardCase`, `IfcSlab`, `IfcRoof`,
  `IfcColumn`, `IfcBeam`, `IfcCurtainWall`, `IfcDuctSegment`,
  `IfcDistributionElement` (`_ELEMENT_CLASSES`, `ifc/ifc_import.py:1228-1239`)
  — so CRAB's beams and columns would be imported as `BimElement`s;
- wall geometry from extruded `IfcRectangleProfileDef` solids
  (`_rect_profile_dims`, `ifc/ifc_import.py:252`; `_rect_profile_footprint`,
  `:260`);
- wall **thickness** from `IfcMaterialLayerSet` via `_material_layers`
  (`ifc/ifc_import.py:1157`), with the body centreline preferred over the
  layer-set usage (`_wall_centreline_fix`, `ifc/ifc_import.py:386`);
- spaces from `IfcSpace` geometry (`_extract_space_geometry`,
  `ifc/ifc_import.py:1431`);
- openings via `IfcOpeningElement` + `IfcRelVoidsElement` + `IfcRelFillsElement`
  (docstring `ifc/ifc_import.py:12-15`).

### Judgement

- **Export of CRAB-style cuboids: feasible with the existing entity set.**
  The blocker is not the IFC schema but Matchline's writer: `write_ifc4`
  iterates ring edges (`edges = _wall_edges(model.ring_m)`,
  `bem/bem_ifc4.py:243`) and emits one uniform-thickness wall per edge. To
  emit CRAB-style cuboids, Matchline would need a second wall-writing path
  that takes a component list (centreline, per-wall thickness, per-wall
  height) instead of a ring. No new IFC entities are needed.
- **Import of a CRAB-produced IFC: partially covered.** A CRAB file's walls
  would import cleanly (rect-profile extrusion + material layer set for
  thickness). But Matchline **never reads `IfcRelSpaceBoundary`**
  (`ifc/ifc_import.py:3-4`, `:3788`); it reconstructs space boundaries
  geometrically (virtual borders in `ifc/ifc_space_borders.py:83`,
  `find_virtual_borders`). So any space-boundary information CRAB wrote into
  IFC would be discarded on import, and Matchline's BEM path would re-derive
  boundaries from space polygons — which brings us back to Q2, item 1.
- **What CRAB itself writes into IFC is not addressed in the source.** The
  paper states that refined cuboids "are converted into editable IFC
  components", with "corner-joint, cross-joint, or wall-integrated wall
  structures" (paper §4.4, Step 3), and that the representation has
  "IFC-compatible semantics and parameters" (abstract). The paper does not
  name the IFC entity classes used, does not state whether `IfcSpace` or
  `IfcRelSpaceBoundary` are emitted, and does not publish an IFC schema
  mapping. **Not addressed in the source** — do not assume CRAB's IFC carries
  space boundaries.

---

## Verdict

### Adopt partially.

**Adopt:** the CRAB *centreline-based cuboid* as an **as-built intake
geometry contract**, i.e. a wall component carrying an explicit centreline,
an explicit thickness, a per-component height, and an explicit
opening→host-wall affiliation. This maps cleanly onto Matchline's canonical
model, which already stores exactly those fields: `EnvelopeWall.from_m/to_m`
(`building_model.py:600-601`), `BimElement.thickness_m` /
`material_layers` (`building_model.py:672-675`), `SpaceOpening.host_interval_m`
/ `s_center_m` (`building_model.py:149-150`), and `Space.polygon_m`
(`building_model.py:329`). It is also the natural IFC shape: `IfcWall` with an
`IfcRectangleProfileDef` extrusion, which Matchline's importer already
consumes (`ifc/ifc_import.py:252`, `:260`, `:1157`).

**Do not adopt:** CRAB cuboids as the **input to the area-gated simplifier**.
The simplifier's contract is a 2-D closed footprint ring
(`geometry/geometry_simplify.py:54`, `:192`, `:494`, `:576`); its gate is a
relative envelope-area budget of 1–5 % (`geometry/geometry_simplify.py:205`)
with a 0.25 % per-step cap (`:198`) and a 0.1 % growth backstop (`:402`).
A cuboid set is not a ring, and a cuboid set is already the *output* of
surface-count reduction, so the gate would be policing the adapter rather
than the model. Concretely: the CRAB path should feed the **footprint ring
reconstructed from cuboids** into the existing simplifier, exactly as
today's pipeline does from space polygons (`run_pipeline.py:716-717`).

**Also not adopted:** replacing Matchline's space-polygon contract with
cuboids for the BEM path. The BEM writer needs space polygons
(`bem/bem_geometry.py:12-49`), derives interior walls from shared space
polygon boundaries (`bem/bem_space_split.py:125`), attaches exterior walls by
point-in-polygon (`bem/bem_helpers.py:43`), and tiles-checks spaces against
the footprint (`bem/bem_space_split.py:102`). CRAB's rooms are derived, not
carried (paper §4.4, Step 3), so a cuboid intake would need Matchline to
re-implement CRAB's room-assembly step before BEM export can run at all.

### Risks that stay on the table

- CRAB's own limitation (2): cuboids "may oversimplify fine-scale deviations"
  in irregular/curved geometry (paper §5.4) — with **no area gate** to catch
  it, unlike Matchline's simplifier.
- The cited 2.80 cm mean thickness error (abstract) is a geometric error in
  CRAB's output; it does not substitute for the construction/assembly data
  Matchline's BEM path needs (`BEMSpace.wall_u_value_w_m2k`,
  `bem/bem_geometry.py:23-24`).
- CRAB's IFC output content is undocumented at entity level (not addressed in
  the source), so an IFC round-trip cannot be assumed to work.

### First implementation step (one follow-up issue)

> **Add an offline cuboid→Matchline adapter, no pipeline wiring.**
> New module `geometry/cuboid_adapter.py` with two pure functions:
> 1. `cuboids_to_envelope_walls(cuboids) -> list[EnvelopeWall]` — takes a list
>    of centreline cuboids
>    (`{"category", "centerline": ((x1,y1),(x2,y2)), "thickness_m", "height_m"}`)
>    and returns `EnvelopeWall` records (`building_model.py:593-610`) with
>    `from_m`/`to_m`/`length_m`/`height_m` set; thickness is carried on a
>    parallel `BimElement`-style record (`building_model.py:658-683`) because
>    `EnvelopeWall` has no thickness field.
> 2. `footprint_from_cuboids(cuboids) -> list[FootprintPart]` — builds the
>    wall-adjacency graph by shared endpoints, finds closed cycles of ≥ 4
>    segments (the CRAB Step-3 rule, paper §4.4), turns them into room
>    polygons, and returns them through the existing
>    `footprint_parts_from_regions` (`geometry/geometry_simplify.py:494`) so
>    the result is directly consumable by `simplify_footprint`
>    (`geometry/geometry_simplify.py:576`).
>
> Acceptance: unit tests only — a 4-wall square cuboid set yields a
> 4-vertex footprint part with `area_delta_pct == 0.0`; an L-shaped room
> (5 segments) yields a 5-vertex part; a cuboid set with no closed cycle
> returns `[]` rather than raising. No changes to `run_pipeline.py`, no BEM
> changes, no IFC changes.

This step is deliberately small and reversible: it produces a **documented
adapter contract** (centrelines in, footprint parts out) that lets the
question "can a cuboid intermediate feed the existing area gate?" be answered
with Matchline's own measurements, instead of with an estimate.

---

## Sources

**Paper (quoted, not re-measured):** arXiv:2610.29257, *CRAB: Cuboid-based
Representation for As-built BIM from Point Clouds* — abstract, §3 (CrabSet
construction), §4.1 (problem formulation and cuboid parameterisation), §4.4
(reconstruction pipeline, Steps 1-3), §5 (results, ablation on
centreline-based parameterisation, robustness), §5.4 (limitation analysis),
§6 (conclusion and future work).

**Matchline code cited in this document:**

- `geometry/geometry_simplify.py` — `footprint_from_regions` (:54),
  `envelope_area` (:84), `simplify_ring` (:192), budget test (:315), reflex
  guard (:334), `MAX_GROWTH` (:402), `simplify_report` (:459),
  `footprint_parts_from_regions` (:494), `simplify_footprint` (:576).
- `bem/bem_geometry.py` — `BEMSpace` (:13), `BEMOpeningUnit` (:52),
  `BEMModel` (:150), `model_from_takeoff` (:237), scale handling (:241),
  space polygons (:268), ring conversion (:286), `MAX_SIMPLIFY_TOL` (:330).
- `bem/bem_export.py` — envelope surfaces (:501), floors (:690), interior
  walls (:711), air walls (:743).
- `bem/bem_helpers.py` — `_wall_edges` (:38), `_assign_wall_to_space` (:43),
  `_apportion` (:95), `_edge_spaces` (:113), `_distribute_openings` (:121).
- `bem/bem_space_split.py` — `covers_footprint` (:102), `shared_walls` (:125).
- `bem/bem_ifc4.py` — `IfcWall` creation (:266), wall representation (:274),
  `IfcOpeningElement` (:310), `IfcSpace` (:674), `IfcRelSpaceBoundary` (:805),
  `_write_grade_boundaries` (:884).
- `ifc/ifc_export.py` — `_bem_from_model` (:87), `_export_ifc` (:385).
- `ifc/ifc_import.py` — Tier-0 scope (:1-25), `_rect_profile_dims` (:252),
  `_rect_profile_footprint` (:260), `_wall_centreline_fix` (:386),
  `_material_layers` (:1157), `_ELEMENT_CLASSES` (:1227),
  `_extract_space_geometry` (:1431), "no IfcRelSpaceBoundary is read" (:3788).
- `ifc/ifc_space_borders.py` — `find_virtual_borders` (:83).
- `building_model.py` — `SpaceOpening` (:138), `Space` (:324), `EnvelopeWall`
  (:593), `BimElement` (:658).
- `run_pipeline.py` — `_stage_3_simplify` (:708-717).
- `docs/geometry_simplify.md` — measured results and documented limitations
  of the existing simplifier.
