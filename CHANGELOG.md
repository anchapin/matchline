# Changelog

All notable changes to this project are documented here, in
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) style.
`main` is reserved for releases; `develop` is the working branch.

## [Unreleased]

### Fixed
- HVAC detection: a grille or diffuser whose WiSARD label fails the tight confirm takes the NCC proposal's class when the proposal named the other terminal class and that class's tight template scores at least 0.76 (`hvac_trace.RELABEL_CONFIRM_NCC`, `_confirm_label`); a label that confirms is never changed. Clinic grilles (detector measured only): clean unchanged at 123/0 and 83/0 TP/FP; line weight 94 to 118 and 63 to 81 found; scale 76 to 96 and 62 to 69; `--vary 1` 11 to 18 (one more false grille) and 17 to 19; `--vary 2` 19 to 28 and 12 to 17. Diffusers unchanged. The 0.76 bar was read off Clinic; follow-up #887 (#745).
- Door detection: a door whose far jamb is a wall that a crossing wall meets in a T right past the opening is now found; when the jamb is seen on the first row past the opening but the second row runs into the crossing wall, the jamb is read again further along (`door_detect._canonical`). Synthetic door went from missed in 8 of 8 orientations to found in all 8, and the same jamb with no gap is still not a door. Clinic doors (plumbing only): first floor 141 to 142 of 148, recall 0.959 to 0.966, precision unchanged at 1.0; second floor unchanged (#875).
- Door detection: a door whose leaf is drawn just off a crossing wall's face (separable from it, so not read as a leaf on the face) is now found when its hinge wall runs on beyond that crossing wall; when the row behind the hinge runs along the crossing wall, the hinge wall is read on its far side (`door_detect._canonical`, `_run_through`). Synthetic door went from missed in 8 of 8 orientations to found in all 8, and the same leaf against an unbroken wall is still not a door. Clinic doors (plumbing only): first floor 140 to 141 of 148, recall 0.953 to 0.959, precision unchanged at 1.0; second floor unchanged (#875).
- Door detection: a door whose far jamb is the hinge stub of a door hung in the crossing wall (that door's leaf lying just off the wall line across the opening, the two swings crossing) is now found; when the wall just past the jamb gives no clear opening, the jamb wall is read again further along (`door_detect._canonical`). Synthetic stub-jamb door went from missed to found in all 8 orientations, and a stub against an unbroken wall is still not a door. Clinic doors (plumbing only): first floor recall 0.946 to 0.953, second floor 0.917 to 0.927, precision unchanged at 1.0 and 0.989 (#875).
- CI: the empty-commit check skips merge commits. `git diff-tree` lists no files for a merge, so every merge of `develop` into a PR branch failed the check; plain commits are still checked.
- Plan walls: a window through an asymmetric double-leaf wall whose glass line falls on one leaf's cavity face is now found; the two walls that meet on the glass line and together fill the glazed band no longer count as a wall inside the band (`plan_walls._split_by_glass_line`). Synthetic 0.10/0.10/0.20 m leaf/cavity/leaf walls (and three like them) went from missed to found; Clinic walls, rooms and windows are unchanged (#877).
- Plan walls: a short lined stretch (a lining a gap off the wall's inside face, closed at both ends by its return and a corner) is no longer called a window; a thinner parallel wall flush against a glazed band's face now rejects the candidate like one inside it (`plan_walls._wall_inside_band`). Synthetic 1.64 m stretch went from a false window to none; Clinic walls, rooms and windows are unchanged (#879).
- Door detection: a door hung on one face line of a hollow jamb wall (two thin lines with a clear gap wider than two leaves) is now found; the hollow wall also offers its face lines as the hinge, tried with that wall alone, and the arc, leaf-reach and clear-opening checks are unchanged (`door_detect._hinge_options`, `_hollow`). Clinic doors (plumbing only): first floor 139 to 140 of 148, recall 0.946 to 0.953, precision 1.0; second floor unchanged (#875).
- Plan walls: a window through a double-leaf (cavity) wall whose cavity is as wide as a leaf or wider is now found; the two leaves, flush with the glazed band's faces on both sides, count as the wall running on past it (`plan_walls._collinear_run`). Synthetic 0.10/0.10 and 0.10/0.15 m leaf/cavity walls went from missed to found; Clinic walls, rooms and windows are unchanged (#872).
- Plan walls: a glazed band that holds another, thinner parallel wall (a box, chase or pier lining built against the wall) is no longer called a window (`plan_walls._wall_inside_band`); walls and rooms are unchanged. Clinic windows (plumbing only): first floor 23 to 22 found, precision 0.957 to 1.0; second floor 37 to 36, precision 0.973 to 1.0; recall stays 1.0 (#868).

### Added
- Semi-exterior envelope: each wall two spaces share is classified from their ASHRAE 90.1-2019 Section 3.2 categories (`Space.conditioning`, #895). Conditioned against semiheated or unconditioned is semi-exterior envelope (Figure 5.5.2) and lands on `BuildingModel.semi_exterior`, with the space pair, both categories, shared length and segments. A wall that would be semi-exterior if an unsettled side (category `review`, or no zone found) turned out conditioned gets a `semi_exterior_envelope` review item instead of a default. Two spaces with no category stay silent, and semiheated against unconditioned is not semi-exterior. The gbXML and constructions don't read it yet, so exports are unchanged (`semi_exterior.semi_exterior_boundaries`, `real_set._semi_exterior`) (#747).
- Door detection: a door hung beside a wall that meets its hinge jamb is found when its open leaf is drawn on that wall's face (`door_detect._face_leaf`); the arc, jamb and clear-opening checks are unchanged. Clinic doors (plumbing only): recall 0.932 to 0.946 on the first floor, 0.896 to 0.917 on the second, precision unchanged (#743).
- Clinic validation: `scripts/validate_clinic_walls.py --doors` draws each IfcDoor opening as a leaf-and-arc swing on a 50 px/m image of the cut walls and scores `door_detect` against the IFC doors (recall, precision; `--png-dir` saves the sheets). An opening wider than one leaf is drawn as a pair of leaves. Plumbing only: we choose how the swing is drawn (#743).
- HVAC detection: the tight grille passes and the confirm gate also try the return grille drawn as a box of parallel blades with no diagonal (`hvac_trace.TEMPLATE_STYLES`, glyph `synth.mech._ALT_GLYPH_FN`), so a grille in that common drafting style is found and kept as a grille. Diffusers keep one style (#745).
- HVAC detection: diffuser and grille templates are also tried at 0.87x and 1.15x (`hvac_trace.TEMPLATE_SCALES`) for proposals, the tight relabel passes and the confirm gate, so a terminal drawn up to 15% smaller or larger than ours is still found (#745).
- HVAC detection: the stub-less grille template used to relabel and confirm grilles is now also tried at a quarter turn, so a grille whose diagonal runs the other way is kept as a grille instead of being dropped or left labeled diffuser. The diffuser X looks the same turned and keeps one template (`hvac_trace.TIGHT_TURNS`, #745).
- Set runs: supply diffusers, grilles and thermostats found from a mechanical sheet's own legend now reach the HVAC model. A symbol placed in a room goes on the space (`hvac.diffusers` / `hvac.sensors`) and on its zone when exactly one zone serves that room (method `legend_symbol`); rooms with no zone or several zones keep the counts in `symbols_by_room` only, and a symbol repeated on a second sheet of the level is not added twice. A zone on a level read this way that gets no supply diffuser goes to review as `zone_no_diffuser` (#744).
- Clinic detection check: `--vary-only NAMES` (with `--vary SEED`) draws only the named variations (scale, weight, turn, tilt, skew, style, knockout, text, scan, shift), each at the value the seed gives it with all of them on, so the recall loss can be put down to one variation at a time (#745).
- Clinic detection check: `scripts/validate_clinic_hvac_detection.py --vary SEED` redraws the terminals the way another firm or a scan would (scale +-15%, line weight 2-4 px, a quarter turn plus up to 5 degrees tilt, slight shear, a four-way-arrow diffuser and a hatched grille as alternative styles, duct and tag text over half the symbols, then scan noise, JPEG and a 0-2 px raster shift). Seeded and deterministic; the clean render stays the default and is unchanged, and no detector threshold changes (#745).
- Set runs: legend symbol hits on a mechanical plan that registers to its level's architectural plan are put in the room they fall in; each `hvac_legends` entry gains `symbols_by_room` (space id -> class -> count, `""` for no single room) and the note says how many landed in rooms. Mechanical plan registration is now one helper shared with equipment tag placement (#744).
- Set runs: each mapped row of a mechanical symbol legend is cut from the sheet's own raster as a one-shot template and matched across that sheet (`hvac_legend.legend_templates`, `hvac_trace.detect_legend_symbols`, NCC accept 0.80, no WiSARD pass), so a firm's own diffuser or thermostat symbols are found where matchline's built-in glyphs propose none. Each legend on the report gains `symbol_hits` (class -> count); the counts are not yet joined to zones. `hvac_trace.merge_legend_detections` keeps the built-in templates as the fallback for classes the legend does not draw (#744).
- Set runs: the symbol legend on each vector mechanical sheet (a SYMBOL LEGEND / HVAC LEGEND / SYMBOLS title over rows of a drawn symbol and its description) is read by the new `hvac_legend` module. Each row's description is mapped to an HVAC detector class (supply diffuser, return/exhaust grille, VAV, AHU, thermostat/sensor) from its words only; rows go on the report under `hvac_legends` with the symbol's box, and rows naming no class go to one `hvac_legend_unmapped` review item per legend. The symbols are not yet used for detection (#744).
- Set runs: an HVAC zone made from a placed VAV or fan coil now carries its terminal unit's scheduled design airflow (`Zone.design_airflow_max_m3s` / `design_airflow_min_m3s`, converted to m3/s from the schedule's MAX/MIN CFM or L/S columns; a fan coil's single airflow column is its max). Airflow columns whose headers state no unit (`AIRFLOW` alone), a max that is not positive, or a min above the max are kept on the schedule record but not put on the zone; the zone's provenance note says which. Mechanical schedule records gain `airflow_unit`, and MAX/MIN L/S columns are now read as max/min (#746).
- Set runs: a door drawn inside a storefront run is modelled as a door when the storefront's window row is as wide as the run less its doors (the row is then the glass alone, centred on the glass that is left); a door no scheduled door explains goes to review. A window row as wide as the whole run still includes its doors. Before, such a storefront went to review whole and nothing was modelled (#793).
- Plan walls: a door drawn inside full-length glazing with no gap in the wall (two jamb ticks with a single or double swing between them) no longer counts its jambs as mullions, so a cavity line with a door is not a false storefront; a storefront window that still qualifies lists the door under `doors_in_glazing` (#793).
- Plan walls: a thin wall band between thicker walls whose face lines are on a glazing CAD layer (such as `A-GLAZ`) is a window over its whole length (`glazing_layer_band`, confidence 0.7) instead of a `maybe_glazing` review question; on any other layer it stays flagged as before (#793).
- PDF CAD layers: ingestion records each vector path's optional content group (layer) name and each sheet's layer list. On plans, glazing that runs a whole wall counts as a window (`glazing_layer`, confidence 0.7) when it sits on a glazing layer such as `A-GLAZ`, with or without mullion ticks, and a middle line on a pattern or insulation layer such as `A-WALL-PATT` is never a window (#793).
- Lighting power per room on a drawing set: lighting schedule tags on a reflected ceiling plan or electrical floor plan are counted as fixtures in the room that contains them, giving `Space.lighting` fixtures, watts and LPD (one tag counted as one fixture, so each lit level goes to review); drawing lighting now reaches the gbXML `LightPowerPerArea` on the set path, which it did not before (#746).
- Plan walls: a thinner wall on the same line filling at least 80% of a gap in a thick wall run stops the run there, so a glazed bay narrower than `MAX_OPENING_M` (2.5 m) is kept as its own wall and flagged `maybe_glazing` instead of being covered by one continuous opaque wall (#793).
- Heated slab: when the radiant floor rows name the ground-floor rooms they serve, only those rooms' exposed slab edge gets the Table 5.5 heated F-factor and the rest the unheated one (`slab_heated_spaces`); otherwise the whole slab stays heated (#747).
- Plan walls: a thin wall band between thicker walls on the same line, with no opening or scheduled tag, is flagged `maybe_glazing` and goes to review as possible storefront glazing; the wall stays opaque (#793).
- Slab-on-grade exposed perimeter counts every ground-floor wing (not only the largest) and the edge of open-air courtyards, a hole in the slab with exterior walls along at least half its edge; other holes stay slab and are listed under `holes_not_exposed` (#747).
- Schedules drawn as whitespace-aligned text with no cell lines are read (`pdf_unruled_table`, confidence 0.75): columns from the header row, wrapped cells and note rows handled, crossing values and repeated tags flagged unparsed (#746).
- Plan walls that stop 2.5 to 4.5 m short of the wall ahead close with a reviewed air wall when that parts two labelled rooms (counters, half walls between named spaces); inside one named space they stay open (#740).
- Each placed VAV or fan-coil tag on a set run becomes an HVAC zone on the room it sits in (#746).
- Scheduled mechanical equipment tags on the mechanical plans are placed in rooms, registered to the architectural plan by the shared grid or the same sheet frame (#746).
- Wall-type legend descriptions wrapped onto several lines are read whole; the first line still sets the class when it names one (#828).
- Plan openings keep every nearby tag (`tags_near`); the nearest scheduled door/window mark picks the schedule row even when a wall-type tag sits closer, and the wall type still applies to the wall (#829).
- Drawing-set runs read the wall-type legend (`W1  8" CMU ...`) and give each tagged exterior wall a construction named by it; the library fills its U from Table 5.5 for that class instead of the unlabeled default. Conflicting legends and walls with two types go to review (#747).
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
