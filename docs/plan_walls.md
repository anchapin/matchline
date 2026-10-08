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
| First Floor, with joins | 72 / 154 | 85 | 9.4% | 2.7% |
| Second Floor, before joins | 27 / 109 | 44 | 9.0% | 0.3% |
| Second Floor, with joins | 50 / 109 | 68 | 9.0% | 0.3% |

Before joins, the First Floor wall graph had 327 free ends (counting door
bridges as connections): 180 within half of both thicknesses of another wall's
centreline, 116 between 0.2 and 1.0 m from one, 30 between 1.0 and 2.5 m, 1
farther. A free end within half of both thicknesses now joins that wall with a
short connector (a T-junction stopped at a face, a wall that changes thickness
or steps sideways). `unclosed_wall` items fell from 462 to 261 (First Floor)
and 225 to 131 (Second Floor). The ends 0.2 to 2.5 m short (openings beside a
corner, where the wall on the far side is not on the same line) are next.

## Not yet

Casework drawn within a wall thickness of a wall face (a counter front 0.6 m
off the wall) can still pair as a wall; the Clinic measurement will show how
often. Raster fallback for scanned sheets, hatch-pattern walls, curved walls,
classifying unlabeled faces (`polygon_classify` / `space_merge`), and closing
the T-junctions and off-line openings the Clinic measurement found are
follow-ups on #740.
