# Non-Room Polygon Classification — `polygon_classify.py`

Classifies architectural plan polygons that are not rooms — shafts, closets, elevator cores — using only geometric evidence from the building dict. No ML.

## Pipeline position

```
room_labels  →  _build_spaces  →  classify_polygons  →  BEMModel / measurement_layer
```

`classify_polygons` runs after `_build_spaces`. It adds a `poly_type` field to each space so that area rollups and takeoff can exclude non-room polygons.

## Classification taxonomy

| poly_type | Meaning | Criteria |
|---|---|---|
| `room` | Labeled room with number | `has_room_number=True` and `area_m2 ≥ 8` |
| `room` | Labeled small room | `has_room_number=True` and `area_m2 < 8` (lower confidence) |
| `shaft` | Vertical chase / utility shaft | `area_m2 < 2` and no room number |
| `closet` | Storage / equipment closet | `2 ≤ area_m2 < 8` and no room number, OR small unclassified polygon |
| `elevator_core` | Elevator shaft / stairwell | Label contains "elevator", "elev", "stair" |
| `unassigned` | Default for large unlabeled polygons | Large polygon with no distinguishing features |

## Evidence used

All evidence is extracted from the building generator output:

- `area_m2` — polygon area in square meters
- `aspect_ratio` — width / depth from bounding rect; > 1 for elongated shapes
- `has_room_number` — whether the space has a non-empty room number string
- `label_text` — raw name field (matched case-insensitively)
- `n_doors` — count of south-facade windows/doors assigned to this room (future: from adjacency)
- `n_adjacent_spaces` — wall-sharing neighbors (computed from `grids_h`; v1 always 0)

## Decision order

The classifier evaluates rules in priority order:

1. **Tiny unnumbered** (`area < 2 m²`, no number) → `shaft` (conf 0.95)
2. **Small unnumbered** (`2 ≤ area < 8 m²`, no number) → `closet` (conf 0.90)
3. **Numbered large** (`has_number`, `area ≥ 8 m²`) → `room` (conf 0.95)
4. **Numbered small** (`has_number`, `area < 8 m²`) → `room` (conf 0.80)
5. **Elevator label** → `elevator_core` (conf 0.90)
6. **Stair label** → `elevator_core` (conf 0.90)
7. **Shaft/chase label** → `shaft` (conf 0.90)
8. **Closet/storage/utility label** → `closet` (conf 0.85)
9. **Small unclassified** (`area < 4 m²`) → `closet` (conf 0.70)
10. **Everything else** → `unassigned` (conf 0.50)

## Confidence scoring

Confidence is fixed per rule (not Bayesian). Higher confidence means the rule
has strong geometric or textual evidence; lower confidence indicates a default
or fallback classification.

## Coordinate frame

All measurements are in the canonical building model frame (meters, y-down).
No coordinate transform is needed.

## Limitations

1. **Adjacency unused** — `n_adjacent_spaces` is always 0 in v1; a shaft touching a room wall would still classify as `shaft` by area alone.
2. **South-facade windows only** — `n_doors` reflects only south-facade window counts; doors on other facades are not counted.
3. **Label text brittle** — keyword matching on label text ("elevator", "stair", "shaft") is fragile; synonyms or typos cause misclassification.
4. **No polygon shape analysis** — elongated shapes that should be shafts might be classified as closets if under 8 m²; aspect ratio is computed but not used in classification.
5. **No training data** — all thresholds (2 m², 8 m², 4 m²) are heuristic; no cross-validation against labeled data.
