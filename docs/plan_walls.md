# Walls and rooms (`plan_walls.py`)

First slice of #740: turns a vector floor-plan sheet into walls (centreline +
thickness), openings and closed room polygons.

```bash
matchline ingest set.pdf --out sheets/
matchline scale sheets/        # needed: walls are found by real thickness
matchline index sheets/        # optional: only floor plans marked for takeoff are read
matchline walls sheets/        # writes walls_NNN.json per sheet
```

## How it works

1. **Wall faces.** Two parallel stroked lines 0.05 to 0.60 m apart, overlapping
   at least 0.25 m, with no third parallel line between them, make a wall band.
   Bands that share a face (a window's glazing line) merge when the total still
   fits a wall. Filled near-rectangles with a wall-thickness short side (solid
   poché) are walls too. Dashed lines and curves (door swings) are ignored.
   A run of equally spaced parallel lines (stair treads, hatching) is not a
   wall, and the gap between two walls with a narrow chase between them is not
   a third, thicker wall. Bands are always compared in one direction frame, so
   CAD float noise either side of horizontal does not split a wall.
2. **Wall runs.** Collinear bands of one thickness join. Gaps up to 2.5 m are
   bridged: a gap with another wall ending in it is a junction and the run
   continues through it as one wall; otherwise it is an opening (door or cased
   opening) with its width.
3. **Joints.** Wall ends snap to a crossing wall's centreline within reach
   (0.75 x the thicker wall), closing L and T joints; ends that meet are noded
   exactly.
4. **Rooms.** Closed faces of the centreline graph. Each room has the gross
   (centreline) polygon, any islands inside it (`holes_m`, e.g. a column) and
   the net polygon inside the wall faces, in metres with y up (origin at the
   sheet's bottom-left).
5. **Labels.** Text inside a face (dimension strings excluded) is parsed with
   `room_labels.parse_room_label`.

## Review queue

Never silently dropped:

- `unclosed_wall`: a wall end that connects to nothing (a room may be open)
- a face smaller than 1 m2
- a face holding two different room numbers (a wall is probably missing)
- `no_scale`, `no_walls`

## Measurement

`compare_rooms(pred, truth)` matches rooms by IoU >= 0.5 and reports room
count, missed/extra rooms and per-room area error; `wall_length_error` compares
total wall length. These are for the held-out BSI Clinic sheets, which are run
on the development machine and never committed:

```bash
python scripts/validate_clinic_walls.py /path/Clinic_Architectural.ifc --json clinic_walls.json
```

The script cuts every wall Body at 1.2 m above its storey floor, unions the cut
outlines and draws them as stroked lines on a 1:100 sheet with each IfcSpace's
name and number at its centroid, then runs `extract_walls` and compares against
the IfcSpace footprints (rooms) and the IFC walls' cut length.

Measurements:

| Storey | Rooms matched / truth | Extra faces | Median area err | Wall length err |
|---|---|---|---|---|
| First Floor, before joins | 27 / 154 | 52 | 11.5% | 2.7% |
| First Floor, near joins (#773) | 72 / 154 | 85 | 9.4% | 2.7% |
| First Floor, + corner doors | 106 / 154 | 114 | 9.4% | 2.7% |
| Second Floor, before joins | 27 / 109 | 44 | 9.0% | 0.3% |
| Second Floor, near joins (#773) | 50 / 109 | 68 | 9.0% | 0.3% |
| Second Floor, + corner doors | 81 / 109 | 82 | 9.2% | 0.3% |
| First Floor, + sliver merge | 106 / 154 | 27 | 11.2% | 2.7% |
| Second Floor, + sliver merge | 81 / 109 | 22 | 10.6% | 0.3% |

IfcSpace boundaries include virtual ones (corridor segments, a waiting area
open to a corridor, an alcove) that no plan line shows. `rooms_wall_bounded`
in `scripts/validate_clinic_walls.py` joins spaces whose shared edge has at
least 0.8 m with no wall and no door or window opening between them, and scores
the same rooms against those regions:

| Storey | Spaces / regions | Matched / regions | Extra faces | Median area err |
|---|---|---|---|---|
| First Floor | 154 / 126 | 100 / 126 (79%) | 33 | 11.2% |
| Second Floor | 109 / 87 | 72 / 87 (83%) | 31 | 10.6% |

Against raw spaces the same output matches 69% (FF) and 74% (SF). Of the raw
misses on joined rooms, the shared edges with nothing found between them were
81 m (FF) and 17 m (SF), all with no IFC wall there. The truth still counts
ROOF and OPEN TO BELOW spaces on the Second Floor as regions.

A collinear gap wider than a door but up to `WIDE_OPENING_M` (4.5 m) closes
with an air wall: the opening carries `"air_wall": true` and goes to review as
`air_wall` (a window wider than a door cut at sill height, a storefront, or an
open edge). Swept 2.5 to 8 m on the Clinic; 4.5 m is the widest before extra
faces climb:

| Storey | Raw matched | Extra | Wall-bounded matched | Extra | Median area err |
|---|---|---|---|---|---|
| First Floor, 2.5 m (before) | 106 / 154 | 27 | 100 / 126 | 33 | 11.2% |
| First Floor, 4.5 m | 106 / 154 | 23 | 102 / 126 | 27 | 10.9% |
| First Floor, 6.0 m | 111 / 154 | 45 | 101 / 126 | 55 | 11.4% |
| Second Floor, 2.5 m (before) | 81 / 109 | 22 | 72 / 87 | 31 | 10.6% |
| Second Floor, 4.5 m | 83 / 109 | 22 | 75 / 87 | 30 | 10.6% |
| Second Floor, 6.0 m | 87 / 109 | 29 | 74 / 87 | 42 | 10.6% |

What is left is mostly open-plan areas whose walls end in the open (reception
counters, cubicle partitions, half walls): 21 FF and 18 SF spaces have no face
over them, 30 of them with an unclosed wall end on their edge.

Before joins, the First Floor wall graph had 327 free ends (counting door
bridges as connections): 180 within half of both thicknesses of another wall's
centreline, 116 between 0.2 and 1.0 m from one, 30 between 1.0 and 2.5 m, 1
farther. Two rules now close them:

- A free end within the two walls' thicknesses added together of another
  wall's centreline joins it with a short connector (a T-junction stopped at a
  face, a wall that changes thickness or steps sideways).
- A free end whose wall, carried on along its own line, crosses another wall
  within `MAX_OPENING_M` is a door beside a corner. It becomes an opening with
  `"beside_corner": true`.

`unclosed_wall` items went 462 -> 261 -> 140 (First Floor) and 225 -> 131 -> 32
(Second Floor). Before the sliver merge, 97 of the 114 First Floor extra
faces were slivers under 2 m^2 (wall jogs closing into tiny loops), 11 spanned
several truth rooms and 6 split one room.

Nothing is dropped: an unlabeled face under `SLIVER_M2` (2 m^2) merges into a
neighbour by the closet/shaft rule, into the face it shares a door with, else
the face it shares the most wall with. The folded areas are kept on the room
as `merged_m2`. A face with a room label stays a room however small. Merging
cut `room` review items from 88 to 4 (First Floor) and 58 to 4 (Second
Floor). Median area error rose about 1.5 points because sliver area is now
counted in the room next to it.

A wall that stops in the open, more than a door width (`MAX_OPENING_M`) but no
more than `WIDE_OPENING_M` short of the wall ahead of it, closes with an air wall
only when the closure then parts two faces that both carry a room label: a
counter, half wall or open office edge between two named spaces. The opening is
`beside_corner` with `"air_wall": true` and goes to review as `air_wall`. Inside
one named space (one label, or none) the wall stays open and the area stays one
room, with the free end in review as `unclosed_wall`. Closing every such end
regardless of labels matched 5 more raw spaces but added 16 extra faces on the
Clinic; the label rule keeps most of the gain:

| Storey | Raw matched | Extra | Wall-bounded matched | Extra | Unclosed ends |
|---|---|---|---|---|---|
| First Floor, before | 106 / 154 | 23 | 102 / 126 | 27 | 136 |
| First Floor, every end closed | 111 / 154 | 34 | 106 / 126 | 39 | 122 |
| First Floor, labelled rooms only | 108 / 154 | 24 | 105 / 126 | 27 | 128 |
| Second Floor, before | 83 / 109 | 22 | 75 / 87 | 30 | 29 |
| Second Floor, every end closed | 87 / 109 | 27 | 76 / 87 | 38 | 14 |
| Second Floor, labelled rooms only | 85 / 109 | 23 | 75 / 87 | 33 | 18 |

The Second Floor wall-bounded extras rise by 3 because that truth joins spaces
across edges with no wall, which is exactly where a labelled split lands.

## Not yet

Casework drawn within a wall thickness of a wall face (a counter front 0.6 m
off the wall) can still pair as a wall; the Clinic measurement will show how
often. Raster fallback for scanned sheets, hatch-pattern walls, curved walls,
classifying unlabeled faces (`polygon_classify` / `space_merge`), and
simplifying wall jogs so slivers never form are follow-ups on #740.

## Door swings (#743)

A gap is marked `kind: "door"` when the plan draws a swing in it: a stroked
curve (dashed counts; consecutive Bezier pieces are one run) whose ends both
sit one leaf width from a jamb, one end at the closed position (the far jamb)
and the other swung off the wall line. A pair is two half-width curves, one
hinged on each jamb, meeting at the middle. Radius and position match within
`SWING_TOL` (20% of the leaf width, or the wall thickness for the closed end).

Each door opening gains `swing` (`single` / `double`), `hinges_m` and
`door_confidence` (0.85 single, 0.8 pair); `stats.doors` counts them. A gap
with no swing stays unclassified: it is never called a window for lacking one.
Air-wall gaps are not checked.

In a set (`real_set.py`) a door swing restricts the schedule width match to
door rows, so a door and a window of the same scheduled width no longer tie;
the provenance note records the swing. A swing with no scheduled door of that
width goes to review as `opening_unsized`. This is a vector rule with no
trained weights, so it ships under any release.

## Windows from glazing lines (#743)

A window drawn as a glazing line (or sill lines) inside a wall leaves two
half-thickness bands sharing that line; `_merge_glazing` folds them back
into the wall, and each folded band is a window candidate. It becomes an
opening with `kind: "window"`, `source: "glazing_line"` and
`window_confidence` 0.75 when it:

- lies along one wall and is no wider than `WIDE_OPENING_M`;
- leaves the wall running on past it by `WINDOW_RETURN_M` (0.30 m) or two
  wall thicknesses, whichever is more, on at least one side. A middle line
  the length of the wall is a cavity or insulation line, not glazing;
- does not overlap a window already found on that wall by more than half.

Sill lines across a gap close the gap, so that window is one `window`
opening, not an unclassified gap. A plain gap with nothing drawn in it stays
unclassified. `stats.windows` counts them. In a set, a window restricts the
schedule width match to window rows, the mirror of the door-swing rule, and
the provenance note records the glazing line.

### Storefront and curtain wall (#793)

The return rule above misses glazing that runs the whole length of its wall
(storefront, curtain wall between columns, a glazed wall corner to corner),
and the width cap misses glazing wider than `WIDE_OPENING_M`. Either kind
still counts when mullions break it up: at least `MULLION_MIN` (2) ticks
cross the wall inside the glazed run. A tick is a stroked segment
perpendicular to the wall, within 50% of the wall thickness long, centred on
the wall line; ticks closer than the return margin to each other or to the
ends of the run count once, so a frame, a corner or a return is not a
mullion. These openings carry `source: "glazing_mullions"` and
`window_confidence` 0.6 (`STOREFRONT_CONFIDENCE`), below a punched window's
0.75, and keep the whole glazed run as their width.

A middle line along the whole wall with fewer than two ticks is still a
cavity or insulation line and the wall stays opaque.

A door drawn inside storefront glazing with no gap in the wall has two jamb
ticks that look exactly like mullions. Two neighbouring ticks at most
`MAX_DOOR_M` (2.5 m) apart with a door swing between them (the same swing
test as a door gap, single or double, see Door swings) are taken as the
door's jambs. They do not count toward `MULLION_MIN`, so a cavity line with
a door in it is no longer a storefront. Ticks with no swing between them are
still mullions.

When the run is still a window (other mullions, or a glazing layer), it keeps
its whole length and lists each door inside it under `doors_in_glazing`
(jamb points and width). The window is not split, because a storefront's
schedule width may or may not include its door and the schedule tag match
downstream sizes the window from the schedule. Taking the door out of the
glass, and adding it as a door, is not done yet.

### CAD layers (#793)

When the PDF keeps its CAD layers (optional content groups), ingestion
records each path's layer name on the primitive (`layer`) and the sheet's
layer names (`layers`). Layers then decide before the geometry rules:

- A glazed band with a face line on a glazing layer (`GLAZ`, `STOREFRONT`,
  `CURTAIN`/`CURT-WALL`, `WINDOW`/`WIND`, e.g. `A-GLAZ`, `A-WALL-GLAZ`) is a
  window at any length, mullions or not: `source: "glazing_layer"`,
  `window_confidence` 0.7 (`LAYER_GLAZING_CONFIDENCE`).
- A middle line on a pattern or insulation layer (`PATT`, `INSUL`, `HATCH`,
  `BATT`, e.g. `A-WALL-PATT`; `A-GLAZ-PATT` counts as pattern) is never a
  window, at any length, ticks or not.
- Any other layer name (`A-WALL`, `0`) gives no evidence and the return,
  width and mullion rules above apply as before.

Only the middle line inside the wall is judged, not the wall's own face
lines. `stats["layers"]` lists the layer names on all the plan's stroked
segments.
A thin wall band that would be flagged `maybe_glazing` (see the thin band
rule) is a window over its whole length instead when glazing-layer face lines
lying along it, within half its thickness of the centreline, cover at least
80% (`THIN_FILL_SHARE`) of its length and none of those lines is on a pattern
layer: `source: "glazing_layer_band"`, `window_confidence` 0.7, no
`maybe_glazing` review item. The band stays a wall, so the room still closes.

A storefront drawn as a plain wall band, with no glazing line at all, can still
be found from its schedule tag. A tag-like span within `TAG_RADIUS_M` of a wall
that has no opening (and not within it of any opening) is kept in `wall_tags`
(`wall`, `tag_text`, `point_m`, `tag_dist_m`). The drawing-set run then checks it
against the schedule: a scheduled window whose width spans the wall (up to
`WIDTH_TOL_M` over, one wall thickness under, since a schedule may give the
rough opening between corners) is modelled on that wall at the scheduled size,
provenance `plan_wall_tag`, confidence 0.7. Any other scheduled door or window
tag on such a wall goes to review as `opening_unsized` ("may be glazing"), with
the gap a review edit needs to add it (#798). Unscheduled tags are ignored.

A storefront drawn as a thin wall band of its own, with no glazing line and no
scheduled tag, is flagged rather than modelled. A wall at most
`THIN_BAND_RATIO` (0.6) as thick as the wall it meets end to end on both sides,
on the same line (centrelines within half the thicker wall, since glazing often
sits on one face), at least `THIN_BAND_MIN_M` (1.0 m) long and with no opening
on it, gets `maybe_glazing: true`, a `maybe_glazing` review item and a count in
`stats.maybe_glazing`. A partition turns off the exterior line; it does not
continue it, so a thin run between two thicker ones is more often glazing. A
thin run into a corner (thick wall on one side only) is a change of wall type
and is not flagged. In a drawing-set run a flagged wall on the envelope goes to
review as `opening_unsized` (gap `drawn: "thin_band"`, no candidates) and stays
opaque: nothing says how tall the glazing is or which product it is, and a
review edit adds it. A band with a scheduled tag on it is left to the tag rule
above.

A wall that thins for a recess (a panel niche) reads the same way and is
flagged too; that costs a review item, never a window.

A thin bay narrower than `MAX_OPENING_M` used to vanish: the gap between the
two thick walls read as a junction (the thin wall's ends sit in it) and the
thick run carried straight through, one opaque wall over the bay. Now a wall
at most `THIN_BAND_RATIO` as thick as the run, on its line (within half its
thickness) and covering at least `THIN_FILL_SHARE` (80%) of the gap, stops the
run there (`_thin_fill`), so the bay keeps its own wall and the rule above
flags it. The band is trimmed to the thick walls' faces, so it covers the gap
less one wall thickness; with a 0.3 m wall, gaps under about 1.5 m fall below
80% and the run carries through, which matches `THIN_BAND_MIN_M`: such short
pieces are frames or jambs, close the room, and are not flagged.

Not covered yet: a thin bay narrower than `MAX_OPENING_M` is bridged by the
thicker wall run it interrupts and is not flagged; glazing drawn as two lines
closer than `WALL_T_MIN_M` never becomes a wall to flag; PDF layer names; door
jambs inside a full-length glazing line would read as ticks.

## Opening tags (#793)

Text that reads like a schedule tag (letters then digits, e.g. `W3`, `D-12`, `SF-1`;
room numbers and names do not match) within 1.5 m of an opening's midpoint is attached
as `tag_text` with `tag_dist_m`; the nearest opening wins when two are in range.
Every tag an opening gets is listed in `tags_near`, nearest first (#829), since a
wall-type mark can sit closer than the window's own mark. `stats.tagged` counts tagged
openings. The set pipeline picks the schedule row from the nearest tag that is in the
door/window schedule, and any legend wall type among the tags still types the wall.
