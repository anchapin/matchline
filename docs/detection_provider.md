# Detection provider (#743)

Door and window detection sits behind `detection_provider.py`, so the backend is a
config value, not code (ROADMAP R2). A provider takes one sheet image and returns
`datasets_adapter.Detection` rows: label (`door` / `window`), score, pixel bbox and
source sheet. The takeoff join reads that shape whatever produced it.

## Choosing a provider

`matchline run --set set.pdf --detector-config det.json`, where `det.json` is one of:

```json
{"provider": "none"}
{"provider": "precomputed", "dir": "dets/", "artifact": "<ledger artifact path>"}
{"provider": "yolo_sahi", "weights": "best.pt", "artifact": "<ledger artifact path>"}
{"provider": "door_swing"}
{"provider": "vector_glazing"}
```

- `none` (the default when no config is given): no detections; plan sheets report
  the symbols stage as skipped, as before.
- `precomputed`: reads `<dir>/sheet_NNN.json` files written by
  `detector/sahi_infer.py` for the rendered sheet `sheet_NNN.png`. A sheet without a
  file gets no detections.
- `yolo_sahi`: runs tiled YOLO inference on each floor plan's rendered image (needs
  `ultralytics`; CPU unless `device` says otherwise). Optional keys: `tile`,
  `overlap`, `conf`, `device`.
- `door_swing`: finds doors by rule in each floor plan's rendered image
  (`door_detect.py`, see `docs/space_merge.md`): a straight leaf from the hinge
  jamb plus a quarter arc back to the far jamb, a wall at both jambs and a clear
  opening between them. It never guesses a door from a wall gap alone and finds
  no windows. No dataset or trained weights sit behind it, so its license class
  is permissive and a release may use it. It needs the drawing scale: the set
  reader passes each plan's rendered px per metre from that sheet's own scale
  (`dpi / 72 / m_per_pt`) and does not run it on a sheet with no scale. Optional
  keys: `px_per_m` (a fixed scale, used only when the caller passes none),
  `ink_max`, `min_width_m`, `max_width_m`.

An unknown provider name or a missing required key stops the run with the reason.

## License class

Every provider names the trained artifact behind it (`artifact`, defaulting to the
weights path for `yolo_sahi`). Its class is the worst class of the datasets the
[license ledger](license_ledger.md) lists for that artifact:

- permissive or share-alike: may ship;
- noncommercial, copyleft, or not in the ledger: evaluation only.

The set report records `detector: {provider, artifact, license_class, eval_only}`,
adds a note, and marks each plan sheet's symbols stage `eval_only: true` when the
detections came from evaluation-only weights. `provider_from_config(cfg,
release=True)` refuses an evaluation-only provider outright.

Today the only trained door/window weights are the CubiCasa5K YOLO11n runs, which
the ledger classes as noncommercial (and copyleft through the YOLO11n starting
weights): they work through this interface for evaluation, never for a release.

- `vector_glazing`: reports the windows the wall reader (`plan_walls`) already
  finds in the sheet's vectors: a glazing line drawn inside a wall run, a
  storefront run, and a band whose faces sit on a glazing CAD layer. Each
  `kind: "window"` opening becomes a `window` detection whose box spans the
  opening plus half its wall's thickness either side, in rendered pixels.
  Its score is the opening's `window_confidence`, and its tag is the tag the
  wall reader attached, if any. It finds no doors. Rules, not weights, so it is
  permissive and a release may use it. The set reader hands it each plan's
  walls result, sheet height and rendered px per pt. Metres from `plan_walls`
  are y-up from the sheet's bottom edge, and the image is y-down.

## Not yet

A trained door/window backend whose training data and weights may ship (the
`door_swing` rules ship but find swing doors only), measurement on commercial sheets (F1 at IoU 0.50) beside the CubiCasa baseline, joining detections
to plan openings and mechanical tags, and running door and window providers
together on one set (one provider per run today).

## Vector door swings

Vector plan sheets do not need a trained detector to find doors: the swing
arcs drawn in wall gaps are read directly (see `docs/plan_walls.md`, "Door
swings"). That path has no dataset behind it and is always on; the provider
here is for raster sheets and for symbol classes a plan does not draw as
geometry.
