# docs/design-docs/

Architectural decisions, core beliefs, and indexed design references.

## Index

| File | Status | Description |
|---|---|---|
| `core-beliefs.md` | active | Agent-first operating principles for this project |
| `open-issues.md` | active | Genuinely open technical questions only |

## Completed design decisions (CFG-*)

Located in `docs/plans/designs/`:

| ID | Title |
|---|---|
| [CFG-01](../plans/designs/cfg-01-cross-sheet-window-deduplication.md) | Cross-sheet window deduplication |
| [CFG-02](../plans/designs/cfg-02-thick-stroke-skeleton.md) | Thick-stroke skeleton pre-processing |
| [CFG-03](../plans/designs/cfg-03-complex-gdt-invariant-rows.md) | Complex GD&T invariant rows |
| [CFG-04](../plans/designs/cfg-04-provenance-tracking.md) | Provenance Tracking System |

## Design decisions embedded in module docs

Many decisions are documented inline in the module docs rather than here:

- `docs/building_model.md` — canonical model, provenance fields, review queue
- `docs/validation.md` — conservation law tolerances and rationale
- `docs/ifc_import.md` — IFC Tier 0 vs Tier 1 boundaries
- `docs/measurement_layer.md` — unit and uncertainty conventions
- `docs/belief_derivation.md` — WiSARD belief normalization formula reconstruction

## XML Security (XXE Protection)

All XML parsing MUST use . Never import , , or bare  directly. The enforcement test is .
