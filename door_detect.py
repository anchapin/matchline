"""Find door swings (leaf + quarter-circle arc) in a plan image.

numpy only. A plan door symbol is a straight leaf drawn from the hinge jamb
plus a 90-degree arc of the same radius from the leaf tip back to the far
jamb. Both jambs sit on the wall line, so the opening centre is their
midpoint and the opening width is the radius.

The search runs one canonical orientation (leaf pointing +x from the hinge,
arc sweeping toward +y) over the 8 rotations/flips of the image, so every
hinge side and swing direction is covered. A candidate counts only when the
whole symbol is there: a thin leaf, an arc inked along nearly its whole
length, a wall behind the hinge or past the far jamb, and a clear opening
between the jambs. It never guesses a door from a wall gap alone.

Walls may be drawn as a solid band (poche) or as two parallel lines. Pixel
tolerances are tuned at 50 px/m and grow with the drawing scale, never
shrink below the tuned values.

Outputs are in SHEET PIXELS; callers register them into plan metres.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

METHOD = "door_swing_arc"
INK_MAX = 100  # darker than this is ink; plan grid lines (grey 120) are not
MIN_WIDTH_M = 0.6
MAX_WIDTH_M = 1.25
ARC_SAMPLES = 48
MIN_ARC_COVERAGE = 0.9
GAP_MAX_INK = 0.2  # share of the opening's wall line allowed to carry ink

# tolerances at the 50 px/m reference scale, and their size in metres
ARC_TOL_PX, ARC_TOL_M = 2, 0.04
HINGE_SLACK_PX, HINGE_SLACK_M = 14, 0.28  # leaf may start inside its wall
MIN_WALL_PX, MIN_WALL_M = 6, 0.12  # a wall is at least this thick
WALL_PROBE_PX, WALL_PROBE_M = 4, 0.08  # how far past a jamb to look for it
MAX_LEAF_THICK_PX, MAX_LEAF_THICK_M = 6, 0.12  # leaves are thinner than walls
DEDUPE_PX, DEDUPE_M = 8, 0.16


@dataclass(frozen=True)
class _P:
    rmin: int
    rmax: int
    arc_tol: int
    slack: int
    min_wall: int
    max_wall: int
    probe: int
    max_leaf: int
    dedupe: int


def _params(px_per_m: float, min_width_m: float, max_width_m: float) -> _P:
    def px(ref, m):
        return max(ref, int(round(m * px_per_m)))

    slack = px(HINGE_SLACK_PX, HINGE_SLACK_M)
    return _P(
        rmin=int(np.floor(min_width_m * px_per_m)),
        rmax=int(np.ceil(max_width_m * px_per_m)),
        arc_tol=px(ARC_TOL_PX, ARC_TOL_M),
        slack=slack,
        min_wall=px(MIN_WALL_PX, MIN_WALL_M),
        max_wall=4 * slack,
        probe=px(WALL_PROBE_PX, WALL_PROBE_M),
        max_leaf=px(MAX_LEAF_THICK_PX, MAX_LEAF_THICK_M),
        dedupe=px(DEDUPE_PX, DEDUPE_M),
    )


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Square dilation, separable, no wrap-around at the borders."""
    if r <= 0:
        return mask.copy()
    out = mask
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        p = np.pad(out, pad)
        n = out.shape[axis]
        acc = np.zeros_like(out)
        for k in range(2 * r + 1):
            acc |= np.take(p, range(k, k + n), axis=axis)
        out = acc
    return out


def _row_runs(row: np.ndarray):
    padded = np.concatenate(([False], row, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist()))  # [start, end)


_TH = np.linspace(0.0, np.pi / 2, ARC_SAMPLES)
_COS, _SIN = np.cos(_TH), np.sin(_TH)


def _arc_coverage(fat: np.ndarray, hx: int, hy: int, r) -> np.ndarray:
    """Inked share of the quarter arc for each radius in r (array)."""
    r = np.atleast_1d(np.asarray(r, dtype=float))[:, None]
    xs = np.rint(hx + r * _COS).astype(int)
    ys = np.rint(hy + r * _SIN).astype(int)
    h, w = fat.shape
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    cov = fat[np.clip(ys, 0, h - 1), np.clip(xs, 0, w - 1)] & ok
    out = cov.mean(axis=1)
    out[~ok.all(axis=1)] = 0.0
    return out


def _thin_leaf(ink: np.ndarray, y: int, x0: int, x1: int, max_leaf: int) -> bool:
    """Ink through row y over the middle half of [x0, x1) is a thin line."""
    a, b = x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4
    if b <= a:
        return False
    h = ink.shape[0]
    lo, hi = max(0, y - max_leaf), min(h, y + max_leaf + 1)
    col = ink[lo:hi, a:b]
    k = y - lo
    up = np.cumprod(col[k::-1], axis=0).sum(axis=0)  # includes row y
    down = np.cumprod(col[k + 1 :], axis=0).sum(axis=0)
    return float(np.median(up + down)) <= max_leaf - 1


