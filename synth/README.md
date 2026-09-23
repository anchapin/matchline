# synth/ — Synthetic Data Generators

Deterministic, fixture-quality synthetic drawings for testing the full pipeline
without real CAD files or external datasets.

## Modules

| Module | What it generates | GT schema |
|---|---|---|
| `symbols.py` | Individual CAD glyph crops (28×28) for classifier training | — |
| `sheets.py` | Complete architectural floor-plan sheets with rooms, walls, doors, windows, labels, schedules | rooms, symbols, labels, schedule, expected totals |
| `mech.py` | Mechanical HVAC floor-plan sheets with AHU, VAVs, ducts, diffusers, grilles, sensors | rooms, components, ducts, graph, crossings, zones |
| `multidiscipline.py` | Five-sheet coordinated building: arch + lighting + mech + elev_grid + elev_nogrid | cross-sheet links (fixture→room, sensor→room, etc.) |
| `lighting.py` | Lighting plan sheets with fixture layouts per room type | fixtures, room assignments |
| `lighting_sheets.py` | Helper: default fixture plan and room fixture mappings | — |

## sheets.py — Architectural Floor Plans

**Purpose:** Compositional synthetic floor-plan sheet generator. Procedurally lays out a
complete floor plan — rectangular or L-shaped footprint, guillotine-subdivided into 4–8 rooms,
doors/windows placed in walls with carved gaps, room name+number labels, and a
window/door schedule table — rendered at drawing-like resolution.

**Sheet generation taxonomy:**

```
generate_sheet(seed)
  └─ _layout_footprint()          → rects + rooms (meters, y-down)
       └─ _subdivide()            → guillotine room subdivision
  └─ _classify_edges()            → interior / exterior wall segments
  └─ OpeningPlacer               → conflict-free opening placement on wall lines
  └─ render_and_paste()           → glyph functions from synth.symbols
       └─ symbols + labels        → room labels, door/window symbols
  └─ draw_table()                 → WINDOW SCHEDULE + DOOR SCHEDULE
  └─ GT dict                      → rooms, symbols, labels, schedule, expected
```

**GT schema** (ground-truth JSON returned alongside the sheet image):

```
{
  "sheet_id":        "sheet_NNN",
  "seed":            int,
  "scale_px_per_m":  50.0,
  "sheet_size_px":   [w, h],
  "footprint":       "rect" | "L",
  "rooms": [{
    "name", "number", "polygon_m", "polygon_px", "area_m2"
  }],
  "symbols": [{
    "type":  "Single Swing Door" | "Double Swing Door" | "Window",
    "tag":   "A" | "B" | "D1" | "D2",
    "bbox_px": [x0, y0, x1, y1],
    "drawing": "floor_plan"
  }],
  "labels": [{
    "text", "bbox_px", "room_idx"
  }],
  "schedule": [{ "tag", "category", "width_m", "height_m" }],
  "expected": {
    "floor_area_m2", "window_area_m2", "door_area_m2",
    "counts": { "A": n, "B": n, "D1": n, "D2": n }
  }
}
```

**Coordinate frame:** layout in meters, y growing downward (image coords). `PX_PER_M = 50`.

**Design constraint:** Door/window glyphs reuse `synth.symbols` glyph functions so the
sheet distribution matches the classifier training distribution exactly.

**Entry points:**

- `generate_sheet(seed)` → `(image uint8, gt_dict)`
- `save_sheet(seed, outdir)` → `(png_path, json_path)`

---

## mech.py — Mechanical HVAC Plans

**Purpose:** Synthetic mechanical floor plans for the HVAC zoning spike. Generates clean
vector-style mechanical plans: rectangular footprint, guillotine-subdivided rooms, one
AHU feeding a supply trunk, 1–3 VAV terminal units each serving a contiguous zone of
rooms via branch ducts to supply diffusers, a return-air system (grilles + return main),
and one temperature sensor (circle + "T") per room.

**Sheet generation taxonomy:**

