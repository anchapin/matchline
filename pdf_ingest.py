"""Drawing-set ingestion: PDF pages -> sheet records (#737).

Real projects arrive as multi-page PDF sets, usually vector PDFs exported from
Revit or AutoCAD. Every page becomes a :class:`Sheet` that the rest of the
pipeline can use without touching the PDF again:

* a greyscale raster at a recorded DPI, so ``m_per_px`` follows from the drawing
  scale once that is known (``px_per_pt = dpi / 72``);
* the vector primitives on the page (lines, polylines, closed shapes, curves);
* the text layer with positions (vector PDFs carry real text, no OCR needed);
* provenance: file hash, page number, page size, rotation.

Coordinates. Everything is in *sheet points*: PDF points (1/72 in), origin at
the top-left of the page as displayed, y pointing down, with the page's
``/Rotate`` already applied. That is the raster's frame divided by
``px_per_pt``, so a primitive at ``(x, y)`` pt sits at pixel
``(x * px_per_pt, y * px_per_pt)``.

Classification. A page whose area is mostly covered by images and that has
almost no vector or visible text content is ``raster_only`` (a scan). It is
reported loudly in ``warnings``: only a raster path can read it. ``mixed`` is
vector markup over a scan, ``empty`` has nothing on it.

The PDF is untrusted input: file size, page count, page size, object count and
raster size are all bounded (``limits.py``), and a corrupt or encrypted file is
refused with a clear message. pdfium (via pypdfium2, Apache-2.0/BSD-3) does the
parsing; PyMuPDF was not used because it is AGPL.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import limits

SCHEMA = "matchline.sheet/1"
DEFAULT_DPI = 150

# A page counts as a scan when images cover at least this much of it and it has
# no more than a handful of vector paths and visible text characters.
SCAN_IMAGE_FRAC = 0.5
SCAN_MAX_PATHS = 50
SCAN_MAX_CHARS = 20

Point = Tuple[float, float]
Matrix = Tuple[float, float, float, float, float, float]

# pdfium object and segment type codes (fpdf_edit.h)
_OBJ_TEXT, _OBJ_PATH, _OBJ_IMAGE, _OBJ_SHADING, _OBJ_FORM = 1, 2, 3, 4, 5
_SEG_LINETO, _SEG_BEZIERTO, _SEG_MOVETO = 0, 1, 2
_TEXT_INVISIBLE = 3


class IngestError(ValueError):
    """The PDF was refused (not a PDF, too large, corrupt, encrypted)."""


@dataclass
class Primitive:
    """One vector path. ``segments`` keeps the path exactly: ``("M", [p])``,
    ``("L", [p])``, ``("C", [c1, c2, p])`` and ``("Z", [])`` for a close."""

    kind: str  # line | polyline | polygon | path (has curves)
    segments: List[Tuple[str, List[Point]]]
    bbox: Tuple[float, float, float, float]
    stroke_width: float
    stroked: bool
    filled: bool
    dashed: bool


@dataclass
class TextSpan:
    text: str
    bbox: Tuple[float, float, float, float]


@dataclass
class ImageRef:
    bbox: Tuple[float, float, float, float]
    pixel_size: Tuple[int, int]


@dataclass
class Sheet:
    page_number: int  # 1-based
    width_pt: float  # as displayed (rotation applied)
    height_pt: float
    rotation: int
    kind: str  # vector | raster_only | mixed | empty
    dpi: Optional[float]
    px_per_pt: Optional[float]
    raster_file: Optional[str]
    raster_size: Optional[Tuple[int, int]]
    primitives: List[Primitive] = field(default_factory=list)
    text: List[TextSpan] = field(default_factory=list)
    images: List[ImageRef] = field(default_factory=list)
    stats: Dict[str, float] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    provenance: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["schema"] = SCHEMA
        return d


@dataclass
class IngestResult:
    source: str
    sha256: str
    n_pages: int
    sheets: List[Sheet]
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "schema": "matchline.ingest/1",
            "source": self.source,
            "sha256": self.sha256,
            "n_pages": self.n_pages,
            "warnings": list(self.warnings),
            "sheets": [
                {
                    "page_number": s.page_number,
                    "kind": s.kind,
                    "width_pt": s.width_pt,
                    "height_pt": s.height_pt,
                    "rotation": s.rotation,
                    "dpi": s.dpi,
                    "raster_file": s.raster_file,
                    "n_primitives": len(s.primitives),
                    "n_text": len(s.text),
                    "n_images": len(s.images),
                    "warnings": list(s.warnings),
                }
                for s in self.sheets
            ],
        }


# --------------------------------------------------------------------------- geometry


def _mul(m: Matrix, n: Matrix) -> Matrix:
    """Compose: apply ``m`` first, then ``n`` (PDF row-vector convention)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _apply(m: Matrix, x: float, y: float) -> Point:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def page_to_sheet_matrix(x0: float, y0: float, w: float, h: float, rotation: int) -> Matrix:
    """Unrotated PDF user space (y up, box origin ``x0, y0``, size ``w x h``) ->
    sheet points (top-left origin, y down, ``/Rotate`` applied clockwise)."""
    flip: Matrix = (1.0, 0.0, 0.0, -1.0, -x0, y0 + h)  # u = x - x0, v = y0 + h - y
    rot = rotation % 360
    if rot == 0:
        r: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    elif rot == 90:  # (u, v) -> (h - v, u)
        r = (0.0, 1.0, -1.0, 0.0, h, 0.0)
    elif rot == 180:  # (u, v) -> (w - u, h - v)
        r = (-1.0, 0.0, 0.0, -1.0, w, h)
    elif rot == 270:  # (u, v) -> (v, w - u)
        r = (0.0, -1.0, 1.0, 0.0, 0.0, w)
    else:
        raise IngestError(f"page /Rotate {rotation} is not a multiple of 90")
    return _mul(flip, r)


