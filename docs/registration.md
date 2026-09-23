# Sheet Registration

`registration.py` aligns discipline sheets (lighting, mechanical, elevations) into
the architectural floor-plan coordinate frame (canonical meters, y-down).

## Concepts

**Canonical metres** — the architectural floor plan is the reference frame. Every
other sheet is registered into it: sheet px → canonical metres via an affine map.

**Plan sheets** (lighting, mechanical): `PlanRegistration` carries the affine
transform `(a,b,tx, c,d,ty)` plus method and confidence. In production this
comes from title-block scale + image alignment; the prototype takes
`(origin_px, px_per_m)` from sheet metadata, with a general tie-point
least-squares fitter available (`Affine2D.from_tie_points`).

**Facades** — each exterior wall run is a `Facade` in canonical metres
(y-down). The `Facade.plan_point(s_m)` helper maps a distance along the
facade to `(x, y)` in canonical coordinates.

**Elevation sheets** — `FacadeRegistration` maps elevation px → `(s_m along
facade, z_m height)` via two independent 1D affines:

```
s_m = a_s * u_px + b_s
z_m = a_z * v_px + b_z   (z up from grade)
```

Two registration paths:

| Path | Method | Confidence | When |
|---|---|---|---|
| Grid | Shared column-grid labels (A, B, …) tie plan ↔ elevation; 1D affine fit | 0.95 (0.85 if residual > 5 cm) | Grid bubbles present on elevation |
| Geometric fallback | Facade reference corner assumed at elevation wall origin; title-block scale | 0.65 | No grids on elevation |

The geometric fallback confidence (0.65) is deliberately below
`REVIEW_CONFIDENCE = 0.80` — every link built on it is flagged for review.

## Key functions

`Affine2D` — dataclass holding a 2D affine. `apply(px, py)` → `(x_m, y_m)`.
`from_scale_translate` constructs a scale+translate-only transform; `from_tie_points`
solves the general case via least-squares from ≥ 3 tie points.

`register_elevation_grid(sheet_id, facade, plan_grid_m, elev_bubbles, v_ground_px, elev_px_per_m, revision)`
— grid path. `plan_grid_m` maps grid labels to plan coordinates (metres).
Matches labels present in both plan and elevation; fits 1D affine `s_m = a*u_px + b`.
Raises if fewer than 2 shared labels are found.

`register_elevation_geometric(sheet_id, facade, wall_u0_px, elev_px_per_m, v_ground_px, revision)`
— geometric fallback. Maps the facade reference corner to the elevation drawing's
wall origin (u0_px); title-block scale gives px/m. **The wall origin is an
assumption** — there is no independent check on the drawing's internal origin.

`assign_points_to_spaces(points, spaces)` — point-in-polygon (canonical metres)
for each point. Returns `{point_id: space_id or None}`. First enclosing space wins;
misses return `None` and are reported, never silently dropped.

`match_interval_to_segments(s0, s1, segments)` — matches a facade interval
`[s0, s1]` to wall segments. Returns `(best_segment, overlap_fraction, ambiguous)`.
`ambiguous` is `True` when the best fraction is < 0.5 or the margin to the second
is < 0.2 — those links are sent to the review queue.

## Output contract

Every `PlanRegistration` and `FacadeRegistration` carries a `provenance` field:
`sheet_id`, `revision`, `method`, `confidence`, and a `note` with details
(grid labels used, residuals, or the geometric assumption).

## Limitations

- **Rectangular footprints only** — `Facade` and the geometric fallback assume
  orthogonal exterior walls.
- **Non-overlapping spaces** — `assign_points_to_spaces` uses first-enclosing-space
  wins; it does not handle overlapping room polygons (e.g. shaft cuts).
- **Geometric fallback is unauthenticated** — the elevation wall origin (u0_px)
  is read from the drawing's own coordinate system; there is no independent
  verification. Every result built on this path carries confidence 0.65 and
  enters the review queue.
- **Grid-bubble OCR** — if grid bubble labels are misread or partially occluded,
  `register_elevation_grid` may use wrong tie points; residual check catches large
  errors but small systematic OCR errors pass undetected.
- **No door/window distinction** — openings on elevations are treated uniformly;
  `elevation_windows` disambiguates by reconciling with window counts from
  `schedule.csv`.
