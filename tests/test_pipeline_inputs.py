"""run_pipeline --set input resolution (#988): a zip of PDFs becomes one set.

A PDF passes straight through. A zip is unpacked: one member PDF is used
directly, several are concatenated in filename order with pdfium (the same
library the set stages already parse with) into one multi-sheet set PDF, so
the rest of the pipeline keeps seeing a single drawing set.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from types import SimpleNamespace

import pypdfium2
import pytest

from run_pipeline import StageError, _drawing_set_pdf


def _mini_pdf(path: Path, n_pages: int, width: float = 612.0) -> Path:
    doc = pypdfium2.PdfDocument.new()
    try:
        for _ in range(n_pages):
            doc.new_page(width, 792.0)
        doc.save(path)
    finally:
        doc.close()
    return path


def _zip(path: Path, members: list[tuple[str, Path]]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for arcname, src in members:
            zf.write(src, arcname=arcname)
    return path


def test_pdf_passthrough(tmp_path):
    pdf = _mini_pdf(tmp_path / "set.pdf", 1)
    assert _drawing_set_pdf(SimpleNamespace(set_pdf=pdf), tmp_path / "out") == pdf


def test_zip_single_member_is_used_directly(tmp_path):
    good = _mini_pdf(tmp_path / "only.pdf", 2)
    zf_path = _zip(tmp_path / "set.zip", [("only.pdf", good)])
    resolved = _drawing_set_pdf(SimpleNamespace(set_pdf=zf_path), tmp_path / "out")
    assert resolved != zf_path and resolved.exists()
    assert len(pypdfium2.PdfDocument(resolved)) == 2


def test_zip_single_pdf_among_other_files(tmp_path):
    good = _mini_pdf(tmp_path / "only.pdf", 1)
    zf_path = _zip(tmp_path / "set.zip", [("only.pdf", good)])
    with zipfile.ZipFile(zf_path, "a") as zf:
        zf.writestr("notes.txt", "not a drawing")
    resolved = _drawing_set_pdf(SimpleNamespace(set_pdf=zf_path), tmp_path / "out")
    assert resolved.name == "only.pdf"


def test_zip_multiple_members_merge_in_filename_order(tmp_path):
    a = _mini_pdf(tmp_path / "a.pdf", 2, width=100.0)  # narrow pages
    b = _mini_pdf(tmp_path / "b.pdf", 1, width=200.0)  # wide page
    zf_path = _zip(tmp_path / "set.zip", [("b.pdf", b), ("a.pdf", a)])
    resolved = _drawing_set_pdf(SimpleNamespace(set_pdf=zf_path), tmp_path / "out")
    doc = pypdfium2.PdfDocument(resolved)
    try:
        assert len(doc) == 3
        sizes = [doc[i].get_size()[0] for i in range(len(doc))]
        assert sizes == [100.0, 100.0, 200.0]  # a.pdf's sheets first
    finally:
        doc.close()


def test_zip_without_pdfs_raises(tmp_path):
    zf_path = tmp_path / "set.zip"
    with zipfile.ZipFile(zf_path, "w") as zf:
        zf.writestr("notes.txt", "no drawings here")
    with pytest.raises(StageError, match="holds no PDF"):
        _drawing_set_pdf(SimpleNamespace(set_pdf=zf_path), tmp_path / "out")


def test_corrupt_member_in_a_multi_pdf_zip_raises(tmp_path):
    good = _mini_pdf(tmp_path / "good.pdf", 1)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    zf_path = _zip(tmp_path / "set.zip", [("good.pdf", good), ("bad.pdf", bad)])
    with pytest.raises(StageError, match="could not be read as a PDF"):
        _drawing_set_pdf(SimpleNamespace(set_pdf=zf_path), tmp_path / "out")


def test_zip_with_two_members_sharing_a_name_raises(tmp_path):
    a = _mini_pdf(tmp_path / "a.pdf", 1)
    b = _mini_pdf(tmp_path / "b.pdf", 1)
    zf_path = _zip(
        tmp_path / "set.zip",
        [("sheets/floor.pdf", a), ("pdf/floor.pdf", b)],
    )
    with pytest.raises(StageError, match="two PDFs both named"):
        _drawing_set_pdf(SimpleNamespace(set_pdf=zf_path), tmp_path / "out")


def test_not_a_zip_raises(tmp_path):
    fake = tmp_path / "set.zip"
    fake.write_bytes(b"definitely not a zip")
    with pytest.raises(StageError, match="not a readable zip"):
        _drawing_set_pdf(SimpleNamespace(set_pdf=fake), tmp_path / "out")