def _bbox_of(points: List[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _xform_box(m: Matrix, box) -> Tuple[float, float, float, float]:
    l, b, r, t = box
    return _bbox_of([_apply(m, l, b), _apply(m, r, b), _apply(m, l, t), _apply(m, r, t)])


def _r(v: float) -> float:
    return round(float(v), 3)


# --------------------------------------------------------------------------- reading


def _check_file(path: Path) -> str:
    if not path.is_file():
        raise IngestError(f"'{path}' is not a file or does not exist")
    try:
        limits.check_file_size(str(path), limits.MAX_PDF_SIZE_MB)
    except ValueError as e:
        raise IngestError(str(e)) from None
    with path.open("rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" not in head:
        raise IngestError(f"'{path}' is not a PDF (no %PDF- header in the first 1 KB)")
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _open(path: Path):
    try:
        import pypdfium2 as pdfium
    except ImportError as e:  # pragma: no cover - core dependency
        raise ImportError("PDF ingestion needs pypdfium2: pip install pypdfium2") from e
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        msg = str(e)
        if "password" in msg.lower():
            raise IngestError(f"'{path}' is encrypted; decrypt it first ({msg})") from None
        raise IngestError(f"'{path}' could not be read as a PDF ({msg})") from None


class _Walker:
    """Collect primitives, text-render stats and images from a page's object
    tree, descending into form XObjects with their matrices composed."""

    def __init__(self, to_sheet: Matrix, max_objects: int):
        import pypdfium2.raw as raw

        self.raw = raw
        self.to_sheet = to_sheet
        self.max_objects = max_objects
        self.n_objects = 0
        self.truncated = False
        self.primitives: List[Primitive] = []
        self.images: List[ImageRef] = []
        self.visible_text_objs = 0
        self.invisible_text_objs = 0

    def walk_page(self, page) -> None:
        raw = self.raw
        n = raw.FPDFPage_CountObjects(page.raw)
        for i in range(n):
            if self.truncated:
                return
            self._obj(raw.FPDFPage_GetObject(page.raw, i), self.to_sheet)

    def _matrix(self, obj) -> Matrix:
        m = self.raw.FS_MATRIX()
        if not self.raw.FPDFPageObj_GetMatrix(obj, m):
            return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        return (m.a, m.b, m.c, m.d, m.e, m.f)

    def _obj(self, obj, outer: Matrix) -> None:
        raw = self.raw
        self.n_objects += 1
        if self.n_objects > self.max_objects:
            self.truncated = True
            return
        t = raw.FPDFPageObj_GetType(obj)
        if t == _OBJ_FORM:
            m = _mul(self._matrix(obj), outer)
            for j in range(raw.FPDFFormObj_CountObjects(obj)):
                if self.truncated:
                    return
                self._obj(raw.FPDFFormObj_GetObject(obj, j), m)
        elif t == _OBJ_PATH:
            self._path(obj, _mul(self._matrix(obj), outer))
        elif t == _OBJ_TEXT:
            if raw.FPDFTextObj_GetTextRenderMode(obj) == _TEXT_INVISIBLE:
                self.invisible_text_objs += 1
            else:
                self.visible_text_objs += 1
        elif t == _OBJ_IMAGE:
            self._image(obj, outer)

    def _bounds(self, obj, outer: Matrix):
        """Object bounds are reported in the coordinates of its container
        (page or form), so apply the container's matrix only."""
        l, b, r, t = (ctypes.c_float() for _ in range(4))
        if not self.raw.FPDFPageObj_GetBounds(obj, l, b, r, t):
            return None
        return _xform_box(outer, (l.value, b.value, r.value, t.value))

    def _image(self, obj, outer: Matrix) -> None:
        box = self._bounds(obj, outer)
        if box is None:
            return
        w, h = ctypes.c_uint(), ctypes.c_uint()
        self.raw.FPDFImageObj_GetImagePixelSize(obj, w, h)
        self.images.append(ImageRef(tuple(_r(v) for v in box), (int(w.value), int(h.value))))

    def _path(self, obj, m: Matrix) -> None:
        raw = self.raw
        n = raw.FPDFPath_CountSegments(obj)
        if n <= 0:
            return
        segs: List[Tuple[str, List[Point]]] = []
        pts: List[Point] = []
        pending: List[Point] = []  # bezier points collect in threes
        has_curve = False
        n_close = 0
        n_move = 0
        x, y = ctypes.c_float(), ctypes.c_float()
        for i in range(n):
            s = raw.FPDFPath_GetPathSegment(obj, i)
            raw.FPDFPathSegment_GetPoint(s, x, y)
            p = _apply(m, x.value, y.value)
            p = (_r(p[0]), _r(p[1]))
            st = raw.FPDFPathSegment_GetType(s)
            pts.append(p)
            if st == _SEG_MOVETO:
                segs.append(("M", [p]))
                n_move += 1
            elif st == _SEG_LINETO:
                segs.append(("L", [p]))
            elif st == _SEG_BEZIERTO:
                has_curve = True
                pending.append(p)
                if len(pending) == 3:
                    segs.append(("C", pending))
                    pending = []
            if raw.FPDFPathSegment_GetClose(s):
                segs.append(("Z", []))
                n_close += 1
        fill = ctypes.c_int()
        stroke = ctypes.c_int()
        raw.FPDFPath_GetDrawMode(obj, fill, stroke)
        w = ctypes.c_float()
        raw.FPDFPageObj_GetStrokeWidth(obj, w)
        scale = math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))
        dashed = raw.FPDFPageObj_GetDashCount(obj) > 0
        n_lines = sum(1 for k, _ in segs if k == "L")
        if has_curve:
            kind = "path"
        elif n_move == 1 and n_lines == 1 and n_close == 0:
            kind = "line"
        elif n_move == 1 and n_close == 1:
            kind = "polygon"
        else:
            kind = "polyline"
        self.primitives.append(
            Primitive(
                kind=kind,
                segments=segs,
                bbox=_bbox_of(pts),
                stroke_width=_r(w.value * scale),
                stroked=bool(stroke.value),
                filled=fill.value != 0,
                dashed=dashed,
            )
        )


def _text_spans(page, to_sheet: Matrix) -> List[TextSpan]:
    tp = page.get_textpage()
    try:
        spans = []
        for i in range(tp.count_rects()):
            box = tp.get_rect(i)
            s = tp.get_text_bounded(*box).strip()
            if s:
                spans.append(TextSpan(s, tuple(_r(v) for v in _xform_box(to_sheet, box))))
        return spans
    finally:
        tp.close()


def _image_area_frac(images: List[ImageRef], w: float, h: float) -> float:
    if not images or w <= 0 or h <= 0:
        return 0.0
    # coarse union on a 100x100 grid: exact enough to tell "a scan" from "a logo"
    import numpy as np

    g = np.zeros((100, 100), dtype=bool)
    for im in images:
        x0, y0, x1, y1 = im.bbox
        c0, c1 = (int(max(0, min(100, v / w * 100))) for v in (x0, x1))
        r0, r1 = (int(max(0, min(100, v / h * 100))) for v in (y0, y1))
        g[r0 : max(r1, r0 + 1), c0 : max(c1, c0 + 1)] = True
    return float(g.mean())


def _classify(n_paths: int, n_chars: int, img_frac: float) -> str:
    if n_paths == 0 and n_chars == 0 and img_frac == 0.0:
        return "empty"
    if img_frac >= SCAN_IMAGE_FRAC:
        if n_paths <= SCAN_MAX_PATHS and n_chars <= SCAN_MAX_CHARS:
            return "raster_only"
        return "mixed"
    return "vector"


def _dpi_for(w_pt: float, h_pt: float, dpi: float) -> float:
    mpx = (w_pt * dpi / 72.0) * (h_pt * dpi / 72.0) / 1e6
    if mpx <= limits.MAX_PDF_RASTER_MPX:
        return dpi
    return math.floor(dpi * math.sqrt(limits.MAX_PDF_RASTER_MPX / mpx))


def ingest_pdf(
    path,
    out_dir=None,
    dpi: float = DEFAULT_DPI,
    rasterize: bool = True,
) -> IngestResult:
    """Read a PDF drawing set into :class:`Sheet` records.

    With ``out_dir``, writes ``sheet_NNN.png`` (greyscale, when ``rasterize``),
    ``sheet_NNN.json`` (the full sheet record) and ``ingest.json`` (index).
    """
    path = Path(path)
    sha = _check_file(path)
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi}")
    doc = _open(path)
    try:
        n_pages = len(doc)
        if n_pages > limits.MAX_PDF_PAGES:
            raise IngestError(
                f"'{path}' has {n_pages} pages, over the {limits.MAX_PDF_PAGES} limit. "
                "Set MATCHLINE_MAX_PDF_PAGES to increase it."
            )
        out = Path(out_dir) if out_dir is not None else None
        if out is not None:
            out.mkdir(parents=True, exist_ok=True)
        sheets: List[Sheet] = []
        warnings: List[str] = []
        for i in range(n_pages):
            page = doc[i]
            try:
                sheets.append(_ingest_page(page, i + 1, path, sha, out, dpi, rasterize))
            finally:
                page.close()
            for w in sheets[-1].warnings:
                warnings.append(f"page {i + 1}: {w}")
    finally:
        doc.close()
    result = IngestResult(str(path), sha, n_pages, sheets, warnings)
    if out is not None:
        for s in sheets:
            (out / f"sheet_{s.page_number:03d}.json").write_text(json.dumps(s.to_dict()))
        (out / "ingest.json").write_text(json.dumps(result.summary(), indent=2))
    return result


