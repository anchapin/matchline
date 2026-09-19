# Geometry simplification for BEM handoff

`geometry_simplify.py` reduces the number of wall surfaces exported to
energy-modeling software while holding total envelope area inside a
configurable tolerance (default 2%, supports 1–5%).

## Why this matters for BEM

In EnergyPlus/OpenStudio every wall segment is a heat-transfer surface:
conduction, solar, and airflow calculations scale with surface count, and
surface-matching/adjacency algorithms get slower and more fragile as counts
grow. But the *area* of the envelope is what drives loads — conduction is
U·A·ΔT, infiltration and solar gains all scale with area. So the correct
trade is: **minimize surface count, conserve area**. A 20-segment CAD
outline of a rectangular wall carries no more thermal information than 1
segment; deleting it changes simulation cost but must not change results.

## Algorithm

1. **Envelope construction** (`footprint_from_regions`): union the
   floor-area polygons (rooms, balconies, …) with shapely and take the
   exterior ring. Each ring edge is one wall surface. Interior holes are
   dropped; disjoint wings keep the largest part.

2. **Area-budgeted greedy vertex removal** (`simplify_ring`): a min-heap
   ordered by the triangle area each vertex's removal would cost. Collinear
   vertices cost exactly 0, so collinear/over-segmented edges merge first
   and for free; small notches and jogs follow in order of increasing area
   impact. A removal is applied only if it fits **both** guards:
   - a per-step cap (default 0.25% of envelope area) — blocks single large
     bites even when the total budget would allow them, so real corners and
     wings are never silently deleted;
   - the cumulative budget `tol` (default 2%).
   
   Rejected removals are logged in `skipped` with the reason. A per-removal
   segment-intersection test guards topology (no self-intersecting rings),
   and the final ring is re-validated with shapely.

3. **Optional Douglas–Peucker** (`dp_eps`): a first pass for over-segmented
   curved outlines (e.g. a faceted arc). It is itself area-checked and
   rolled back if it would breach `tol`.

4. **Auditability** (`simplify_report`): every output surface lists the
   source edge indices it replaced (`per_surface_mapping` partitions the
   original edge set exactly once — verified in tests), plus
   original/simplified counts, area delta %, the tolerance used, and the
   skipped-op log. Nothing is ever dropped silently.

The area metric is full envelope area (walls = perimeter × height, plus
roof + floor) when `wall_height` is given, else footprint area.

## Measured results

| Case | Surfaces | Reduction | Area Δ | tol |
|---|---|---|---|---|
| Rectangle, 5× over-segmented | 20 → 4 | −80% | +0.000% | 2% |
| L-shape, 4× over-segmented | 24 → 6 | −75% | +0.000% | 2% |
| U-shape, 4× over-segmented | 32 → 8 | −75% | +0.000% | 2% |
| Rectangle + 6 small jogs | 21 → 4 | −81% | −0.196% | 2% |
| Rectangle + faceted arc (DP) | 43 → 5 | −88% | −0.602% | 2% |
| Big notch, tol = 1% (breach case) | 8 → 8 | 0% | +0.000% | 1% |
| Real: AEC-bench sheet_01 envelope | 14 → 12 | −14% | −0.009% | 2% |

Notes:

- Collinear merging is exact (Δ = 0.000%): the L/U cases keep every true
  corner — the 6 skipped ops are the corner vertices correctly refused by
  the per-step cap.
- The breach case behaves as designed: at tol = 1% the only simplifiable
  feature (a large notch) would exceed the budget, so nothing is removed
  and all 8 rejections are logged.
- The real sheet reduces less (−14%) because shapely's polygon union
  already dissolves collinear edges when the envelope is built from room
  polygons; the remaining win is small kinks. On raw CAD wall-centerline
  polylines (the production input), expect reductions closer to the
  synthetic over-segmentation cases.
- Tolerance is configurable 1–5%; verified at 1%, 2%, 5%.

## Gaps / v2

- Input is a single exterior ring. Courtyard buildings (holes) and
  multi-wing campuses need per-part simplification with a shared budget.
- Wall heights are uniform (`wall_height` scalar); varying story heights
  need per-edge heights.
- No window/door area reallocation: punched openings are handled by the
  schedule path (`datasets_adapter.rollup_takeoff`), not by this module —
  the two area streams must be reconciled before BEM export so openings
  are not double-counted.
- The module depends on shapely, vendored persistently at
  `~/workspace/vendor/pylibs` (system site-packages do not survive VM
  restarts); the import falls back to the vendored copy automatically.
