"""Procedural CAD-style symbol renderer for classifier training data.

Draws plan-view architectural symbols as crisp vector-like strokes on a
128x128 canvas with parametric variation (line weight, scale, rotation,
translation, background noise), then normalizes to the 28x28 classifier
input contract via ``datasets_adapter.normalize_crop``.

The label set matches AEC-geometric-bench's 8 object classes exactly, so
the sim-to-real gap experiment can train on these synthetic crops and test
on the 1,626 real crops from ``load_aec_bench``.

Glyph functions are also reused by ``synth.sheets`` to draw door/window
symbols onto synthetic floor plans at drawing scale, keeping the sheet
distribution matched to the training distribution.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import sys as _sys
sys_path = str(Path(__file__).resolve().parent.parent)
if sys_path not in _sys.path:
    _sys.path.insert(0, sys_path)
from datasets_adapter import normalize_crop  # noqa: E402

CANVAS = 128

# Label set mirrors AEC-geometric-bench object classes (gap experiment).
AEC_CLASSES = [
    "Single Swing Door",
    "Double Swing Door",
    "Window",
    "Sink",
    "Toilet",
    "Bathtub",
    "Shower",
    "Cooktops",
]
EXTRA_CLASSES = ["Sliding Door"]
ALL_CLASSES = AEC_CLASSES + EXTRA_CLASSES


# ---------------------------------------------------------------------------
# Glyph primitives. Each takes (d, cx, cy, w, lw[, rng]) where w is the
# characteristic width in px and lw the stroke width. y grows downward.
# ---------------------------------------------------------------------------

def glyph_single_door(d: ImageDraw.ImageDraw, cx, cy, w, lw,
                      wall: bool = True, rng=None):
    """Plan door: wall line with gap, leaf + quarter swing arc below."""
    wt = max(6, int(w * 0.22))          # wall thickness
    if wall:                            # wall stubs across the canvas
        d.line([0, cy, CANVAS, cy], fill=0, width=wt)
        d.rectangle([cx - w / 2, cy - wt / 2 - 2, cx + w / 2, cy + wt / 2 + 2],
                    fill=255)
    hx, hy = cx - w / 2, cy            # hinge at left jamb
    ang = math.radians(58)
    ex, ey = hx + w * math.cos(ang), hy + w * math.sin(ang)
    d.line([hx, hy, ex, ey], fill=0, width=lw)
    d.arc([hx - w, hy - w, hx + w, hy + w], start=0, end=58, fill=0, width=lw)


def glyph_double_door(d: ImageDraw.ImageDraw, cx, cy, w, lw,
                      wall: bool = True, rng=None):
    wt = max(6, int(w * 0.22))
    if wall:
        d.line([0, cy, CANVAS, cy], fill=0, width=wt)
        d.rectangle([cx - w / 2, cy - wt / 2 - 2, cx + w / 2, cy + wt / 2 + 2],
                    fill=255)
    ang = math.radians(58)
    for sgn, hx in ((-1, cx - w / 2), (1, cx + w / 2)):
        ex = hx + sgn * (w / 2) * math.cos(ang)
        ey = cy + (w / 2) * math.sin(ang)
        d.line([hx, cy, ex, ey], fill=0, width=lw)
        r = w / 2
        if sgn < 0:
            d.arc([hx - r, cy - r, hx + r, cy + r], start=0, end=58,
                  fill=0, width=lw)
        else:
            d.arc([hx - r, cy - r, hx + r, cy + r], start=122, end=180,
                  fill=0, width=lw)


def glyph_window_plan(d: ImageDraw.ImageDraw, cx, cy, w, lw,
                      wall: bool = True, rng=None):
    """Plan window: double wall line, 3 glass lines + end caps in the gap."""
    wt = max(8, int(w * 0.30))
    if wall:
        d.line([0, cy - wt / 2, CANVAS, cy - wt / 2], fill=0, width=max(3, lw))
        d.line([0, cy + wt / 2, CANVAS, cy + wt / 2], fill=0, width=max(3, lw))
        d.rectangle([cx - w / 2, cy - wt / 2 - 2, cx + w / 2, cy + wt / 2 + 2],
                    fill=255)
    for yy in (cy - wt / 2, cy, cy + wt / 2):
        d.line([cx - w / 2, yy, cx + w / 2, yy], fill=0, width=max(2, lw - 1))
    d.line([cx - w / 2, cy - wt / 2, cx - w / 2, cy + wt / 2], fill=0, width=lw)
    d.line([cx + w / 2, cy - wt / 2, cx + w / 2, cy + wt / 2], fill=0, width=lw)


def glyph_sink(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    hw, hh = w / 2, w * 0.38
    d.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], outline=0, width=lw)
    if rng is not None and rng.random() < 0.35:      # double basin
        for sgn in (-1, 1):
            bx = cx + sgn * hw / 2
            d.rectangle([bx - hw / 2 + 5, cy - hh + 5, bx + hw / 2 - 5,
                         cy + hh - 5], outline=0, width=max(2, lw - 1))
    else:
        d.rectangle([cx - hw + 6, cy - hh + 6, cx + hw - 6, cy + hh - 6],
                    outline=0, width=max(2, lw - 1))


def glyph_toilet(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    tw, th = w * 0.62, w * 0.26                       # tank
    d.rectangle([cx - tw / 2, cy - w / 2, cx + tw / 2, cy - w / 2 + th],
                outline=0, width=lw)
    d.ellipse([cx - w * 0.28, cy - w / 2 + th + 2,     # bowl
               cx + w * 0.28, cy + w / 2], outline=0, width=lw)


def glyph_bathtub(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    hw, hh = w * 0.62, w * 0.34
    d.rounded_rectangle([cx - hw, cy - hh, cx + hw, cy + hh], radius=8,
                        outline=0, width=lw)
    d.rectangle([cx - hw + 7, cy - hh + 7, cx + hw - 7, cy + hh - 7],
                outline=0, width=max(2, lw - 1))


def glyph_shower(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    h = w * 0.44
    d.rectangle([cx - h, cy - h, cx + h, cy + h], outline=0, width=lw)
    if rng is not None and rng.random() < 0.5:         # X drain
        d.line([cx - h, cy - h, cx + h, cy + h], fill=0, width=max(2, lw - 1))
        d.line([cx - h, cy + h, cx + h, cy - h], fill=0, width=max(2, lw - 1))
    else:                                             # single diagonal
        d.line([cx - h, cy - h, cx + h, cy + h], fill=0, width=max(2, lw - 1))


def glyph_cooktop(d: ImageDraw.ImageDraw, cx, cy, w, lw, rng=None):
    hw, hh = w / 2, w * 0.34
    d.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], outline=0, width=lw)
    r = w * 0.11
    for sx in (-1, 1):
        for sy in (-1, 1):
            bx, by = cx + sx * hw * 0.5, cy + sy * hh * 0.45
            d.ellipse([bx - r, by - r, bx + r, by + r], outline=0,
                      width=max(2, lw - 1))


def glyph_sliding_door(d: ImageDraw.ImageDraw, cx, cy, w, lw,
                       wall: bool = True, rng=None):
    wt = max(6, int(w * 0.22))
    if wall:
        d.line([0, cy, CANVAS, cy], fill=0, width=wt)
        d.rectangle([cx - w / 2, cy - wt / 2 - 2, cx + w / 2, cy + wt / 2 + 2],
                    fill=255)
    ph = w * 0.58                                     # panel length
    d.rectangle([cx - ph / 2 - 6, cy - wt / 2 - 5, cx + ph / 2 - 6,
                 cy - wt / 2 - 1], outline=0, width=max(2, lw - 1))
    d.rectangle([cx - ph / 2 + 6, cy + wt / 2 + 1, cx + ph / 2 + 6,
                 cy + wt / 2 + 5], outline=0, width=max(2, lw - 1))


GLYPHS = {
    "Single Swing Door": glyph_single_door,
    "Double Swing Door": glyph_double_door,
    "Window": glyph_window_plan,
    "Sink": glyph_sink,
    "Toilet": glyph_toilet,
    "Bathtub": glyph_bathtub,
    "Shower": glyph_shower,
    "Cooktops": glyph_cooktop,
    "Sliding Door": glyph_sliding_door,
}


# ---------------------------------------------------------------------------
# Rendering with parametric variation
# ---------------------------------------------------------------------------

def render_symbol(label: str, rng: np.random.Generator,
                  out_size: int = 28,
                  glyphs: dict | None = None) -> np.ndarray:
    """Render one labeled symbol crop: (out_size, out_size) float64 0..255,
    dark ink on light background -- the classifier input contract.

    ``glyphs`` overrides the glyph map (e.g. synth.lighting.LIGHTING_GLYPHS);
    defaults to the AEC set in GLYPHS."""
    glyph_map = glyphs if glyphs is not None else GLYPHS
    img = Image.new("L", (CANVAS, CANVAS), 255)
    d = ImageDraw.Draw(img)
    s = rng.uniform(0.85, 1.12)
    w = 84.0 * s          # fill the frame: tight crops match real ann. boxes
    lw = int(rng.choice([3, 4, 5]))
    cx = CANVAS / 2 + rng.uniform(-7, 7)
    cy = CANVAS / 2 + rng.uniform(-7, 7)
    glyph_map[label](d, cx, cy, w, lw, rng=rng)    # orientation augmentation: real drawings show doors/windows in all
    # four wall orientations; the base glyphs are drawn axis-aligned.
    k90 = int(rng.integers(0, 4))
    if k90:
        img = img.rotate(90 * k90, expand=False)
    if rng.random() < 0.5:                       # mirror: both chiralities
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    ang = rng.uniform(-5, 5)
    if abs(ang) > 0.5:
        img = img.rotate(ang, resample=Image.BICUBIC, fillcolor=255)
    arr = np.asarray(img).astype(np.float64)
    arr += rng.normal(0.0, rng.uniform(0.0, 3.5), arr.shape)  # sensor noise
    arr = np.clip(arr, 0, 255)
    return normalize_crop(arr, size=out_size)


def make_symbol_dataset(class_names: list[str], n_per_class: int,
                        seed: int) -> tuple[np.ndarray, np.ndarray]:
    """(X, y): X (N,28,28) float64, y int labels into class_names."""
    rng = np.random.default_rng(seed)
    X, y = [], []
    for i, name in enumerate(class_names):
        for _ in range(n_per_class):
            X.append(render_symbol(name, rng))
            y.append(i)
    X = np.stack(X)
    y = np.asarray(y, dtype=np.int64)
    perm = rng.permutation(len(y))
    return X[perm], y[perm]


def save_crops(X: np.ndarray, y: np.ndarray, class_names: list[str],
               outdir: str | Path, source: str = "synth") -> Path:
    """Write crops as PNGs + a manifest CSV. Returns the manifest path."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    manifest = outdir / "crops_manifest.csv"
    with open(manifest, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["path", "label", "source"])
        for i, (im, yi) in enumerate(zip(X, y)):
            label = class_names[int(yi)]
            sub = outdir / label.replace(" ", "_")
            sub.mkdir(exist_ok=True)
            p = sub / f"crop_{i:05d}.png"
            Image.fromarray(np.asarray(im).astype(np.uint8)).save(p)
            wr.writerow([str(p.relative_to(outdir)), label, source])
    return manifest
