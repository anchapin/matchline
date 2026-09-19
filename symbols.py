"""Programmatic clean vector-style symbol renderer (112x112, downsampled to 28x28).

Approximates the architectural-drawing use case: geometric/GD&T-like glyphs
drawn as crisp vector strokes with jitter (rotation/translation/scale/stroke).
"""

import numpy as np

CANVAS = 112
OUT = 28


def _line(img, x0, y0, x1, y1, thick):
    n = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
    xs = np.linspace(x0, x1, n)
    ys = np.linspace(y0, y1, n)
    rr = int(np.ceil(thick / 2))
    for x, y in zip(xs, ys):
        xi, yi = int(round(x)), int(round(y))
        img[max(0, yi - rr) : yi + rr + 1, max(0, xi - rr) : xi + rr + 1] = 1.0


def _circle(img, cx, cy, r, thick):
    n = max(int(2 * np.pi * r * 2), 64)
    th = np.linspace(0, 2 * np.pi, n)
    _poly(img, cx + r * np.cos(th), cy + r * np.sin(th), thick)


def _poly(img, xs, ys, thick):
    for x0, y0, x1, y1 in zip(xs[:-1], ys[:-1], xs[1:], ys[1:]):
        _line(img, x0, y0, x1, y1, thick)


def _arc(img, cx, cy, r, a0, a1, thick):
    n = max(int(abs(a1 - a0) * r * 2), 32)
    th = np.linspace(a0, a1, n)
    _poly(img, cx + r * np.cos(th), cy + r * np.sin(th), thick)


def draw(cls: int, thick: float = 7.0) -> np.ndarray:
    """Draw symbol class 0..9 centered on a 112x112 canvas."""
    img = np.zeros((CANVAS, CANVAS))
    c, R = CANVAS / 2, 34
    if cls == 0:  # circle
        _circle(img, c, c, R, thick)
    elif cls == 1:  # square
        _poly(img, [c - R, c + R, c + R, c - R, c - R], [c - R, c - R, c + R, c + R, c - R], thick)
    elif cls == 2:  # triangle
        _poly(img, [c, c + R, c - R, c], [c - R, c + R * 0.8, c + R * 0.8, c - R], thick)
    elif cls == 3:  # cross (plus)
        _line(img, c - R, c, c + R, c, thick)
        _line(img, c, c - R, c, c + R, thick)
    elif cls == 4:  # perpendicularity (T)
        _line(img, c - R, c + R * 0.6, c + R, c + R * 0.6, thick)
        _line(img, c, c + R * 0.6, c, c - R, thick)
    elif cls == 5:  # parallelism (two bars)
        _line(img, c - R * 0.5, c - R, c - R * 0.5, c + R, thick)
        _line(img, c + R * 0.5, c - R, c + R * 0.5, c + R, thick)
    elif cls == 6:  # angularity (angle)
        _line(img, c - R, c + R * 0.5, c + R, c + R * 0.5, thick)
        _line(img, c - R, c + R * 0.5, c + R * 0.4, c - R * 0.7, thick)
    elif cls == 7:  # position (circle + crosshair)
        _circle(img, c, c, R, thick)
        _line(img, c - R, c, c + R, c, thick)
        _line(img, c, c - R, c, c + R, thick)
    elif cls == 8:  # door (rect + quarter swing arc)
        _poly(img, [c - R, c + R, c + R, c - R, c - R], [c - R, c - R, c + R, c + R, c - R], thick)
        _arc(img, c - R, c + R, 2 * R, -np.pi / 2, 0, thick)
    elif cls == 9:  # window (double line + mullions)
        _line(img, c - R, c - R * 0.4, c + R, c - R * 0.4, thick)
        _line(img, c - R, c + R * 0.4, c + R, c + R * 0.4, thick)
        for fx in (-0.5, 0.0, 0.5):
            _line(img, c + fx * R, c - R * 0.4, c + fx * R, c + R * 0.4, thick)
    else:
        raise ValueError(cls)
    return img


def jitter(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Rotation +-8 deg, translation +-6 px, scale 0.92..1.08 (applied at 112px)."""
    ang = np.deg2rad(rng.uniform(-8, 8))
    tx, ty = rng.uniform(-6, 6, 2)
    s = rng.uniform(0.92, 1.08)
    n = img.shape[0]
    yy, xx = np.mgrid[0:n, 0:n].astype(float)
    # inverse map: out(y,x) = in( R^-1 (p - c - t)/s + c )
    ca, sa = np.cos(ang), np.sin(ang)
    px = (xx - n / 2 - tx) / s
    py = (yy - n / 2 - ty) / s
    sx = ca * px + sa * py + n / 2
    sy = -sa * px + ca * py + n / 2
    xi = np.clip(np.round(sx).astype(int), 0, n - 1)
    yi = np.clip(np.round(sy).astype(int), 0, n - 1)
    return img[yi, xi]


def to28(img: np.ndarray) -> np.ndarray:
    """Block-average 112x112 -> 28x28, scale to 0..255."""
    b = img.reshape(OUT, 4, OUT, 4).mean(axis=(1, 3))
    return np.clip(b * 255, 0, 255)


NAMES = [
    "circle",
    "square",
    "triangle",
    "cross",
    "perpendicularity",
    "parallelism",
    "angularity",
    "position",
    "door",
    "window",
]


def make_dataset(n_train: int, n_test: int, seed: int):
    rng = np.random.default_rng(seed)
    Xtr, ytr, Xte, yte = [], [], [], []
    for cls in range(10):
        for _ in range(n_train):
            th = rng.uniform(5, 9)
            Xtr.append(to28(jitter(draw(cls, th), rng)))
            ytr.append(cls)
        for _ in range(n_test):
            th = rng.uniform(5, 9)
            Xte.append(to28(jitter(draw(cls, th), rng)))
            yte.append(cls)
    return (
        np.array(Xtr),
        np.array(ytr, dtype=np.int64),
        np.array(Xte),
        np.array(yte, dtype=np.int64),
    )
