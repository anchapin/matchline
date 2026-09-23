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

`_dedupe_space_openings(model)` — merges duplicate `SpaceOpening` entries
when two elevation runs of the same facade are linked separately. Dedup is by
tag (primary) + geometric proximity (fallback for untagged/conflicting entries):
entries whose `host_interval_m` overlaps by `>= OPENING_DEDUP_TOL_M (0.15 m)`
are merged. Merged entries carry a compound `sheet_id` and the higher
confidence. See Issue #1 in `docs/design-docs/open-issues.md`.

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
- **Cross-sheet window dedup is approximate**: `_dedupe_space_openings` uses
  `OPENING_DEDUP_TOL_M = 0.15 m` center-distance tolerance. Overlapping windows
  on the same facade with different tags may be incorrectly merged.
- **No wall opening piercing**: `_link_elevation` creates `SpaceOpening` entries
  but does not model wall penetration geometry (penetration depth, surrounding
  wall insulation). BEM export handles surface-area distribution only.
- **Geometric fallback assumes wall origin**: the elevation drawing's `(0, 0)`
  origin is assumed to align with the facade reference corner. This is a
  strong assumption verified by neither the registration nor the linking step.
