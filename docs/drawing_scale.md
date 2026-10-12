# Drawing scale (`drawing_scale.py`)

Reads each ingested sheet's scale from the sheet itself (#739), so lengths and
areas on real drawings mean something. Input is a sheet from `pdf_ingest`
(#737); output is `m_per_pt` (real metres per sheet point) and, given the
raster DPI, `m_per_px`.

```bash
matchline ingest set.pdf --out sheets/
matchline scale sheets/        # writes sheets/scale.json
```

## Sources

| Source | Reads | Notes |
|---|---|---|
| Scale note | `1/8" = 1'-0"`, `3/32" = 1'-0"`, `1" = 20'-0"`, `1:100` | ratio = real length / paper length |
| Scale bar | a row of numeric labels from 0, linear in position, with a unit (`FEET`, `FT`, `'`, `M`) | labels without a unit are ignored |
| Dimension strings | `12'-6"`, `12'-6 1/2"`, `3600 mm`, ... centred on a dimension line | needs 3 or more agreeing (within 1%) to stand alone |

`NTS`, `N.T.S.` and `NOT TO SCALE` mark a sheet that gives no areas.
`SCALE: AS NOTED` defers to the per-view notes.

## Decision

1. Several different scale notes on one sheet: no sheet scale. `views` lists
   each note with its box; `scale_at(scale, x, y)` picks the note nearest below
   a point, since view titles sit under their views.
2. One note: it wins. A scale bar, and two or more agreeing dimensions,
   cross-check it; agreement raises confidence, a disagreement over 1% sets
   `needs_review` and says which source gave what.
3. No note: the scale bar, then dimensions.
4. Nothing: `m_per_pt` is `None` with the reason and `needs_review`. Never a
   guess.

Every candidate is kept in `evidence` (source, text, box, ratio), so a reviewer
can see why a scale was chosen.

## Text normalisation

Typographic quotes (pdfium reads a typed `'` in standard-encoded fonts as
U+2019), primes, unicode fractions and dashes are normalised before parsing.
Inches of 12 or more in a feet-inches string are rejected rather than carried.
