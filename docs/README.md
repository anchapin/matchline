# docs/

Design notes, module guides, and experiment records for Matchline.
Each page documents what works, what doesn't, and why — limitations are
disclosed, not hidden.

| Doc | What it covers |
|---|---|
| `belief_derivation.md` | Derivation of the WiSARD belief/normalization formulas |
| `bem_export.md` | gbXML 6.01 + IFC4 export paths and validation |
| `building_model.md` | Canonical `BuildingModel`: spaces, provenance, review queue |
| `ecosystem_audit.md` | Detector-strategy audit: YOLO + tiling + legend learning rationale |
| `elevation_windows.md` | Exact window placement from elevations + daylight zones |
| `facade_takeoff.md` | Facade wall/glazing/door fractions (CMP Facade) + priors |
| `geometry_simplify.md` | Area-budgeted BEM surface reduction |
| `hvac_trace.md` | Duct tracing → terminal units → zone graphs |
| `ifc_import.md` | Tier-0 IFC import: what recovers without space boundaries |
| `jesse.md` | WiSARD classifier + Zhang-Suen skeleton invariants + thick-stroke preprocessing |
| `lighting.md` | Fixture takeoffs, building watts, per-space LPD |
| `link.md` | Cross-sheet linker: Spaces, fixtures, zones, elevations, dedup |
| `measurement_layer.md` | Measurement/uncertainty conventions across the pipeline |
| `registration.md` | Sheet registration: plan + elevation affine alignment into canonical metres |
| `review_classifier.md` | Local ML triage layer for the extraction review queue |
| `room_labels.md` | OCR room names/numbers and polygon association |
| `synthetic_data.md` | Synthetic sheet/dataset generation |
| `validation.md` | The 26-check invariant battery and tolerance rationales |

Start with `ROADMAP.md` (repo root) for where the project is going.
