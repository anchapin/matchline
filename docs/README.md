# docs/

Design notes, module guides, and experiment records for Matchline.
Each page documents what works, what doesn't, and why — limitations are
disclosed, not hidden.

## Start here

| Doc | What it covers |
|---|---|
| `../README.md` | Repo front page: install, CLI, pipeline overview |
| `../ARCHITECTURE.md` | Full domain map referenced by CONTRIBUTING.md |
| `repo-map.md` | Generated Mermaid map of module dependencies; pans and zooms on GitHub. Regenerate with `scripts/generate_repo_map.py` |
| `pipeline.md` | Stage chain, intermediate JSON artifacts, auto-triage integration |
| `cli.md` | CLI reference: `matchline` command-line interface |
| `building_model.md` | Canonical `BuildingModel`: spaces, provenance, review queue |
| `validation.md` | The 49-check invariant battery and tolerance rationales |

## Ingestion and registration

| `drawing_scale.md` | Drawing scale (`drawing_scale.py`) |
| `grid_detect.md` | Column grids and grid bubbles (#742) |
| `pdf_ingest.md` | PDF drawing-set ingestion (`pdf_ingest.py`) |
| `pdf_schedules.md` | Schedule tables from PDF sheets (#746) |
| `registration.md` | Sheet registration: plan + elevation affine alignment into canonical metres |
| `room_labels.md` | OCR room names/numbers and polygon association |
| `plan_walls.md` | Walls and rooms (`plan_walls.py`) |
| `polygon_classify.md` | Non-room polygon classification (shafts, closets, elevator cores) |
| `sheet_index.md` | Sheet index (`sheet_index.py`) |
| `link.md` | Cross-sheet linker building BuildingModel from plan + elevation annotations |
| `measurement_layer.md` | Measurement/uncertainty conventions across the pipeline |
| `datasets_adapter.md` | Dataset adapters bridging external datasets to the Matchline pipeline |
| `real_set.md` | Running a real drawing set (#741) |

## Detection and symbols

| `ecosystem_audit.md` | Detector-strategy audit: YOLO + tiling + legend learning rationale |
| `detection_provider.md` | Detection provider (#743): shippable door/window detector behind a provider interface |
| `detector-robustness.md` | Synthetic-degradation robustness eval (`detector/degrade.py`, `detector/eval_robustness.py`) |
| `elevation_windows.md` | Exact window placement from elevations + daylight zones |
| `facade_takeoff.md` | Facade wall/glazing/door fractions (CMP Facade) + priors |
| `legend_learning.md` | Learned legend tags (#995) |
| `symbols.md` | Programmatic GD&T symbol rendering for synthetic training data |
| `synthetic_data.md` | Synthetic sheet/dataset generation |
| `synth/README.md` | synth/ module guide: sheets.py, mech.py, GT schemas, conftest fixture relationships |

## HVAC

| `hvac_trace.md` | Duct tracing → terminal units → zone graphs |
| `mech_schedule.md` | Mechanical schedule parsing and system-type classification (#974) |

## BEM and export

| `bem_export.md` | gbXML 8.01 + IFC4 export paths and validation |
| `construction_library.md` | Cited construction library for envelope U-values (ASHRAE 90.1-2019, #747) |
| `geometry_simplify.md` | Area-budgeted BEM surface reduction |
| `lighting.md` | Fixture takeoffs, building watts, per-space LPD |
| `space_merge.md` | Closet and shaft merging |
| `space_use_defaults.md` | Space-use defaults (#685) |
| `ifc_import.md` | Tier-0 IFC import: what recovers without space boundaries |
| `ifc_export.md` | IFC4 export pathway: geometry + space boundaries + classification |
| `export_package.md` | Export package and trust report (#750) |

## Review, feedback and trust

| `run_review.md` | `matchline review` — review queue triage, `ReviewItem` fields, `validate.export_gate` blocking |
| `review_classifier.md` | Local ML triage layer for the extraction review queue |
| `review_report.md` | HTML review report (`out/review/`), decisions file and `matchline review --apply` replay |
| `review_viewer.md` | Review viewer: a read-only 3D view of the model (#1001) |
| `corrections.md` | `corrections.py` — Correction history (#997) |
| `revision_tracking.md` | Revision tracking and takeoff diffs |
| `trust-sign-off.md` | Trust report and sign-off workflow |
| `telemetry-data.md` | What leaves your machine: matchline telemetry data page |

## Research and experiments

| `jesse.md` | WiSARD classifier + Zhang-Suen skeleton invariants + thick-stroke preprocessing |
| `belief_derivation.md` | Derivation of the WiSARD belief/normalization formulas |
| `r1_vector_spike.md` | R1 vector spike (#973) |
| `sim_smoke.md` | Simulation smoke test (#752) |
| `adr/` | Decision records |
| `design-docs/` | Design documents |
| `plans/` | Work plans (see `PLANS.md` for conventions) |

## Ops and governance

| `ci.md` | CI gates, test count regression policy, and merge requirements |
| `CODE-REVIEW.md` | Code review standards and per-PR checklist (severity levels, domain-specific focus, anti-patterns) |
| `PLANS.md` | Planning conventions and plan template (3-digit prefix, work/designs folders, Status lifecycle) |
| `release.md` | Release notes and scope: v0.1.0 |
| `risk-register.md` | Risk register |
| `license_ledger.md` | License ledger (#751) |
| `data-contribution-agreement.md` | Data-contribution agreement (template, v1.0) |
| `safe_xml.md` | Hardened XML parser for XXE prevention |
| `process/branch-hygiene.md` | Branch cleanup policy for a squash-merge repo: 3-criterion deletion gate, the triage script, when not to delete, recovery commands |

## Quick Start

```bash
pip install -e ".[test]"        # (1) editable install with test extras
python -m pytest tests/ -q      # (2) smoke test — 3,500+ tests, a few minutes
matchline run --seed 0          # (3) minimal pipeline: generate + link + validate + export
```

See the [repo front page](../README.md), browse the groups above, or open
`repo-map.md` for the generated module graph. Start with `ROADMAP.md` (repo
root) for where the project is going.
