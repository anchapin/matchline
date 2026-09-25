# docs/

Design notes, module guides, and experiment records for Matchline.
Each page documents what works, what doesn't, and why — limitations are
disclosed, not hidden.

| Doc | What it covers |
|---|---|
| `belief_derivation.md` | Derivation of the WiSARD belief/normalization formulas |
| `bem_export.md` | gbXML 6.01 + IFC4 export paths and validation |
| `building_model.md` | Canonical `BuildingModel`: spaces, provenance, review queue |
| `cli.md` | CLI reference: `matchline` command-line interface |
| `datasets_adapter.md` | Dataset adapters bridging external datasets to the Matchline pipeline |
| `ecosystem_audit.md` | Detector-strategy audit: YOLO + tiling + legend learning rationale |
| `elevation_windows.md` | Exact window placement from elevations + daylight zones |
| `facade_takeoff.md` | Facade wall/glazing/door fractions (CMP Facade) + priors |
| `geometry_simplify.md` | Area-budgeted BEM surface reduction |
| `hvac_trace.md` | Duct tracing → terminal units → zone graphs |
| `ifc_import.md` | Tier-0 IFC import: what recovers without space boundaries |
| `ifc_export.md` | IFC4 export pathway: geometry + space boundaries + classification |
| `jesse.md` | WiSARD classifier + Zhang-Suen skeleton invariants + thick-stroke preprocessing |
| `lighting.md` | Fixture takeoffs, building watts, per-space LPD |
| `link.md` | Cross-sheet linker building BuildingModel from plan + elevation annotations |
| `measurement_layer.md` | Measurement/uncertainty conventions across the pipeline |
| `pipeline.md` | Stage chain, intermediate JSON artifacts, auto-triage integration |
| `polygon_classify.md` | Non-room polygon classification (shafts, closets, elevator cores) |
| `registration.md` | Sheet registration: plan + elevation affine alignment into canonical metres |
| `review_classifier.md` | Local ML triage layer for the extraction review queue |
| `room_labels.md` | OCR room names/numbers and polygon association |
| `safe_xml.md` | Hardened XML parser for XXE prevention |
| `symbols.md` | Programmatic GD&T symbol rendering for synthetic training data |
| `synthetic_data.md` | Synthetic sheet/dataset generation |
| `synth/README.md` | synth/ module guide: sheets.py, mech.py, GT schemas, conftest fixture relationships |
| `ci.md` | CI gates, test count regression policy, and merge requirements |
| `validation.md` | The 28-check invariant battery and tolerance rationales |

## Quick Start

```bash
pip install -e ".[test]"        # (1) editable install with test extras
python -m pytest tests/ -q      # (2) smoke test — 699 tests, a few seconds
matchline run --seed 0          # (3) minimal pipeline: generate + link + validate + export
```

See the [full documentation](../README.md) or browse the tables above for module guides.

Start with `ROADMAP.md` (repo root) for where the project is going.
