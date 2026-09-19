"""Synthetic lighting-fixture symbols + schedule data.

Plan-view fixture glyphs drawn in the same CAD style as synth.symbols
(dark strokes on light, parametric line weight/scale/rotation), so they
can be rendered into classifier crops via ``symbols.render_symbol`` with
``glyphs=LIGHTING_GLYPHS`` and pasted onto synthetic sheets with the same
temp-canvas technique as synth.sheets.

Also provides a realistic synthetic lighting schedule (tag -> fixture
description, lamp type, watts per fixture) matching the columns that
``datasets_adapter.parse_lighting_schedule_csv`` expects.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import sys as _sys
sys_path = str(Path(__file__).resolve().parent.parent)
if sys_path not in _sys.path:
    _sys.path.insert(0, sys_path)
from synth.symbols import render_symbol, CANVAS  # noqa: E402

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

LIGHTING_CLASSES = [
    "Troffer 2x4",
    "Troffer 2x2",
    "Downlight",
    "Pendant",
    "Wall Sconce",
    "Exit Sign",
]


# ---------------------------------------------------------------------------
# Glyph primitives. Signature matches synth.symbols: (d, cx, cy, w, lw[, rng]).
# y grows downward. Fixtures are ceiling-mounted (orientation-free) except
# the sconce/exit, which mount on walls -- train-time 90-degree rotations in
# render_symbol cover wall orientations.
# ---------------------------------------------------------------------------

def glyph_troffer_2x4(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    """Recessed 2x4 troffer: outer rect, inner lens rect, louver dividers."""
    hw, hh = w / 2, w * 0.25
    d.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], outline=0, width=lw)
    d.rectangle([cx - hw + 6, cy - hh + 6, cx + hw - 6, cy + hh - 6],
                outline=0, width=max(2, lw - 1))
    for fx in (-0.25, 0.0, 0.25):          # louver cells
        x = cx + fx * w
        d.line([x, cy - hh + 6, x, cy + hh - 6], fill=0,
               width=max(2, lw - 1))


def glyph_troffer_2x2(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    h = w * 0.36
    d.rectangle([cx - h, cy - h, cx + h, cy + h], outline=0, width=lw)
    d.rectangle([cx - h + 6, cy - h + 6, cx + h - 6, cy + h - 6],
                outline=0, width=max(2, lw - 1))
    d.line([cx, cy - h + 6, cx, cy + h - 6], fill=0, width=max(2, lw - 1))
    d.line([cx - h + 6, cy, cx + h - 6, cy], fill=0, width=max(2, lw - 1))


def glyph_downlight(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    """Can/downlight: circle with cross (plan symbol)."""
    r = w * 0.32
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=lw)
    d.line([cx - r, cy, cx + r, cy], fill=0, width=max(2, lw - 1))
    d.line([cx, cy - r, cx, cy + r], fill=0, width=max(2, lw - 1))


def glyph_pendant(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    """Pendant: shade ring + large stem dot (vs. downlight's cross)."""
    r = w * 0.30
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=lw)
    sr = max(5, w * 0.13)
    d.ellipse([cx - sr, cy - sr, cx + sr, cy + sr], fill=0)


def glyph_wall_sconce(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    """Wall sconce: half-disc (D) mounted on a wall line.

    The wall stub is the discriminative feature vs. the downlight's free
    circle+cross -- real sconce symbols are drawn on walls, so this matches
    drafting convention as well as helping the classifier."""
    wt = max(6, int(w * 0.22))
    d.line([0, cy, CANVAS, cy], fill=0, width=wt)   # wall
    d.rectangle([cx - w / 2, cy - wt / 2 - 2, cx + w / 2, cy + wt / 2 + 2],
                fill=255)                            # carve gap
    r = w * 0.30
    d.pieslice([cx - r, cy - r, cx + r, cy + r], start=270, end=90,
               outline=0, width=lw)
    d.line([cx, cy - r, cx, cy + r], fill=0, width=lw)   # mounting chord
    if rng is not None and rng.random() < 0.5:           # shade tick
        d.line([cx, cy, cx + r * 0.7, cy], fill=0, width=max(2, lw - 1))


def glyph_exit_sign(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    """Exit sign: text-dominated face -- the rect hugs the EXIT text tightly,
    so at crop scale the glyph reads as a bold text blob, unlike the
    troffer's louver grid."""
    txt = "EXIT"
    size = int(w * 0.34)
    try:
        fnt = ImageFont.truetype(FONT_PATH, size)
    except OSError:
        fnt = ImageFont.load_default()
    bb = d.textbbox((0, 0), txt, font=fnt)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    hw, hh = tw / 2 + 7, th / 2 + 6
    d.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], outline=0, width=lw)
    d.text((cx - tw / 2 - bb[0], cy - th / 2 - bb[1]), txt, fill=0, font=fnt)


LIGHTING_GLYPHS = {
    "Troffer 2x4": glyph_troffer_2x4,
    "Troffer 2x2": glyph_troffer_2x2,
    "Downlight": glyph_downlight,
    "Pendant": glyph_pendant,
    "Wall Sconce": glyph_wall_sconce,
    "Exit Sign": glyph_exit_sign,
}


def render_lighting_symbol(label: str, rng: np.random.Generator,
                           out_size: int = 28) -> np.ndarray:
    """One labeled fixture crop, same contract as symbols.render_symbol."""
    return render_symbol(label, rng, out_size=out_size, glyphs=LIGHTING_GLYPHS)


# ---------------------------------------------------------------------------
# Synthetic lighting schedule (realistic commercial values)
# ---------------------------------------------------------------------------

# tag, description, lamp_type, watts per fixture
LIGHTING_SCHEDULE = [
    {"tag": "A", "description": "2x4 recessed LED troffer, 4000K",
     "lamp_type": "LED", "watts": 45.0},
    {"tag": "B", "description": "2x2 recessed LED troffer, 4000K",
     "lamp_type": "LED", "watts": 30.0},
    {"tag": "C", "description": '6" LED downlight, 3000K',
     "lamp_type": "LED", "watts": 12.0},
    {"tag": "D", "description": "LED pendant, direct/indirect",
     "lamp_type": "LED", "watts": 24.0},
    {"tag": "E", "description": "LED wall sconce",
     "lamp_type": "LED", "watts": 15.0},
    {"tag": "X", "description": "LED exit sign, battery backup",
     "lamp_type": "LED", "watts": 5.0},
]
LIGHTING_SCHED_BY_TAG = {s["tag"]: s for s in LIGHTING_SCHEDULE}

# Which schedule tag each fixture class carries on synthetic sheets.
CLASS_TO_TAG = {
    "Troffer 2x4": "A",
    "Troffer 2x2": "B",
    "Downlight": "C",
    "Pendant": "D",
    "Wall Sconce": "E",
    "Exit Sign": "X",
}


def lighting_schedule_csv(rows: list[dict] | None = None) -> io.StringIO:
    """Render schedule rows as the CSV that parse_lighting_schedule_csv reads."""
    rows = LIGHTING_SCHEDULE if rows is None else rows
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["tag", "description", "lamp_type", "watts"])
    for r in rows:
        wr.writerow([r["tag"], r["description"], r["lamp_type"], r["watts"]])
    buf.seek(0)
    return buf
