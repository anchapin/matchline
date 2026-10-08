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

## Not yet

Casework drawn within a wall thickness of a wall face (a counter front 0.6 m
off the wall) can still pair as a wall; the Clinic measurement will show how
often. Raster fallback for scanned sheets, hatch-pattern walls, curved walls,
classifying unlabeled faces (`polygon_classify` / `space_merge`), and
simplifying wall jogs so slivers never form are follow-ups on #740.
