# Stage-6 linked-export geometric defects — diagnosis (#1409)

Found by the #1406 Phase 0 measurement: linked service-rooms models
(seed311/312/313-svc) exported through `run_pipeline` Stage 6 fail
`shell_closure` (and seed312 carries duplicate surfaces) while passing
`validate_gbxml`. Stage 6 never ran the geometric checks, so the defects
published silently. This note records the mechanism, diagnosed by the
#1409 worker run (`matchline-1409-1794971860`, 2026-11-17) against the
bench linked models; the visibility half of #1409 (Stage 6 now records
the geometric checks and both validator verdicts in
`stage_06_bem/export_report.json`) is what makes this class observable
in future runs.

## Mechanism

Both symptoms share one root cause: a zero-area spike ("slit") in a
merged space polygon, produced by `space_merge` when it unions polygons
whose shared boundary differs by ~1e-8 m.

1. A merge target polygon's coordinates are rounded to 6 decimals by
   `_ring()` (e.g. a host room's top edge at y = 15.380665), while the
   incoming source polygon (a shaft/closet strip from the ground-truth
   rect) keeps full precision (y = 15.380665071633794).
2. The two polygons therefore overlap in a sliver ~7e-8 m tall along
   their shared edge. Shapely's `unary_union` of two polygons with
   slightly non-coincident boundaries yields a ring containing a
   degenerate spur: the exterior goes … → (19.205268, 13.880665) →
   (18.205268, 13.880665) → (19.205268, 13.880665) → …, revisiting the
   same segment. `_ring`'s simplify (1e-6, preserve_topology) keeps the
   spike.
3. The gbXML shell for that space writes a wall quad for every polygon
   edge, including the doubled spur edge, so that edge is shared by 4
   faces — `shell_closure` fails (seed311/313-svc shape).
4. The footprint ring is built from the same space polygons
   (`footprint_from_regions`), so a spike also repeats ring edges in
   the envelope — the duplicate-surface groups seen on seed312-svc
   (e.g. edges (7.46, 7.92)→(7.46, 14.17) and (7.46, 7.92)→(14.93, 7.92)
   each appearing twice). Duplicate surfaces and open shells are the
   same defect seen from two sides, not two defects.

## What this does and does not propose

- The fix direction (not in #1409's scope) is at the merge: snap the
  incoming polygon to the same 6-decimal grid `_ring` applies to the
  target before unioning, or drop zero-area spurs from the merged ring.
  Either changes model construction and needs its own issue with the
  corpus as the regression oracle.
- Gating Stage 6 (publish-nothing) remains a separate decision, and
  should not precede the merge fix: `shell_closure` is an R5 class with
  no mechanical repair in #1407's loop, so a gate today would simply
  block these exports with no repair path.
