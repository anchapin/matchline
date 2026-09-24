# core-beliefs.md — agent-first operating principles

These principles define how work is done in this project. When in doubt, apply them.

## Every extracted fact carries provenance

Every derived fact that lands in `BuildingModel` must have a `Provenance` record: `sheet_id`, `revision`, `method`, `confidence`. This is not optional. Facts from a newer revision of the same sheet supersede older ones — kept in `history`, never silently overwritten.

**Why**: This is an audit-oriented pipeline. A human reviewer must be able to trace any number in the model back to the exact sheet, revision, and extraction method that produced it.

## Low-confidence results go to the review queue — never silently dropped

Links below `REVIEW_CONFIDENCE = 0.80` (`building_model.py`) are flagged for human review. The review queue is part of the model output; validation errors block export until the queue is acknowledged.

**Why**: Automating low-confidence links into the model would silently propagate errors into energy simulation results. It is better to surface uncertainty than to hide it.

## Conservation laws block export

`validate.py` enforces invariants (area closure, volume closure, envelope closure, LPD plausibility, cross-discipline referential integrity). An error is fatal — the export is blocked.

**Why**: Exporting an internally inconsistent model to gbXML or IFC would produce simulation results that cannot be trusted. Conservation law violations indicate a bug in the pipeline, not in the input.

## Synthetic before real

All pipeline paths are validated against `synth/` fixtures before any real-drawing run. Synthetic fixtures (`bldg_3room`, `bldg_open_office`, `bldg_8room`) are deterministic and fast.

**Why**: Real drawing data has unknown noise characteristics. A path that passes on synthetic data is not guaranteed to pass on real data — but a path that fails on synthetic data will certainly fail in production.

## The architectural floor plan is the canonical frame

Rooms are identified by `"{level}-{number}"` from the arch plan. All other discipline sheets (lighting, mechanical, elevations) are registered into arch-plan coordinates. No cross-sheet ID matching.

**Why**: In practice, only the room number is shared across all discipline drawings. Using it as the primary key avoids fragile symbol or ID matching between sheets drawn by different trades.

## Zones are many-to-many with spaces

`Zone.space_ids` and `Space.hvac.zone_ids` are both lists. An open office can span two zones. There is no zone hierarchy tree.

**Why**: Real buildings do not always follow hierarchical zone trees. Open offices, shared ceilings, and multi-zone AHUs create genuine many-to-many relationships. Modeling this correctly avoids incorrect zone assignments.

## jesse.py is a research asset, not production code

The WiSARD classifier (`jesse.py`) is a deterministic, explainable paper reproduction. Production symbol spotting uses the fine-tuned YOLO pipeline in `detector/`.

**Why**: The WiSARD is auditable and paper-verified, but not the highest-accuracy approach for real drawing noise. It belongs in experiments, not in the critical path.

## Coordinate frame: y-down in model, north-up in BEM export

The canonical model stores coordinates in drawing frame (y growing downward). `bem_export.py` flips to north-up (standard BEM convention) at export time.

**Why**: Every synthetic layout and sheet raster uses y-down. Flipping at the export boundary keeps the model consistent with its inputs and keeps the conversion explicit in one place.

## Machine-specific paths are forbidden in library code

`~/workspace/...` paths must not appear in any `.py` file that ships. External datasets live under `~/workspace/datasets/` on developer machines and fail clearly when absent.

**Why**: Library code must be portable. A path that works on one developer's machine would break on another or in CI.