def _face_leaf(ink: np.ndarray, y: int, x0: int, x1: int, p: _P) -> bool:
    """Row y is the arc-side face of a wall band the open leaf lies on.

    A door hung beside a wall that meets its hinge jamb opens flat against
    that wall, so the drafted leaf merges into the wall's face: the ink is
    thick on the side away from the arc and (almost) nothing hangs below the
    face toward it. The arc, jamb and clear-opening checks still have to pass.
    """
    a, b = x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4
    if b <= a:
        return False
    h = ink.shape[0]
    lo, hi = max(0, y - p.max_wall), min(h, y + p.max_leaf + 1)
    col = ink[lo:hi, a:b]
    k = y - lo
    up = np.cumprod(col[k::-1], axis=0).sum(axis=0)  # includes row y
    down = np.cumprod(col[k + 1 :], axis=0).sum(axis=0)
    return float(np.median(up)) >= p.max_leaf and float(np.median(down)) <= p.max_leaf // 2


def _band_at(ink: np.ndarray, y: int, x: int, p: _P):
    """(centre, start, end) of a wall crossing row y near x, else None.

    A wall is the first ink met scanning from x - slack: either one band at
    least min_wall thick, or two thin lines whose outer span is.
    """
    if not (0 <= y < ink.shape[0]):
        return None
    row = ink[y]
    w = row.size
    lo, hi = max(0, x - p.slack), min(w, x + p.slack + 1)
    hits = np.flatnonzero(row[lo:hi])
    if hits.size == 0:
        return None
    s = lo + int(hits[0])
    while s > 0 and row[s - 1]:
        s -= 1
    e = s
    while e < w and row[e]:
        e += 1
    if p.min_wall <= e - s <= p.max_wall:
        return (s + e - 1) / 2, s, e
    if e - s >= p.min_wall:
        return None  # too thick to be a wall
    # thin first line: look for its partner within one wall thickness
    nxt = np.flatnonzero(row[e : min(w, s + p.max_wall)])
    if nxt.size == 0:
        return None
    s2 = e + int(nxt[0])
    e2 = s2
    while e2 < w and row[e2]:
        e2 += 1
    if e2 - s2 >= p.min_wall or not (p.min_wall <= e2 - s <= p.max_wall):
        return None
    return (s + e2 - 1) / 2, s, e2


def _wall_at(ink: np.ndarray, y: int, step: int, x: int, p: _P):
    """A wall seen on two rows (y and y + step) with the same centre.

    A wall runs on; a letter stroke or a corner does not.
    """
    a = _band_at(ink, y, x, p)
    b = _band_at(ink, y + step, x, p)
    if a is None or b is None or abs(a[0] - b[0]) > 2:
        return None
    return (a[0] + b[0]) / 2, min(a[1], b[1]), max(a[2], b[2])


def _opening_clear(ink: np.ndarray, y: int, r: int, cols, p: _P) -> bool:
    """The wall line between the jambs carries (almost) no ink."""
    y0, y1 = y + p.arc_tol, y + r - 2 * p.arc_tol
    if y1 <= y0:
        return False
    w = ink.shape[1]
    for c in cols:
        c = int(round(c))
        if not (1 <= c < w - 1):
            return False
        if ink[y0:y1, c - 1 : c + 2].all(axis=1).mean() > GAP_MAX_INK:
            return False
    return True