def _ingest_page(page, number: int, path: Path, sha: str, out, dpi, rasterize) -> Sheet:
    import pypdfium2

    rotation = int(page.get_rotation()) % 360
    x0, y0, x1, y1 = page.get_cropbox()
    w, h = x1 - x0, y1 - y0
    if max(w, h) > limits.MAX_PDF_PAGE_SIDE_PT:
        raise IngestError(
            f"page {number} is {w:.0f} x {h:.0f} pt, over the "
            f"{limits.MAX_PDF_PAGE_SIDE_PT:.0f} pt limit. Set MATCHLINE_MAX_PDF_PAGE_SIDE_PT "
            "to increase it."
        )
    disp_w, disp_h = (h, w) if rotation in (90, 270) else (w, h)
    to_sheet = page_to_sheet_matrix(x0, y0, w, h, rotation)
    warnings: List[str] = []

    walker = _Walker(to_sheet, limits.MAX_PDF_PAGE_OBJECTS)
    walker.walk_page(page)
    primitives = walker.primitives
    if walker.truncated:
        primitives = []
        warnings.append(
            f"more than {limits.MAX_PDF_PAGE_OBJECTS} objects; vectors skipped. "
            "Set MATCHLINE_MAX_PDF_PAGE_OBJECTS to read them."
        )
    text = _text_spans(page, to_sheet)
    n_chars = sum(len(t.text) for t in text) if walker.visible_text_objs else 0
    img_frac = _image_area_frac(walker.images, disp_w, disp_h)
    kind = _classify(len(primitives), n_chars, img_frac)
    if kind == "raster_only":
        note = "no vector layer (scanned sheet?); only the raster path can read it"
        if walker.invisible_text_objs:
            note += "; it carries an invisible OCR text layer"
        warnings.append(note)
    elif kind == "mixed":
        warnings.append("vector content drawn over a full-page image (marked-up scan?)")
    elif kind == "empty":
        warnings.append("page is empty")

    used_dpi = raster_file = raster_size = px_per_pt = None
    if rasterize:
        used_dpi = _dpi_for(disp_w, disp_h, dpi)
        if used_dpi != dpi:
            warnings.append(
                f"rendered at {used_dpi:g} dpi instead of {dpi:g} to stay under "
                f"{limits.MAX_PDF_RASTER_MPX:g} Mpx"
            )
        px_per_pt = used_dpi / 72.0
        img = page.render(scale=px_per_pt, grayscale=True).to_pil().convert("L")
        raster_size = img.size
        if out is not None:
            raster_file = f"sheet_{number:03d}.png"
            img.save(out / raster_file)

    return Sheet(
        page_number=number,
        width_pt=_r(disp_w),
        height_pt=_r(disp_h),
        rotation=rotation,
        kind=kind,
        dpi=used_dpi,
        px_per_pt=px_per_pt,
        raster_file=raster_file,
        raster_size=raster_size,
        primitives=primitives,
        text=text,
        images=walker.images,
        stats={
            "n_objects": walker.n_objects,
            "n_paths": len(primitives),
            "n_text_chars": n_chars,
            "visible_text_objects": walker.visible_text_objs,
            "invisible_text_objects": walker.invisible_text_objs,
            "image_area_frac": round(img_frac, 3),
        },
        warnings=warnings,
        provenance={
            "source_file": str(path),
            "sha256": sha,
            "page": number,
            "method": "pdfium vector + text layer",
            "pdfium": getattr(pypdfium2, "PDFIUM_INFO", None) and str(pypdfium2.PDFIUM_INFO),
            "pypdfium2": str(getattr(pypdfium2, "PYPDFIUM_INFO", "")),
        },
    )


def load_sheet(path) -> dict:
    """Read a ``sheet_NNN.json`` written by :func:`ingest_pdf`."""
    d = json.loads(Path(path).read_text())
    if d.get("schema") != SCHEMA:
        raise ValueError(f"'{path}' is not a {SCHEMA} sheet record")
    return d
