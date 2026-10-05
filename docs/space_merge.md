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

## Where door positions come from

`build_model` finds doors in the arch plan image itself with
`door_detect.detect_door_swings` (numpy only) and registers them through the
arch title-block scale. A door counts only when the image shows the whole
symbol: a thin leaf from the hinge jamb, an inked quarter-circle arc of the
same radius back to the far jamb, a wall band behind the hinge or past the far
jamb (seen on two rows with the same centre), and a clear opening between the
jambs. The hinge is snapped to the wall's centre line; the width is the radius
whose arc best sits on the ink. All 8 hinge/swing orientations are searched.
A wall gap with no swing is never treated as a door. The image is searched
only when the model has a closet, since doors only feed closet merging.

With `service_rooms=True` the synthetic arch sheet draws the closet door as
that symbol. `sheets["arch"]["doors"]` records where it was drawn, in sheet
pixels, and `bldg["doors"]` names the host room: both are GT for scoring and
the linker reads neither.

Measured on seeds 0-29, both layouts, with and without service rooms (120
sheets, 59 doors): 56 found, 3 missed, 0 false doors. Found doors land within
4.4 px (9 cm) of the drawn centre, 1.6 px on average. All 3 misses are doors
hard against the closet's north wall with the CLOSET label printed over the
leaf, so the leaf is not separable; those closets stay their own space with a
`space_merge` review item rather than being guessed. One found door had its
width off by 12 px (0.24 m); the merge only uses the centre.

## Real drawings

The detector's pixel tolerances are tuned at 50 px/m and grow with the
drawing scale. Walls may be a solid band or two parallel lines, and a leaf may
be a single line or an outlined panel, so thin-line drafting at 200 dpi
(about 164 px/m at 1/4" = 1'-0") reads too. It has not yet been scored on a
real sheet: `scripts/eval_door_swings.py --aec-bench <dataset dir>` does that
on AEC-geometric-bench, matching detections to the annotated swing doors and
printing recall, precision and seconds per sheet. Title blocks there are
redacted, so the script takes the scale from the annotated door boxes (0.9 m
leaf) and uses the annotations for nothing else.

The AEC-Bench path in `run_pipeline.py` no longer turns wall regions into door
openings; its doors come only from door regions.

## IFC models (#574)

IFC spaces are authored, so the importer marks one as a closet or shaft only
from its IfcSpace `Name` or `LongName` (Alex, 2026-10-05): the words
`closet`/`storage` give a closet (0.85), `shaft`/`chase` give a shaft (0.90),
and shaft wins when both appear. Whole-word match only, and none of the
size-based defaults the drawing classifier uses: a small, unnamed IfcSpace
stays a room.

Each IfcDoor whose opening has a solid gets a plan centre in the world frame
(the centre of the opening solid). Those doors go to `merge_closets_and_shafts`
as connectors, before envelope classification, so walls, openings and
daylight land on the merged space. A door with no opening solid has no plan
centre and is not used; a closet with no usable door stays its own space with
a `space_merge` review item, as on drawings. The import summary line reports
`space_merge: N merged, M kept for review`.

The importer does not read `IfcRelSpaceBoundary` yet; see #573-#589 for the
other IFC follow-ups.

## Wall loops no IfcSpace claims (#581)

Shafts and closets are often not modelled as IfcSpaces at all. After wall ends
are joined onto neighbour centrelines (#575), the importer closes the wall
centrelines on each level into loops. A loop whose inside (shrunk by half the
level's median wall thickness) is less than 10% covered by IfcSpace footprints
becomes an `unclaimed_wall_loop` review item with its polygon, centreline area
and inside area. The candidate class is `shaft` when an unfilled IfcSlab
opening lies mostly inside the loop, otherwise `unknown` (it may be a shaft,
a closet, a courtyard or a modelling gap). Loops under 0.25 m^2 are ignored.
No space is created, so the shaft rule still only runs on modelled spaces;
the area closure check reports the total unclaimed area alongside its result.
Partly covered loops are a coverage question for the area checks, not this one.
