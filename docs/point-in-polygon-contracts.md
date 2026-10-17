# Point-in-polygon contracts (inventory)

The codebase deliberately keeps several containment implementations. They are
distinct contracts, not one concept with several spellings: each serves a
different input space and boundary rule. **Do not unify them.** The tests in
`tests/test_point_in_polygon_contracts.py` pin each contract separately over a
shared fixture family (simple convex and non-convex rings, degenerate rings,
on-edge / on-vertex points, self-intersecting outlines, far points), so none
can drift silently.

| Implementation | Input space | Boundary rule | Degenerate input | Holes | Self-intersection |
| --- | --- | --- | --- | --- | --- |
| `room_labels.point_in_polygon` | `polygon_px` rings, pixel space | **No rule**; observed behavior is orientation-dependent (classic ray-cast artifact, pinned as regression anchors) | < 3 vertices → `False` | No (single ring only) | Even-odd parity |
| `ifc_import._point_in_polygon` | IFC-space rings, meters | **On-boundary is inside** (`True`), explicit segment test | 0 vertices → `False` | No (single ring only) | Even-odd parity, boundary rule dominates on edges |
| `registration.assign_points_to_spaces` | canonical meters, list dispatch | Inherits `room_labels.point_in_polygon` | Degenerate space never captures | No | Inherits |
| `mech_tags.room_of` | `Space.polygon_m`, meters | `covers` → boundary is inside | len(polygon) < 3 skipped | No | shapely semantics |
| `space_merge._contains` | merged `Polygon` | `covers` → boundary is inside | caller's validity repair (`buffer(0)`) upstream | **Yes** (shapely holes) | shapely semantics |
| `grade.Terrain.at` | triangle footprints, meters | `buffer(1e-9).contains` → on-edge inside; 1e-7 outside is out (the buffer is the whole tolerance) | Zero-area facet dropped at construction | No | n/a (triangles) |
| `synth.sheets._point_in_rooms` | axis-aligned rect tuples, pixel space | Inclusive bounds widened by `eps=1e-6` on every side | Degenerate rect = zero-width band within eps | n/a (rects) | n/a |
| `bem_helpers._place_skylights_on_roof` | setback-inset roof, meters | **Strict** `shapely.contains(usable, box)`: a box touching the setback boundary is NOT contained and is skipped | Degenerate roof outline skips every unit | n/a | n/a |

## Rules of thumb

- Pixel-space ray casts (`room_labels`) have no boundary rule; callers that
  need boundary-inside semantics use the IFC variant or shapely `covers`.
- `covers` (mech_tags, space_merge) admits boundary; strict `contains`
  (skylight placement) refuses it: that difference is what keeps a skylight
  edge off the setback line while a mech tag on a wall stays assigned.
- `registration.assign_points_to_spaces` is dispatch over the ray cast (first
  enclosing space wins, miss returns `None`); its geometry contract lives in
  `room_labels.point_in_polygon`.
