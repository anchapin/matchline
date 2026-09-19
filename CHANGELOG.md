# Changelog

All notable changes to this project are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) style.
`main` is reserved for releases; `develop` is the working branch.

## [Unreleased]

### Changed
- Renamed the project from `wisard-bem` to **Matchline**: distribution name,
  GitHub repo (`anchapin/matchline`), docs, and the `matchline` CLI. The old
  `wisard-bem` command still works as a deprecated alias (removal in 0.2.0).
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
- 26-check validation battery; errors block export.
- gbXML 6.01 + IFC4 export (schema/round-trip validated).
- Tier-0 IFC import frontend: IFC4 → canonical model without space
  boundaries (spaces, elements, openings, material layers, zones).
- Detector track: YOLO dataset converters (CubiCasa/AEC/FloorPlanCAD),
  training harness, SAHI tiling inference.
- `ROADMAP.md`: seven hard problems with initial technical framing
  (wall thickness, sloped roofs, skylights, non-room polygons, shading,
  per-segment constructions, IFC frontend).
- BSD-3-Clause license.
