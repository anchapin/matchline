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
```

- `none` (the default when no config is given): no detections; plan sheets report
  the symbols stage as skipped, as before.
- `precomputed`: reads `<dir>/sheet_NNN.json` files written by
  `detector/sahi_infer.py` for the rendered sheet `sheet_NNN.png`. A sheet without a
  file gets no detections.
- `yolo_sahi`: runs tiled YOLO inference on each floor plan's rendered image (needs
  `ultralytics`; CPU unless `device` says otherwise). Optional keys: `tile`,
  `overlap`, `conf`, `device`.

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

## Not yet

A door/window backend whose training data and weights may ship, its measurement on
commercial sheets (F1 at IoU 0.50) beside the CubiCasa baseline, joining detections
to plan openings and mechanical tags, and a vector-path provider.