def _canonical(ink: np.ndarray, fat: np.ndarray, tight: np.ndarray, p: _P):
    """(coverage, fit, hinge x, hinge y, r) for leaf->+x, arc->+y symbols.

    The hinge is snapped to the centre of the wall the door is cut in, and
    the radius is the one whose arc best sits on the ink; the leaf run alone
    can't give it because a leaf often runs into the wall opposite.
    """
    found = []
    radii = np.arange(p.rmin, p.rmax + 1)
    for y in range(ink.shape[0]):
        runs = _row_runs(ink[y])
        for s, e in runs:
            length = e - s
            if length < p.rmin:
                continue
            # a run longer than any leaf can only be a leaf on a wall face
            face_only = length > p.rmax + 2 * p.slack
            # cheap first pass: some radius from near the run start must fit
            if _arc_coverage(fat, s, y, radii).max(initial=0.0) < MIN_ARC_COVERAGE - 0.2:
                hx_try = min(s + p.slack, e - 1 - p.rmin)
                if _arc_coverage(fat, hx_try, y, radii).max(initial=0.0) < MIN_ARC_COVERAGE - 0.2:
                    continue
            leaf_end = min(e, s + p.rmax + 2 * p.slack)
            on_face = face_only or not _thin_leaf(ink, y, s, e, p.max_leaf)
            if on_face and not _face_leaf(ink, y, s, leaf_end, p):
                continue
            # a leaf on a wall face: the row behind it runs through that wall,
            # so only the jamb wall past the opening places the hinge
            behind = None if on_face else _wall_at(ink, y - p.probe, -p.probe, s, p)
            best = None
            for r in radii.tolist():
                past = _wall_at(ink, y + r + p.probe, p.probe, s, p)
                walls = [wl for wl in (behind, past) if wl is not None]
                if not walls:
                    continue
                sx = int(round(sum(wl[0] for wl in walls) / len(walls)))
                if sx + r > e - 1 + p.arc_tol:  # leaf must reach the arc
                    continue
                ws, we = min(wl[1] for wl in walls), max(wl[2] for wl in walls)
                if not _opening_clear(ink, y, r, (ws + 1, sx, we - 2), p):
                    continue
                cov = float(_arc_coverage(fat, sx, y, r)[0])
                if cov < MIN_ARC_COVERAGE:
                    continue
                cand = (float(_arc_coverage(tight, sx, y, r)[0]), cov, sx, y, r)
                if best is None or cand[:2] > best[:2]:
                    best = cand
            if best is not None:
                fit, cov, sx, yy, r = best
                found.append((cov, fit, sx, yy, r))
    return found


def _view(a: np.ndarray, k: int, flip: bool) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(a[::-1] if flip else a, k))


def detect_door_swings(
    img: np.ndarray,
    px_per_m: float,
    *,
    min_width_m: float = MIN_WIDTH_M,
    max_width_m: float = MAX_WIDTH_M,
    ink_max: int = INK_MAX,
) -> list[dict]:
    """Door symbols in ``img`` (2-D grayscale, 0 = black ink)."""
    if img.ndim != 2:
        raise ValueError("expected a 2-D grayscale image")
    p = _params(px_per_m, min_width_m, max_width_m)
    ink0 = img < ink_max
    fat0 = _dilate(ink0, p.arc_tol)
    tight0 = _dilate(ink0, max(1, p.arc_tol // 2))
    yy, xx = np.indices(img.shape)
    cands = []
    for flip in (False, True):
        for k in range(4):
            ymap, xmap = _view(yy, k, flip), _view(xx, k, flip)
            ink, fat, tight = (_view(a, k, flip) for a in (ink0, fat0, tight0))
            for cov, fit, hx, hy, r in _canonical(ink, fat, tight, p):
                jy = min(hy + r, ink.shape[0] - 1)
                tx = min(hx + r, ink.shape[1] - 1)
                cands.append(
                    {
                        "score": cov,
                        "fit": fit,
                        "hinge": (float(xmap[hy, hx]), float(ymap[hy, hx])),
                        "jamb": (float(xmap[jy, hx]), float(ymap[jy, hx])),
                        "tip": (float(xmap[hy, tx]), float(ymap[hy, tx])),
                        "r": float(r),
                    }
                )
    # same symbol hits on neighbouring leaf rows and orientations: keep best
    cands.sort(key=lambda c: (-c["fit"], -c["score"], c["r"]))

    def _centre(c):
        return ((c["hinge"][0] + c["jamb"][0]) / 2, (c["hinge"][1] + c["jamb"][1]) / 2)

    kept: list[dict] = []
    for c in cands:
        if any(
            np.hypot(c["hinge"][0] - q["hinge"][0], c["hinge"][1] - q["hinge"][1]) <= p.dedupe
            and np.hypot(_centre(c)[0] - _centre(q)[0], _centre(c)[1] - _centre(q)[1]) <= p.dedupe
            for q in kept
        ):
            continue
        kept.append(c)
    out = []
    for i, c in enumerate(sorted(kept, key=lambda c: (c["hinge"][1], c["hinge"][0])), 1):
        (hx, hy), (jx, jy), (tx, ty) = c["hinge"], c["jamb"], c["tip"]
        xs, ys = (hx, jx, tx), (hy, jy, ty)
        out.append(
            {
                "id": f"DD{i}",
                "category": "door",
                "method": METHOD,
                "score": round(c["score"], 3),
                "x_px": (hx + jx) / 2,  # opening centre, on the wall line
                "y_px": (hy + jy) / 2,
                "width_px": c["r"],
                "hinge_px": [hx, hy],
                "bbox_px": [min(xs), min(ys), max(xs), max(ys)],
            }
        )
    return out
