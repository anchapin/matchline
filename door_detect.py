"""Find door swings (leaf + quarter-circle arc) in a plan image.

numpy only. A plan door symbol is a straight leaf drawn from the hinge jamb
plus a 90-degree arc of the same radius from the leaf tip back to the far
jamb. Both jambs sit on the wall line, so the opening centre is their
midpoint and the opening width is the radius.

The search runs one canonical orientation (leaf pointing +x from the hinge,
arc sweeping toward +y) over the 8 rotations/flips of the image, so every
hinge side and swing direction is covered. A candidate counts only when the
arc is inked along nearly its whole length; it never guesses a door from a
wall gap alone.

Outputs are in SHEET PIXELS; callers register them into plan metres.
"""

from __future__ import annotations

import numpy as np

METHOD = "door_swing_arc"
INK_MAX = 100  # darker than this is ink; plan grid lines (grey 120) are not
MIN_WIDTH_M = 0.6
MAX_WIDTH_M = 1.25
ARC_SAMPLES = 48
ARC_TOL_PX = 2
MIN_ARC_COVERAGE = 0.9
HINGE_SLACK_PX = 14  # leaf run may start inside the wall it hangs on
MIN_WALL_PX = 6  # a wall band is at least this thick
WALL_PROBE_PX = 4  # how far past a jamb to look for the wall
MAX_LEAF_THICK_PX = 6  # leaves are thin lines; walls are thicker bands
DEDUPE_PX = 8


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            out |= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
    return out


