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

from dataclasses import dataclass, replace
from typing import Optional, Sequence

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
# a thinner jamb wall (down to a 1 px line) is taken only where the vector
# walls report an opening there about as wide as the swing (#874)
THIN_WALL_PX, THIN_WALL_M = 1, 0.02
OPEN_WIDTH_TOL = 0.25  # opening width within +-25% of the swing radius
OPEN_CENTRE_TOL = 0.25  # opening midpoint within this share of r of the door's
OPEN_ANGLE_TOL_DEG = 15.0
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


def _band_at(ink: np.ndarray, y: int, x: int, p: _P, nearest: bool = False):
    """(centre, start, end) of a wall crossing row y near x, else None.

    A wall is the first ink met scanning from x - slack (with ``nearest``,
    the ink nearest x instead): either one band at least min_wall thick, or
    two thin lines whose outer span is.
    """
    if not (0 <= y < ink.shape[0]):
        return None
    row = ink[y]
    w = row.size
    lo, hi = max(0, x - p.slack), min(w, x + p.slack + 1)
    hits = np.flatnonzero(row[lo:hi])
    if hits.size == 0:
        return None
    s = lo + int(hits[np.argmin(np.abs(lo + hits - x))] if nearest else hits[0])
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

    A wall runs on; a letter stroke or a corner does not. When the first ink
    on one row is something else drawn beside the wall (a neighbouring door's
    leaf open along its other face, #875), the ink nearest x is read too.
    """
    for nearest in (False, True):
        a = _band_at(ink, y, x, p, nearest)
        b = _band_at(ink, y + step, x, p, nearest)
        if a is not None and b is not None and abs(a[0] - b[0]) <= 2:
            return (a[0] + b[0]) / 2, min(a[1], b[1]), max(a[2], b[2])
    return None


def _run_through(ink: np.ndarray, y: int, x: int, p: _P) -> bool:
    """Row y holds an ink run through x longer than any wall is thick."""
    if not (0 <= y < ink.shape[0]) or not ink[y, x]:
        return False
    row = ink[y]
    s = e = x
    while s > 0 and row[s - 1]:
        s -= 1
    while e < row.size and row[e]:
        e += 1
    return e - s > p.max_wall


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


def _hollow(ink: np.ndarray, y: int, wl, p: _P) -> bool:
    """Wall ``wl`` seen on row y is two thin lines with a clear gap between
    them, wider than two leaves (#875)."""
    if wl is None or wl[2] - wl[1] <= 2 * p.max_leaf or not (0 <= y < ink.shape[0]):
        return False
    return not ink[y, int(round(wl[0]))]


def _hinge_options(behind, past, p: _P, hollow_b: bool = False, hollow_p: bool = False):
    """(walls, hinge x) pairs to try for one leaf row and radius.

    The hinge is snapped to the centre of the wall the door is cut in, seen
    behind the leaf and past the opening. A thick wall drawn as two thin
    lines with a gap (a hollow jamb, #875) can hang its door on either face
    line instead, so a hollow wall also offers its two faces, tried with that
    wall alone too: the ink behind a door hung on a hollow wall's face is
    often a neighbouring door's leaf or jamb, not this door's wall.
    """
    walls = [wl for wl in (behind, past) if wl is not None]
    if not walls:
        return []
    out = [(walls, int(round(sum(wl[0] for wl in walls) / len(walls))))]
    for wl, hol in ((behind, hollow_b), (past, hollow_p)):
        if wl is None or not hol:
            continue
        for sx in (int(round(wl[0])), wl[1], wl[2] - 1):
            if sx not in [o[1] for o in out if o[0] == [wl]]:
                out.append(([wl], sx))
    return out


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
            if behind is None and not on_face and _run_through(ink, y - p.probe, s, p):
                # a separable leaf drawn just off a crossing wall's face: the
                # row behind it runs along that wall, so read the hinge wall
                # on the far side of it
                behind = _wall_at(ink, y - p.slack, -p.probe, s, p)
            best = None
            for r in radii.tolist():
                # the jamb past the opening can be thickened by a stub (the
                # hinge block of a door in the crossing wall), so if the wall
                # just past the jamb gives no clear opening, try it further on
                hol_b = _hollow(ink, y - p.probe, behind, p)
                for depth in (p.probe, p.slack):
                    past = _wall_at(ink, y + r + depth, p.probe, s, p)
                    if past is None and depth != p.probe:
                        break  # only a jamb seen right past the opening
                    # a jamb seen right past the opening whose second row
                    # meets a crossing wall (a T at the jamb) is read
                    # further along too
                    tee = past is None and _band_at(ink, y + r + depth, s, p) is not None
                    hol_p = _hollow(ink, y + r + depth, past, p)
                    clear = False
                    for walls, sx in _hinge_options(behind, past, p, hol_b, hol_p):
                        if sx + r > e - 1 + p.arc_tol:  # leaf must reach the arc
                            continue
                        ws, we = min(wl[1] for wl in walls), max(wl[2] for wl in walls)
                        if not _opening_clear(ink, y, r, (ws + 1, sx, we - 2), p):
                            continue
                        clear = True
                        cov = float(_arc_coverage(fat, sx, y, r)[0])
                        if cov < MIN_ARC_COVERAGE:
                            continue
                        cand = (float(_arc_coverage(tight, sx, y, r)[0]), cov, sx, y, r)
                        if best is None or cand[:2] > best[:2]:
                            best = cand
                    if clear or (past is None and not tee):
                        break
            if best is not None:
                fit, cov, sx, yy, r = best
                found.append((cov, fit, sx, yy, r))
    return found


def _view(a: np.ndarray, k: int, flip: bool) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(a[::-1] if flip else a, k))


def _candidates(ink0: np.ndarray, fat0: np.ndarray, tight0: np.ndarray, p: _P) -> list[dict]:
    """Swing candidates over all 8 views, in image coordinates."""
    yy, xx = np.indices(ink0.shape)
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
    return cands


def _on_opening(c: dict, openings_px) -> bool:
    """A vector wall opening as wide as the swing lies across its jambs (#874).

    ``openings_px`` are (a, b) segments in image pixels along the wall line.
    """
    (hx, hy), (jx, jy), r = c["hinge"], c["jamb"], c["r"]
    cx, cy = (hx + jx) / 2, (hy + jy) / 2
    th = np.arctan2(jy - hy, jx - hx)
    tol = np.radians(OPEN_ANGLE_TOL_DEG)
    for (ax, ay), (bx, by) in openings_px:
        L = float(np.hypot(bx - ax, by - ay))
        if L == 0 or abs(L - r) > OPEN_WIDTH_TOL * r:
            continue
        if np.hypot((ax + bx) / 2 - cx, (ay + by) / 2 - cy) > OPEN_CENTRE_TOL * r:
            continue
        d = abs((np.arctan2(by - ay, bx - ax) - th + np.pi / 2) % np.pi - np.pi / 2)
        if d <= tol:
            return True
    return False


def detect_door_swings(
    img: np.ndarray,
    px_per_m: float,
    *,
    min_width_m: float = MIN_WIDTH_M,
    max_width_m: float = MAX_WIDTH_M,
    ink_max: int = INK_MAX,
    openings_px: Optional[Sequence] = None,
) -> list[dict]:
    """Door symbols in ``img`` (2-D grayscale, 0 = black ink).

    ``openings_px``: wall openings the vector walls report, as ((ax, ay),
    (bx, by)) segments in image pixels. When given, a door whose jamb wall is
    thinner than ``MIN_WALL_M`` (down to a 1 px line) is kept only where one
    of them, about as wide as its swing, lies across its jambs (#874): at that
    weight a quarter-round fixture against a thin line looks like a door.
    """
    if img.ndim != 2:
        raise ValueError("expected a 2-D grayscale image")
    p = _params(px_per_m, min_width_m, max_width_m)
    ink0 = img < ink_max
    fat0 = _dilate(ink0, p.arc_tol)
    tight0 = _dilate(ink0, max(1, p.arc_tol // 2))
    cands = _candidates(ink0, fat0, tight0, p)
    if openings_px:
        thin = replace(p, min_wall=max(THIN_WALL_PX, int(round(THIN_WALL_M * px_per_m))))
        cands += [c for c in _candidates(ink0, fat0, tight0, thin) if _on_opening(c, openings_px)]
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
