# PDF drawing-set ingestion (`pdf_ingest.py`)

Turns a PDF drawing set into one sheet record per page (#737). This is the
first stage of reading real drawings; sheet indexing (#738), drawing scale
(#739) and wall/room extraction (#740) build on its output.

```bash
matchline ingest set.pdf --out sheets/ --dpi 150
```

writes, per page, `sheet_NNN.png` (greyscale raster) and `sheet_NNN.json`
(schema `matchline.sheet/1`), plus `ingest.json` with a per-page summary.

## What a sheet record holds

| Field | Meaning |
|---|---|
| `kind` | `vector`, `raster_only` (a scan), `mixed` (vector markup over a scan) or `empty` |
| `width_pt`, `height_pt`, `rotation` | page size as displayed, with `/Rotate` applied |
| `dpi`, `px_per_pt`, `raster_file`, `raster_size` | the raster and its resolution (`px_per_pt = dpi / 72`) |
| `primitives` | vector paths: `kind` (line, polyline, polygon, path), exact `segments`, `bbox`, `stroke_width`, `stroked`, `filled`, `dashed` |
| `text` | text runs with their bounding boxes |
| `images` | image placements (bbox, pixel size) |
| `stats`, `warnings` | object counts, image coverage, and anything the page could not give |
| `provenance` | source file, SHA-256, page number, method, pdfium version |

## Coordinates

Everything is in sheet points: PDF points (1/72 in), origin top-left of the
page as displayed, y down, rotation applied. Multiply by `px_per_pt` to land on
the raster. Paths inside form XObjects (how most CAD exporters write a sheet)
are flattened with their transforms; stroke widths are scaled the same way.

Text is kept verbatim. Standard-encoded fonts read a typed apostrophe as
U+2019, so consumers that parse dimensions or scale notes normalise quotes.

## Scans

A page whose area is at least half covered by images, with at most 50 vector
paths and 20 visible text characters, is `raster_only`. It is never silently
treated like a vector page: the sheet and `ingest.json` carry a warning, and an
invisible OCR text layer is called out. Only a raster path can read it.

## Limits (untrusted input)

| Env var | Default | |
|---|---|---|
| `MATCHLINE_MAX_PDF_SIZE_MB` | 500 | refused above |
| `MATCHLINE_MAX_PDF_PAGES` | 500 | refused above |
| `MATCHLINE_MAX_PDF_PAGE_SIDE_PT` | 7200 (100 in) | refused above |
| `MATCHLINE_MAX_PDF_PAGE_OBJECTS` | 1,000,000 | that page's vectors skipped, with a warning |
| `MATCHLINE_MAX_PDF_RASTER_MPX` | 150 | DPI lowered to fit, with a warning |

Files without a `%PDF-` header, corrupt files and encrypted files are refused
with an `IngestError` naming the reason.

## Library

pdfium through pypdfium2 (Apache-2.0 / BSD-3). PyMuPDF was not used because it
is AGPL, which does not fit matchline's BSD-3 license.
