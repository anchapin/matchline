"""PDF drawing-set ingestion (#737). Hermetic: every PDF is written by
``tests/pdf_fixtures.py`` in the test's tmp dir."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from pdf_fixtures import PageSpec, curve, line, rect, text, write_pdf  # noqa: E402

import limits  # noqa: E402
import pdf_ingest as P  # noqa: E402


def _ingest(tmp_path, pages, **kw):
    pdf = write_pdf(tmp_path / "set.pdf", pages)
    return P.ingest_pdf(pdf, out_dir=tmp_path / "out", **kw)


def _ink(tmp_path, sheet):
    a = np.asarray(Image.open(tmp_path / "out" / sheet.raster_file))
    ys, xs = np.where(a < 128)
    return xs.min(), xs.max(), ys.min(), ys.max()


def test_line_rect_curve_in_sheet_points(tmp_path):
    content = (
        line(100, 100, 300, 100, 2) + rect(50, 50, 40, 30) + curve(10, 10, 20, 40, 40, 40, 50, 10)
    )
    s = _ingest(tmp_path, [PageSpec(width=600, height=400, content=content)]).sheets[0]
    kinds = [p.kind for p in s.primitives]
    assert kinds == ["line", "polygon", "path"]
    ln, rc, cv = s.primitives
    # y flips: PDF y=100 on a 400 pt page is 300 pt from the top
    assert ln.bbox == (100.0, 300.0, 300.0, 300.0)
    assert ln.stroke_width == 2.0 and ln.stroked and not ln.filled
    assert rc.bbox == (50.0, 320.0, 90.0, 350.0)
    assert [k for k, _ in rc.segments][-1] == "Z"
    assert [k for k, _ in cv.segments] == ["M", "C"]
    assert cv.segments[1][1][-1] == (50.0, 390.0)


def test_filled_and_dashed_flags(tmp_path):
    content = rect(10, 10, 20, 20, fill=True) + "[6 3 1 3] 0 d " + line(0, 200, 500, 200)
    s = _ingest(tmp_path, [PageSpec(content=content)]).sheets[0]
    fill, dash = s.primitives
    assert fill.filled and not fill.stroked and not fill.dashed
    assert dash.dashed and dash.kind == "line"


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_rotated_pages_match_the_raster(tmp_path, rot):
    """Vector coordinates and the rendered raster share one frame."""
    content = line(100, 50, 300, 50, 6)
    s = _ingest(tmp_path, [PageSpec(width=600, height=400, content=content, rotate=rot)], dpi=72)
    s = s.sheets[0]
    assert s.rotation == rot
    assert (s.width_pt, s.height_pt) == ((400.0, 600.0) if rot in (90, 270) else (600.0, 400.0))
    assert s.raster_size == (int(s.width_pt), int(s.height_pt))
    x0, y0, x1, y1 = s.primitives[0].bbox
    ix0, ix1, iy0, iy1 = _ink(tmp_path, s)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert ix0 - 1 <= cx <= ix1 + 1 and iy0 - 1 <= cy <= iy1 + 1
    assert abs((x1 - x0) + (y1 - y0) - 200) < 1e-6  # the 200 pt run survives rotation


def test_transform_and_form_xobject_are_applied(tmp_path):
    content = "q 2 0 0 2 100 50 cm " + line(0, 0, 50, 0) + "Q\n"
    page = PageSpec(
        width=600, height=400, content=content, form=(rect(10, 10, 50, 20), (1, 0, 0, 1, 400, 200))
    )
    s = _ingest(tmp_path, [page]).sheets[0]
    ln, box = s.primitives
    assert ln.bbox == (100.0, 350.0, 200.0, 350.0)
    assert ln.stroke_width == 2.0  # 1 pt scaled by the 2x cm
    assert box.bbox == (410.0, 170.0, 460.0, 190.0)


def test_text_spans_with_positions(tmp_path):
    content = text(200, 300, "A-101 FIRST FLOOR PLAN") + text(50, 40, 'SCALE: 1/8" = 1\'-0"', 8)
    s = _ingest(tmp_path, [PageSpec(width=600, height=400, content=content)]).sheets[0]
    # Helvetica's StandardEncoding maps 0x27 to quoteright, so pdfium reads a typed
    # apostrophe as U+2019; the text is kept verbatim and scale parsing normalises it
    by = {t.text.replace("\u2019", "'"): t.bbox for t in s.text}
    assert set(by) == {"A-101 FIRST FLOOR PLAN", 'SCALE: 1/8" = 1\'-0"'}
    x0, y0, x1, y1 = by["A-101 FIRST FLOOR PLAN"]
    assert 199 < x0 < 202 and 85 < y0 < y1 < 102 and x1 > x0 + 100


def _grey(w, h, v=200):
    return (w, h, bytes([v]) * (w * h))


def test_scanned_page_is_raster_only_and_loud(tmp_path):
    res = _ingest(tmp_path, [PageSpec(image=_grey(40, 30))])
    s = res.sheets[0]
    assert s.kind == "raster_only"
    assert s.stats["image_area_frac"] == 1.0
    assert any("no vector layer" in w for w in s.warnings)
    assert any(w.startswith("page 1:") and "no vector layer" in w for w in res.warnings)


def test_scan_with_invisible_ocr_text_is_still_raster_only(tmp_path):
    ocr = "BT 3 Tr /F1 10 Tf 100 100 Td (OFFICE 101) Tj ET\n"
    s = _ingest(tmp_path, [PageSpec(image=_grey(40, 30), content=ocr)]).sheets[0]
    assert s.kind == "raster_only"
    assert s.stats["invisible_text_objects"] == 1
    assert any("invisible OCR text layer" in w for w in s.warnings)


def test_vector_markup_over_scan_is_mixed(tmp_path):
    content = "".join(line(10, 10 + i, 500, 10 + i) for i in range(60))
    s = _ingest(tmp_path, [PageSpec(image=_grey(40, 30), content=content)]).sheets[0]
    assert s.kind == "mixed"


def test_empty_page(tmp_path):
    s = _ingest(tmp_path, [PageSpec()]).sheets[0]
    assert s.kind == "empty" and s.primitives == [] and s.text == []


def test_multi_page_set_writes_index_sheets_and_rasters(tmp_path):
    pages = [
        PageSpec(width=612, height=792, content=line(0, 0, 100, 100) + text(10, 10, "A-101")),
        PageSpec(width=2592, height=1728, content=rect(100, 100, 500, 300) + text(10, 10, "M-101")),
    ]
    res = _ingest(tmp_path, pages, dpi=100)
    out = tmp_path / "out"
    idx = json.loads((out / "ingest.json").read_text())
    assert idx["n_pages"] == 2 and len(idx["sha256"]) == 64
    assert [s["page_number"] for s in idx["sheets"]] == [1, 2]
    s2 = res.sheets[1]
    assert s2.raster_size == (round(2592 * 100 / 72), round(1728 * 100 / 72))
    assert s2.px_per_pt == pytest.approx(100 / 72)
    rec = P.load_sheet(out / "sheet_002.json")
    assert rec["provenance"]["page"] == 2 and rec["provenance"]["sha256"] == res.sha256
    assert rec["text"][0]["text"] == "M-101"
    assert (out / "sheet_002.png").is_file()


def test_no_raster_skips_rendering(tmp_path):
    s = _ingest(tmp_path, [PageSpec(content=line(0, 0, 10, 10))], rasterize=False).sheets[0]
    assert s.raster_file is None and s.dpi is None and s.primitives


def test_raster_size_cap_lowers_dpi_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_RASTER_MPX", 1.0)
    s = _ingest(tmp_path, [PageSpec(width=2000, height=2000, content=line(0, 0, 9, 9))], dpi=150)
    s = s.sheets[0]
    assert s.dpi < 150 and s.raster_size[0] * s.raster_size[1] <= 1_000_000
    assert any("instead of 150" in w for w in s.warnings)


def test_object_cap_skips_vectors_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_PAGE_OBJECTS", 5)
    content = "".join(line(10, i, 100, i) for i in range(10))
    s = _ingest(tmp_path, [PageSpec(content=content)], rasterize=False).sheets[0]
    assert s.primitives == []
    assert any("vectors skipped" in w for w in s.warnings)


def test_refuses_non_pdf(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"PK\x03\x04 not a pdf")
    with pytest.raises(P.IngestError, match="not a PDF"):
        P.ingest_pdf(p)


def test_refuses_corrupt_pdf(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\n garbage with no xref or objects")
    with pytest.raises(P.IngestError, match="could not be read"):
        P.ingest_pdf(p)


def test_refuses_oversized_file_and_too_many_pages(tmp_path, monkeypatch):
    pdf = write_pdf(tmp_path / "s.pdf", [PageSpec(), PageSpec(), PageSpec()])
    monkeypatch.setattr(limits, "MAX_PDF_PAGES", 2)
    with pytest.raises(P.IngestError, match="3 pages"):
        P.ingest_pdf(pdf)
    monkeypatch.setattr(limits, "MAX_PDF_SIZE_MB", 0)
    with pytest.raises(P.IngestError, match="limit"):
        P.ingest_pdf(pdf)


def test_refuses_oversized_page(tmp_path, monkeypatch):
    monkeypatch.setattr(limits, "MAX_PDF_PAGE_SIDE_PT", 1000)
    pdf = write_pdf(tmp_path / "s.pdf", [PageSpec(width=1200, height=800)])
    with pytest.raises(P.IngestError, match="1200 x 800 pt"):
        P.ingest_pdf(pdf)


def test_cli_ingest(tmp_path, capsys):
    from cli import main

    pdf = write_pdf(
        tmp_path / "s.pdf", [PageSpec(content=line(0, 0, 9, 9)), PageSpec(image=_grey(4, 4))]
    )
    main(["ingest", str(pdf), "--out", str(tmp_path / "o"), "--dpi", "50"])
    out = capsys.readouterr().out
    assert "pages: 2" in out and "raster_only: 1" in out and "vector: 1" in out
    assert "WARNING page 2:" in out
    assert (tmp_path / "o" / "ingest.json").is_file()
