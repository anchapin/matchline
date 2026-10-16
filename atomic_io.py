"""Atomic text-file writes (#1039): write to a temp sibling, then replace.

A crash, full disk, or a reader arriving mid-``write_text`` used to be able
to leave a truncated or empty JSON file at the destination. Writing to a
temporary file in the same directory and then ``os.replace``-ing it onto the
target leaves the destination with either the old bytes or the new ones,
never half of either, and readers on the same path never see a partial file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(p: Path, text: str) -> None:
    """Replace ``p`` with ``text`` atomically; the parent must already exist."""
    p = Path(p)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
