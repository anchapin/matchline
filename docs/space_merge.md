# Closet and shaft merging

`space_merge.merge_closets_and_shafts(model)` runs at the end of `link.build_model`.
The rule was set by the project owner on 2026-10-04:

- A **closet** merges into the adjacent room its door opens onto.
- A **shaft** merges into the adjacent room with the greatest percentage of shared
  wall area (shared wall area divided by the shaft's total wall area).

Closets are handled first, so a shaft beside a closet sees the room that absorbed it.
Only `poly_type == "room"` spaces receive merges.

## What is never guessed

The space is kept as its own space and a `space_merge` review item names why when:

- the closet has no door with a known plan position (`SpaceOpening.plan_center_m`)
  on its boundary; the drawing path records door counts, not positions, so today
  every drawing-path closet lands here;
- its door opens onto no room, onto more than one room (a corner door), or its
  doors open onto different rooms;
- the shaft shares no wall with any room, or two rooms tie for the largest share
  (within 0.5 percentage points);
- the merged outline would not be a single polygon without holes.

## Geometry

Faces within `ADJ_TOL_M` (0.35 m) count as one shared wall. A shared wall is
measured per shaft edge from a strip on its outward side, so a room that only
touches at a corner shares nothing. When the two outlines are a partition apart
the gap is closed and the absorbed wall strip area is recorded on the merge record
and in the target's history.

The target keeps its id; `Space.merged_from` lists absorbed ids; openings, fixtures,
HVAC components, daylight zones, zone membership and envelope segments move to the
target; `toplit_m2` is recomputed as the union of toplit zones; LPD is recomputed on
the merged area.
