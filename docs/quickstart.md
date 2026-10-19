# Quickstart: one building, start to finish

This walkthrough takes one building from input to exported package:
import, simplification, validation, review, and the gbXML + IFC4 exports.
Every command below was run end to end on the sample project; the file
names you see are the real outputs. Install first (`pip install matchline`,
Python 3.10+; extras `[ocr] [mech] [ui]` in pyproject.toml).

## The 60-second path (no inputs)

```bash
matchline demo --no-open
```

That runs the unified pipeline on a fixed sample project and leaves
everything in `matchline-demo/`:

- `package/trust_report.html`: the one-page sign-off report
- `package/bldg_007.xml` (gbXML) and `package/bldg_007.ifc` (IFC4)
- `review/review.html`: the low-confidence calls worth a human look
- `stage_01_building.json` through `stage_06_bem/`: each pipeline stage's
  output, so you can inspect exactly what the model became
- `package.zip`: the shareable export package with its manifest

Drop `--no-open` to have it open the trust report in your browser.

## From an IFC model

```bash
matchline run --ifc building.ifc --out-dir out/
```

The `--ifc` stage imports the IFC (geometry, spaces, openings, and any
U-values/SHGC/VT on windows and doors), then runs the same
simplify/validate/export stages as the demo. Same output layout:
`out/package/` holds the exports and the trust report, `out/review/` the
review report. The import is Tier-0: ifcopenshell reads the file and the
pipeline degrades gracefully where the IFC lacks space boundaries; see
`docs/ifc_import.md` for what recovers and what errors.

## From a drawing set (PDF)

```bash
matchline run --set drawing_set.pdf --climate-zone 4A --out-dir out/
```

One command: ingest, sheet index, scale detection, walls/rooms from the
vector floor plans, then the model, validation and exports. Pass your
ASHRAE climate zone (`--climate-zone 4A`) so the construction library can
fill unset constructions; unset ones stay unset and are flagged. To walk
the drawing stages yourself:

```bash
matchline ingest drawing_set.pdf --out sheets/   # per-page raster, vectors, text
matchline scale sheets/                          # drawing scale per sheet
matchline index sheets/                          # discipline, type, level; paired plans
matchline walls sheets/                          # centrelines, thickness, rooms
matchline schedules sheets/                      # door/window/lighting/mech tables
```

## Review the low-confidence calls

The run already writes `out/review/review.html`. To work the queue from
the terminal:

```bash
matchline review out/review/model.json --list            # pending items, worst first
matchline review out/review/model.json --confirm rq-001  # accept the automatic call
matchline review out/review/model.json --reject rq-002   # reject it
matchline review out/review/model.json --show-all        # also show decided items
```

Or review in the browser page and replay its exported decisions file:

```bash
matchline review out/review/model.json --apply out/review/decisions.json --out reviewed.json
```

Validation re-runs after every confirm/reject: errors block export,
warnings need acknowledgment. The full invariant catalog and the
tolerance rationale are in `docs/validation.md`.

## Export round-trip

`out/package/` already contains the gbXML and IFC4 exports with a
`manifest.json` and a `convention_report.json`. To re-export a reviewed
model on its own:

```bash
matchline ifc-export reviewed.json out.ifc
```

## Where to go next

- `docs/cli.md`: the full command set
- `docs/api.md` and `docs/api_reference.md`: the public API, its breaking
  change rules, and every public symbol with its signature
- `docs/migrations.md`: upgrading from 0.x
- `docs/pipeline.md`: the unified pipeline and its stages
