# Export package and trust report (#750)

Every `run_pipeline` run that reaches the BEM export also writes `out/package/`:

| file | what |
|---|---|
| `<building>.xml`, `<building>.ifc` | the gbXML and IFC4 the run exported |
| `convention_report.json` | measured biases (volume, non-room area, envelope area budget, constructions) |
| `trust_report.json` | every number the report shows, each with the file and field it was read from |
| `trust_report.html` | one page rendered from that JSON; inline CSS, no scripts, no external assets |
| `trust_report.pdf` | only when LibreOffice (`soffice`) is installed; otherwise `manifest.json` says why |
| `DISCLAIMER.txt` | the professional-use disclaimer |
| `manifest.json` | SHA-256 of every file in the package, and whether the PDF was produced |

## What the report shows

- **Inputs**: the drawing set, sheet image, detections, schedule CSV, AEC-Bench path and detector config when given, each with its SHA-256 and size (a path that no longer exists is marked missing); the synthetic seed, climate zone, building category and config values.
- **Extracted**: levels, spaces, floor area, exterior walls, openings, constructions.
- **Convention biases**: the numeric fields of `convention_report.json`; a bias that could not be measured shows its reason, never zero.
- **Validation**: counts by severity and every check that warned or failed, with its message.
- **Open review items**: the count and the 15 lowest-confidence items.
- **Defaults used**: every provenance in the model whose method means the value was not drawn (today `construction_default`, the 90.1 Table 5.5 fills), with where it sits in the model and the table it came from.
- **Review decisions**: the editable decisions file belongs to the HTML review report (#749) and is listed as not yet available.

## Disclaimer

> matchline produces building energy modeling inputs for review by a qualified professional. It is not an engineering stamp, a code compliance determination or a substitute for professional judgement. Check every value against the drawings before using it.