def _row_runs(row: np.ndarray):
    padded = np.concatenate(([False], row, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return zip(edges[0::2], edges[1::2])  # [start, end)


def _arc_coverage(fat: np.ndarray, hx: int, hy: int, r: int) -> float:
    th = np.linspace(0.0, np.pi / 2, ARC_SAMPLES)
    xs = np.rint(hx + r * np.cos(th)).astype(int)
    ys = np.rint(hy + r * np.sin(th)).astype(int)
    h, w = fat.shape
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    if not ok.all():
        return 0.0
    return float(fat[ys, xs].mean())


def _thin_leaf(ink: np.ndarray, y: int, x0: int, x1: int) -> bool:
    """Ink through row y over the middle half of [x0, x1) is a thin line.

    Measures the vertical ink thickness through row y in each column; a wall
    band is thicker than a leaf even when its neighbours are blank.
    """
    a, b = x0 + (x1 - x0) // 4, x1 - (x1 - x0) // 4
    if b <= a:
        return False
    h = ink.shape[0]
    lo, hi = max(0, y - MAX_LEAF_THICK_PX), min(h, y + MAX_LEAF_THICK_PX + 1)
    col = ink[lo:hi, a:b]
    k = y - lo
    up = np.cumprod(col[k::-1], axis=0).sum(axis=0)  # includes row y
    down = np.cumprod(col[k + 1 :], axis=0).sum(axis=0)
    thick = up + down
    return float(np.median(thick)) <= MAX_LEAF_THICK_PX - 1


def _band_through(row: np.ndarray, x: int):
    """(start, end) of the ink run in ``row`` covering column x, else None."""
    if not (0 <= x < row.size) or not row[x]:
        return None
    s = x
    while s > 0 and row[s - 1]:
        s -= 1
    e = x
    while e + 1 < row.size and row[e + 1]:
        e += 1
    return s, e + 1


def _band_centre(ink: np.ndarray, y: int, x: int):
    """Centre x of a wall-thick ink band crossing row y near x."""
    if not (0 <= y < ink.shape[0]):
        return None
    for xx in range(x - HINGE_SLACK_PX, x + HINGE_SLACK_PX + 1):
        band = _band_through(ink[y], xx)
        if band:
            w = band[1] - band[0]
            if MIN_WALL_PX <= w <= 4 * HINGE_SLACK_PX:
                return (band[0] + band[1] - 1) / 2
            return None  # first ink met is not a wall band
    return None


def _wall_centre(ink: np.ndarray, y: int, step: int, x: int):
    """Wall centre near x seen on two rows (y and y + step), consistently.

    A wall runs on; a letter stroke or a corner does not, so the band has to
    show at both probes with the same centre.
    """
    c1 = _band_centre(ink, y, x)
    c2 = _band_centre(ink, y + step, x)
    if c1 is None or c2 is None or abs(c1 - c2) > 2:
        return None
    return (c1 + c2) / 2


def _canonical(ink: np.ndarray, fat: np.ndarray, tight: np.ndarray, rmin: int, rmax: int):
    """Hinge (x, y), radius r for leaf->+x, arc->+y symbols.

    The hinge is snapped to the centre of the wall the door is cut in: that
    wall shows as a thick band just behind the hinge or just past the far
    jamb, and the wall line between the jambs must be clear (the opening).
    The radius is then the one whose arc best sits on the ink; the leaf run
    alone can't give it because a leaf often runs into the wall opposite.
    """
    found = []
    for y in range(ink.shape[0]):
        for s, e in _row_runs(ink[y]):
            length = e - s
            if length < rmin or length > rmax + 2 * HINGE_SLACK_PX:
                continue
            if not _thin_leaf(ink, y, s, e):
                continue
            behind = _wall_centre(ink, y - WALL_PROBE_PX, -WALL_PROBE_PX, s)
            best = None
            for r in range(rmin, rmax + 1):
                past = _wall_centre(ink, y + r + WALL_PROBE_PX, WALL_PROBE_PX, s)
                centres = [c for c in (behind, past) if c is not None]
                if not centres:
                    continue
                sx = int(round(sum(centres) / len(centres)))
                if sx + r > e - 1 + ARC_TOL_PX:  # leaf must reach the arc
                    continue
                gap = ink[y + 2 : y + r - 4, sx - 1 : sx + 2]
                if gap.size == 0 or gap.all(axis=1).mean() > 0.2:
                    continue
                cov = _arc_coverage(fat, sx, y, r)
                if cov < MIN_ARC_COVERAGE:
                    continue
                cand = (_arc_coverage(tight, sx, y, r), cov, sx, y, r)
                if best is None or cand[:2] > best[:2]:
                    best = cand
            if best is not None:
                fit, cov, sx, yy, r = best
                found.append((cov, fit, sx, yy, r))
    return found


def _transforms(shape):
    """Yield (k, flip) with index maps from transformed pixel -> original."""
    yy, xx = np.indices(shape)
    for flip in (False, True):
        fy = yy[::-1] if flip else yy
        fx = xx[::-1] if flip else xx
        for k in range(4):
            yield k, flip, np.rot90(fy, k), np.rot90(fx, k)


def detect_door_swings(
    img: np.ndarray,
    px_per_m: float,
    *,
    min_width_m: float = MIN_WIDTH_M,
    max_width_m: float = MAX_WIDTH_M,
) -> list[dict]:
    """Door symbols in ``img`` (2-D grayscale, 0 = black ink)."""
    if img.ndim != 2:
        raise ValueError("expected a 2-D grayscale image")
    ink0 = img < INK_MAX
    rmin = int(np.floor(min_width_m * px_per_m))
    rmax = int(np.ceil(max_width_m * px_per_m))
    cands = []
    for k, flip, ymap, xmap in _transforms(img.shape):
        ink = np.rot90(ink0[::-1] if flip else ink0, k)
        fat = _dilate(ink, ARC_TOL_PX)
        tight = _dilate(ink, 1)
        for cov, fit, hx, hy, r in _canonical(ink, fat, tight, rmin, rmax):
            jy, jx = hy + r, hx  # far jamb in the canonical frame
            tip = (int(ymap[hy, hx + r]), int(xmap[hy, hx + r]))
            cands.append(
                {
                    "score": cov,
                    "fit": fit,
                    "hinge": (float(xmap[hy, hx]), float(ymap[hy, hx])),
                    "jamb": (float(xmap[jy, jx]), float(ymap[jy, jx])),
                    "tip": (float(tip[1]), float(tip[0])),
                    "r": float(r),
                }
            )
    # same symbol hits on neighbouring leaf rows and hinge offsets: keep best
    cands.sort(key=lambda c: (-c["fit"], -c["score"], c["r"]))
    kept: list[dict] = []

    def _centre(c):
        return ((c["hinge"][0] + c["jamb"][0]) / 2, (c["hinge"][1] + c["jamb"][1]) / 2)

    for c in cands:
        if any(
            np.hypot(c["hinge"][0] - k_["hinge"][0], c["hinge"][1] - k_["hinge"][1]) <= DEDUPE_PX
            and np.hypot(_centre(c)[0] - _centre(k_)[0], _centre(c)[1] - _centre(k_)[1])
            <= DEDUPE_PX
            for k_ in kept
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
