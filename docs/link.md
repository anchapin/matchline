# Cross-Sheet Linker

`link.py` builds the canonical `BuildingModel` from discipline sheets (arch plan,
lighting plan, mechanical plan, elevation drawings).

## Pipeline

```
ARCH PLAN  -> Spaces (id "{level}-{number}"), envelope walls
LIGHTING   -> fixtures to spaces (point-in-polygon) -> per-space watts + LPD
MECH       -> diffusers to spaces -> zone membership (diffuser position, not id)
ELEVATION  -> window intervals -> south wall segments -> rooms
             (grid path or geometric fallback)
DEDUP      -> cross-sheet window dedup via _dedupe_space_openings
```

Inputs are synthetic building dicts (`synth.multidiscipline`); the contracts
mirror real detector outputs (bboxes/tags in sheet px), so a real front-end can
replace the synth layer without changing the linker.

**Coordinate frame**: canonical model uses **y-down** (drawing frame). All
discipline sheets are registered into this frame before linking.

## Key functions

`build_model(bldg, elevation_key, building_name)` — canonical entry point.
`elevation_key` controls the elevation registration path:
- `"elev_grid"` → grid path (conf = 0.95)
- `"elev_nogrid"` → geometric fallback (conf = 0.65)
- `None` → skip elevation linking

`_dedupe_space_openings(model)` — merges duplicate `SpaceOpening` sightings
across spaces and levels (#404, #664). Openings are grouped by
(facade, tag, category); inside a group two are one physical opening when
their along-wall centres are within `CROSS_LEVEL_DEDUP_TOL_M` (5 cm), their
widths agree within 0.15 m, and they sit on the same level (heights agree) or
on adjacent levels with absolute vertical extents that overlap or touch. That
keeps a two-storey window seen on two adjacent sheets as one opening while
punched windows stacked floor over floor stay separate. The best-confidence
sighting is kept; every source sheet goes in `source_provenance` (max 2,
schema from #663) and a `window_dedup` record is appended to `history`.
Openings with no along-wall position are never merged. Matching rules live in
`opening_identity.py`, shared with the `cross_level_dedup` validation check.

`_link_lighting(bldg, model, spaces, sched, report)` — assigns fixtures to
spaces by point-in-polygon (canonical metres). Missing schedule entries
(`entry is None`) are flagged at confidence 0.5. Fixture tags with no
matching schedule entry are also flagged.

`_link_mech(bldg, model, spaces, report)` — zone assignment by diffuser
position: a zone's spaces = union of its diffusers' enclosing spaces. No
cross-sheet id matching is used. Unresolved diffusers are flagged.

`_link_elevation(bldg, model, spaces, elevation_key, win_sched, report)` —
facade window-to-room linking. Two paths:

**Grid path** (`elevation_key="elev_grid"`): `register_elevation_grid` maps
elevation px → `(s_m, z_m)` via shared column-grid labels. Confidence
`conf = 0.95 * (0.5 + 0.5 * frac)`, where `frac` is the interval-overlap
fraction with the target wall segment. For `frac >= 0.70`, `conf >= 0.8075`
— no review flag needed.

**Geometric fallback** (`elevation_key="elev_nogrid"`):
`register_elevation_geometric` maps the facade reference corner to the
elevation drawing wall origin (assumed). Confidence
`conf = 0.65 * (0.5 + 0.5 * frac)` — always below 0.80 regardless of
fraction, so every geometric link is flagged for review.

`_schedules(bldg)` — returns `(win_sched, light_sched)` dicts from the
building manifest.

## Confidence and review routing

`REVIEW_CONFIDENCE = 0.80` is the threshold. Every link carries provenance
+ confidence; links below the threshold go to `model.review_queue` rather
than being silently accepted.

```
needs_review = ambiguous or conf < REVIEW_CONFIDENCE
  ambiguous: match_interval_to_segments overlap fraction < 0.5 or margin < 0.2
  conf < 0.80: geometric fallback (max conf 0.65) or low-overlap grid links
```

## Limitations

- **Single level**: `_build_spaces` creates one `Level` per building; multi-level
  buildings require multiple `build_model` calls and manual merging.
- **Rectangular envelope**: `_build_envelope` hard-codes four wall runs
  (south/north/east/west) at axis-aligned coordinates. Non-orthogonal buildings
  need a different envelope representation.
- **Diffuser-position zones only**: `_link_mech` uses point-in-polygon on
  diffuser positions to determine zone membership. If diffusers are missing from
  the mechanical plan, spaces silently get no zone assignment.
- **Cross-sheet window dedup needs positions**: `_dedupe_space_openings`
  matches on a 5 cm along-wall tolerance, so sightings with no position are
  kept as-is (the `cross_level_dedup` check warns when they exceed 1% of a
  level). A merged two-storey window keeps one sighting's geometry and is
  flagged `needs_review` when that sighting does not span the full height.
- **No wall opening piercing**: `_link_elevation` creates `SpaceOpening` entries
  but does not model wall penetration geometry (penetration depth, surrounding
  wall insulation). BEM export handles surface-area distribution only.
- **Geometric fallback assumes wall origin**: the elevation drawing's `(0, 0)`
  origin is assumed to align with the facade reference corner. This is a
  strong assumption verified by neither the registration nor the linking step.
