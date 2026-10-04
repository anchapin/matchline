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
  on its boundary (doors come from `SpaceOpening.plan_center_m` and from the
  building's `doors` list; the AEC-Bench plan path builds a single space, so it
  has no closets to merge yet);
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

## Synthetic coverage

`generate_building(seed, service_rooms=True)` carves a 1.0 m strip off the east
side of one room: an unnumbered CLOSET (2 to 8 m2) with a door onto that room, and
a 1.0 x 1.5 m SHAFT at the strip's south end. The building's `doors` list carries
the closet door's plan position, and `link.build_model` passes it to the merge.
Closets and shafts get no fixtures, diffusers or windows. The option uses its own
random stream and is off by default, so default buildings are unchanged. Across
seeds 0 to 29 (both layouts) 59 of 60 buildings merge both spaces into the host
room with no review items; the other has no room large enough to host the strip.

## Where door positions come from (synthetic buildings)

With `service_rooms=True` the arch sheet draws the closet door as a standard
plan symbol: a gap in the wall, the open leaf, and a quarter-circle swing into
the closet. The sheet also carries detector-style door outputs in sheet pixels
(`sheets["arch"]["doors"]`: opening centre `x_px`/`y_px`, `bbox_px`,
`width_px`). `build_model` registers those through the arch title-block scale
and hands them to `merge_closets_and_shafts`; the generator's GT list
(`bldg["doors"]`, with the host `room_number`) is for scoring only and is never
read by the linker.

These pixel outputs are simulated the same way lighting fixtures and
diffusers are. No image-based door detector reads the drawn swing yet; tests
check that the swing is drawn exactly where the pixel output says it is.
