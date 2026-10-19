"""Cross-platform font resolution for synthetic rendering (#1163).

The synthetic sheets used to hardcode the Linux DejaVu path, which made
the demo die at Stage 1 on macOS and Windows; these tests pin the
candidate-fallback behavior of synth.symbols._render_font.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

import synth.symbols as symbols  # noqa: E402


def _draw_bbox(font):
    img = Image.new("L", (64, 32), 255)
    d = ImageDraw.Draw(img)
    return d.textbbox((0, 0), "EXIT", font=font)


def test_render_font_picks_a_platform_font():
    assert _draw_bbox(symbols._render_font(24)) is not None


def test_render_font_skips_missing_candidates(monkeypatch):
    monkeypatch.setattr(symbols, "_FONT_CANDIDATES", ("/nonexistent/font.ttf",))
    assert _draw_bbox(symbols._render_font(24)) is not None


def test_render_font_survives_broken_font_file(monkeypatch, tmp_path):
    bad = tmp_path / "fake.ttf"
    bad.write_bytes(b"not a font")
    monkeypatch.setattr(symbols, "_FONT_CANDIDATES", (str(bad),))
    assert _draw_bbox(symbols._render_font(24)) is not None
