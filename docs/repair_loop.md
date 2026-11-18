# Repair Loop — `bem/repair_loop.py` (#1407)

The deterministic repair loop: a default-off step between the staged
BEM export and the publish-nothing export gate
([`bem_export.md`](bem_export.md), #1404) that repairs the few
geometric defect classes whose fix is unique, deterministic and
conservation-preserving. It is Phase 1 of the Solvaix-mined spike
(#1406); the class selection and every repair semantic were
hand-verified in Phase 0 before any of this was built.

## What it repairs

| Class | Defect | Repair |
|---|---|---|
| R1 | Shell wound inside out (`shell_volume` fails), or one face flipped (`shell_winding` fails) | Reverse the inside-out shell; for a flipped face, apply the *unique* single-loop reversal that restores winding consistency — if the fix is not unique, no repair |
| R2 | Duplicate envelope surfaces (`duplicate_surfaces` fails) | Drop the later duplicates in the staged gbXML, keep the first in document order, log the dropped ids |
| R4 | Opening placements on one wall overlap (a source-stated centre does not advance the sequential cursor in `_place_openings_on_wall`) | New detector (`placement_overlaps`) + shift only the *unstated* openings into the wall's free intervals, preserving widths and order |

Repairs operate on the staged gbXML (R1/R2) or at placement level
inside the writers (R4) — never on the model, never on the IFC4
export, and never on a source-stated fact: stated centres, stated
sills and schedule values are facts. A stated-vs-stated overlap has
nothing that may move and is not repaired; it is surfaced in the
placement notes instead of passing silently.

## How the loop runs

- **Off by default.** Enable per publish with
  `publish_exports(..., repair_loop=True)`, per model with
  `model.repair_loop = True`, or per demo export with
  `matchline bem-export --repair-loop`. With the loop off, behaviour
  is byte-for-byte what it was before #1407.
- **Staged-only inner revalidation.** After a repair, only the
  geometric checks (R1/R2) or only the placement-overlap check (R4)
  re-runs. One class per iteration, **max 2 iterations**. A repair
  that does not make its check pass, or that makes a previously
  passing check fail, is reverted byte-for-byte and the original
  failure proceeds exactly as it does without the loop.
- **The gate is unchanged and remains the final arbiter.**
  `publish_exports` still stages, runs the full gbXML XSD + semantic
  validation, the full IFC4 validation and all six geometric checks
  on the final files, and publishes only on a full pass, atomically,
  with the checksummed manifest. A repaired file that fails any part
  of the full gate is refused, exactly as an unrepaired one is. There
  is no waiver or force-publish flag and no second publish path.
- **Provenance.** Every applied repair is recorded in the export
  manifest (`validation.repairs`): `method: repair:<class>`, the
  failed check, before/after measurements, the iteration number and
  the affected ids/intervals. A loop-enabled run with zero repairs
  records the empty list. An export that published after repair is
  distinguishable from one that never failed, from its manifest alone.

## Limitations

- **R3 (degenerate faces) is detector-only, permanently.** Phase 0
  showed the candidate repairs breach shell closure (+8.33% volume)
  or delete real area (−1.45%, past the 1% envelope tolerance): the
  original geometry is unrecoverable, so the landed detector is the
  whole value.
- **R5 classes are untouched** — planarity, shell-closure gaps,
  conservation/area/volume failures, ASHRAE failures and
  simplify-budget breaches have no unique fix; they are
  geometry/physics judgment calls and still refuse the export.
- **The pipeline stage-6 export path** (`run_pipeline`, which writes
  gbXML directly and does not run the geometric checks — the #1406
  Phase 0 side finding) does not invoke this loop. The loop lives in
  the gated bem-export path only; wiring stage 6 to the gate is a
  separate issue.
- **R4 records from the multistorey and single-storey gbXML writers**
  are collected on the model (`model.repair_records`) during staging;
  the IFC4 writer applies the same repaired placements (so gbXML and
  IFC4 agree) but does not add records of its own.
- **Cost.** Phase 0b measured the prototype's inner iteration at
  17.3–18.0% of a full `publish_exports` run (bar: ≤25%); the built
  implementation is re-measured in the #1407 PR. The full gate still
  runs once per publish, as before — the loop adds its iterations on
  top, only when a repairable defect is present.
