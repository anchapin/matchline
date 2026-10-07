"""Tiny hand-written PDF builder for hermetic ingestion tests (#737).

Writes only what the tests need: pages with a vector content stream, the
standard Helvetica font for text, an optional page /Rotate, and an optional
full-page greyscale image (a stand-in for a scanned sheet). No third-party
writer, so the fixtures never depend on a library the pipeline doesn't use.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class PageSpec:
    width: float = 612.0
    height: float = 792.0
    content: str = ""  # raw PDF content-stream operators (PDF space, y up)
    rotate: int = 0
    image: Optional[Tuple[int, int, bytes]] = None  # (w, h, 8-bit grey) drawn full page
    # (content, (a, b, c, d, e, f)): a form XObject drawn once via "/Fm1 Do"; CAD
    # exporters put most of a sheet inside forms, often nested under a transform
    form: Optional[Tuple[str, Tuple[float, ...]]] = None


def line(x0, y0, x1, y1, w=1.0) -> str:
    return f"{w} w {x0} {y0} m {x1} {y1} l S\n"


def rect(x, y, w, h, lw=1.0, fill=False) -> str:
    return f"{lw} w {x} {y} {w} {h} re {'f' if fill else 'S'}\n"


def curve(x0, y0, x1, y1, x2, y2, x3, y3) -> str:
    return f"{x0} {y0} m {x1} {y1} {x2} {y2} {x3} {y3} c S\n"


def text(x, y, s, size=12) -> str:
    s = s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET\n"


def write_pdf(path: Path, pages: List[PageSpec], encrypt_marker: bool = False) -> Path:
    objs: List[bytes] = []

    def add(b: bytes) -> int:
        objs.append(b)
        return len(objs)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add(b"")  # placeholder, filled below
    kids = []
    for p in pages:
        content = p.content
        xobj = b""
        if p.image is not None:
            iw, ih, data = p.image
            comp = zlib.compress(data)
            img = add(
                f"<< /Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
                f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
                f"/Length {len(comp)} >>\nstream\n".encode()
                + comp
                + b"\nendstream"
            )
            xobj = f"/Im1 {img} 0 R ".encode()
            content = f"q {p.width} 0 0 {p.height} 0 0 cm /Im1 Do Q\n" + content
        if p.form is not None:
            fc, m = p.form
            fb = fc.encode("latin-1")
            fm = add(
                f"<< /Type /XObject /Subtype /Form /BBox [0 0 {p.width} {p.height}] "
                f"/Matrix [{' '.join(str(v) for v in m)}] "
                f"/Resources << /Font << /F1 {font} 0 R >> >> "
                f"/Length {len(fb)} >>\nstream\n".encode()
                + fb
                + b"\nendstream"
            )
            xobj += f"/Fm1 {fm} 0 R ".encode()
            content = content + "/Fm1 Do\n"
        if xobj:
            xobj = b"/XObject << " + xobj + b">>"
        cb = content.encode("latin-1")
        cid = add(f"<< /Length {len(cb)} >>\nstream\n".encode() + cb + b"\nendstream")
        rot = f" /Rotate {p.rotate}" if p.rotate else ""
        pid = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {p.width} {p.height}]"
            f"{rot} /Contents {cid} 0 R /Resources << /Font << /F1 {font} 0 R >> ".encode()
            + xobj
            + b" >> >>"
        )
        kids.append(pid)
    objs[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] "
        f"/Count {len(kids)} >>".encode()
    )
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, b in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + b + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    path = Path(path)
    path.write_bytes(bytes(out))
    return path