```
generate_mech_sheet(seed)
  └─ _layout_mech(rng)            → _Net (duct graph) + GT dict + room_info
       └─ column-strip zones      → VAV zones are vertical strips
       └─ AHU + VAV + diffusers + grilles + sensors
       └─ _Net.add_piece()         → axis-aligned duct segments
       └─ _Net.apply_crossings()  → gap crossings (return ducts / supply trunk)
  └─ render_mech_sheet(net, gt, room_info)
       └─ walls + room labels
       └─ duct bars (solid filled, axis-aligned)
       └─ symbol glyphs (VAV, AHU, diffuser, grille, sensor)
  └─ GT dict                     → rooms, components, ducts, graph, crossings, zones
```

**GT schema:**

```
{
  "seed", "px_per_m", "W_m", "D_m",
  "rooms":    [{ "id", "name", "number", "rect_m", "area_m2" }],
  "components": [{ "id", "type", "x_m", "y_m", "w_m", "h_m", "tag", "room_id", "vav" }],
  "ducts":   [{ "id", "width_m", "pieces_m": [[[x0,y0],[x1,y1]], ...] }],
  "graph": {
    "nodes": [{ "id", "kind", "x_m", "y_m" }],
    "edges": [{ "a", "b", "duct", "length_m" }]
  },
  "crossings": [{ "x_m", "y_m", "gapped", "kept" }],
  "zones": [{
    "zone_id", "vav_id", "room_ids", "sensor_ids",
    "diffuser_ids", "duct_length_m"
  }]
}
```

**Duct crossing gaps:** Crossings between independent nets (return vs. supply) are
rendered with a GAP in exactly one duct (return ducts first, then supply trunk).
Taps, spines, and drops are never gapped, so every supply zone subgraph stays
connected in the raster.

**Symbol templates (NCC):** `render_template(cls)` produces grayscale templates with
optional duct stubs baked in. Stubs are required for VAV/AHU templates — without
them, sheet duct ink fills template-white regions and NCC scores < 0.5.

**Training crops:** `training_crops_from_sheets(n_per_class, seed, bg_per_sheet)` cuts
crops from synthetic sheets at GT positions (±6 px jitter), plus random background
crops (≥ 60 px from any symbol).

**Entry points:**

- `generate_mech_sheet(seed)` → `(image, gt_dict)`
- `save_mech_sheet(seed, outdir)` → `(png_path, json_path)`
- `render_template(cls, stubs=...)` → `np.ndarray` (NCC template)
- `detection_crop(gray, cx_px, cy_px, cls)` → `np.ndarray` (WiSARD crop)
- `training_crops_from_sheets(...)` → `(X, y, classes)`

---

## Relationship to conftest.py fixtures

`tests/conftest.py` defines the `bldg_3room` fixture:

```python
@pytest.fixture(scope="module")
def bldg_3room():
    """3-room building, grid elevation path. Used by test_units.py."""
    bldg = generate_building(seed=101, ...)
    model, report = build_model(bldg, elevation_key="elev_grid", ...)
    return bldg, model, report
```

`generate_building` comes from `synth.multidiscipline`, which **calls** both
`synth.sheets` (for the architectural sheet) and `synth.mech` (for the HVAC sheet).
The individual `synth/sheets.generate_sheet()` and `synth/mech.generate_mech_sheet()`
functions are NOT directly used by conftest — they are used by:

- `synth/e2e_test.py` — end-to-end pipeline validation on individual sheets
- `synth/make_dataset.py` — dataset generation for classifier training
- `synth/gap_experiment.py` — sim-to-real transfer evaluation

The discipline-specific sheets (`sheets.py` architectural, `mech.py` HVAC) feed into
`multidiscipline.py` which assembles them into a coherent multi-sheet building with
cross-sheet GT links. The conftest fixture tests the **linked** model output, not the
individual sheet generators directly.

## Limitations

- **Tier-1 glyphs:** All synthetic symbols use a single drafting style; real AEC
  drawings exhibit much more variation. Sim-to-real transfer for non-window symbols
  is poor (27% vs 44% real→real baseline).
- **mech.py is scope-limited:** Ducts are solid filled bars (not double-line
  outlines). Wall-pairing into centerlines is a known v2 step.
- **No rotated rooms:** Both generators use axis-aligned layouts only.
- **External font required:** `sheets.py` requires
  `/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`; mech.py inherits the same
  dependency via `sheets._font`.
