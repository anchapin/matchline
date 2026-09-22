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
| `synth/multidiscipline.py` | Coordinated synthetic building generator (arch + lighting + mech + two south elevations, one gridded, one not) with ground truth. |
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
- **South facade only** in this prototype; north/east/west generalize by
  adding wall segments per facade.
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
