# Changelog

All notable changes to this project are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) style.
`main` is reserved for releases; `develop` is the working branch.

## [Unreleased]

### Added
- A scheduled window tag (e.g. SF-1) on a plan wall with no opening drawn: when its scheduled width spans the wall it is modelled as a full-length storefront window (`plan_wall_tag`, confidence 0.7); a narrower tagged door or window there goes to review as a possible undrawn opening. `walls_NNN.json` gains `wall_tags` (#793).
- A facade drawn in parts (two sheets, or titles like "SOUTH ELEVATION - EAST HALF") is joined as a whole: each part is placed by shared grid labels or the end its title names, covers its own span, and a plan opening goes to review only when no part shows it. An opening two elevations draw differently goes to review once, naming both sheets (#818).
- Run config `wall_height` takes a per-level map (`{L1: 4.5, L2: 3.6}`) for drawing-set runs; named levels use it and the rest fall through to the elevation level marks. Config `wall_height` now reaches the drawing-set run at all (before, only `--storey-height` did); unknown level ids fail the run (#821).

### Added
- Level marks match more plan levels: GROUND then FIRST FLOOR on one elevation reads the British way, MEZZANINE and PENTHOUSE marks match MEZZ and PH, and marks that name no plan level match by order when the storey count agrees (`height_match` per level) (#822).

### Added
- Each level takes its own storey height from the elevation level marks (matched by name: FIRST FLOOR to L1, SECOND FLOOR to L2); level elevations are the running sum, and only levels whose marks disagree or are missing keep the default and go to `rq-storey-height` (#814).

### Changed
- Elevation join review items use the window kinds: size mismatches and plan openings missing from the elevation are `elevation_conflict`, elevation openings with no plan opening are `window_room_link`; ids, targets and triage task unchanged (#817).

### Added
- Review page draws the elevation sheets the drawing-set run read (facade outline, windows and doors joined or not), and an item that joins two sheets highlights both ends, labels each with the other sheet, and offers a bar to jump between them (#801).

### Added
- Elevation join review items name both sheets (`target.ends` with the elevation box and the plan point), and elevation JSON records each opening's matched plan opening (#810, third slice).

### Added
- Storey height comes from elevation level marks when they agree (checked against where each mark is drawn); disagreeing marks keep the default and go to review (#810, second slice).

### Added
- Elevation openings join the plan's: matched windows and doors get their sill and head height from the elevation, schedule size kept; disagreements and unmatched openings go to review (#810, second slice).

### Added
- Drawing-set run reads elevation sheets: windows and doors with sill/head heights, registered to the plan facade by shared grid labels or outline length; unnamed or mismatched elevations go to review (#810, first slice).

### Added
- Plan windows: full-length or extra-wide glazing broken by at least two
  mullion ticks is a window (`source: "glazing_mullions"`, confidence 0.6);
  a cavity line along the whole wall still is not (#793).
- Plan openings: a schedule tag written next to a plan gap picks its schedule row, even where same-width rows disagree; a contradicting tag goes to review (#793).
- CI: a browser-level test drives the review page in headless Chromium and
  replays its exported decisions with `matchline review --apply` (#802).
- Review: an edit on an `opening_unsized` item adds the opening the pipeline left
  out, from a schedule tag or typed sizes, checked against the drawn gap before
  anything changes; revert, confirm or reject remove it (#798).

### Changed
- gbXML export now writes and validates against schema 8.01 (was 6.01), using a
  local copy of the 8.01 xsd whose `versionEnum` adds `7.03` and `8.01`; the
  published 8.01 schema stops at 6.01 and rejects its own version (#804).
- Renamed the project from `wisard-bem` to **Matchline**: distribution name,
  GitHub repo (`anchapin/matchline`), docs, and the `matchline` CLI. The old
  `wisard-bem` alias was removed in 0.2.0.
  The WiSARD paper-reproduction module (`jesse.py`) keeps its name.

### Added
- `pyproject.toml`: the project is installable (`pip install -e .`), version
  0.1.0, with declared runtime dependencies and optional `ocr`, `test`, and
  `detector` extras.
- Unified `matchline` CLI with subcommands for every demo script
  (`validate`, `bem-export`, `elevation-windows`, `facade-takeoff`,
  `room-labels`, `multidiscipline`, `mnist`, `symbols`, `ifc-import`).
- GitHub Actions CI: install, `ruff check`, `ruff format --check`, pytest on
  push/PR to `develop` and `main`.
- Ruff lint/format config, `.pre-commit-config.yaml`.
- `CONTRIBUTING.md`, `RELEASING.md`, `SECURITY.md`, issue/PR templates,
  `docs/README.md` index.
- `geometry_simplify` now raises a clear `ImportError` (install hint) instead
  of falling back to a machine-specific vendored shapely.

## [0.1.0] - 2026-09-19

Initial public development snapshot on `develop`.

### Added
- WiSARD classifier reproduction (thermometer encoding, empirical-Bayes
  normalization, COM normalization, skeletonization): 94.57% on MNIST.
- Drawing pipeline: symbol/schedule takeoffs (`count × width × height`), OCR
  room labeling, area-budgeted geometry simplification (≤2% drift default),
  lighting takeoffs + LPD, HVAC zoning from duct tracing.
- Exact elevation windows: along-wall position, sill/head heights, daylight
  zones, two-elevation dedup, reconciliation review queue.
- CMP Facade takeoffs: wall/glazing/door fractions + dataset priors.
- Canonical `BuildingModel`: architectural spaces as the canonical entity,
  provenance on every fact, review queue, cross-discipline linking.
- 28-check validation battery; errors block export.
- gbXML 6.01 + IFC4 export (schema/round-trip validated).
- Tier-0 IFC import frontend: IFC4 → canonical model without space
  boundaries (spaces, elements, openings, material layers, zones).
- Detector track: YOLO dataset converters (CubiCasa/AEC/FloorPlanCAD),
  training harness, SAHI tiling inference.
- `ROADMAP.md`: seven hard problems with initial technical framing
  (wall thickness, sloped roofs, skylights, non-room polygons, shading,
  per-segment constructions, IFC frontend).
- BSD-3-Clause license.
