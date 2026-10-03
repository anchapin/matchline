# bem_helpers.py — shared helpers for gbXML and IFC export
# Extracted from bem_export.py to avoid duplication and reduce module sizes.

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from room_labels import point_in_polygon as _pip

GBXML_NS = "http://www.gbxml.org/schema"

WINDOW_SILL_M = 0.9
DOOR_SILL_M = 0.0


def _fmt(v: float) -> str:
    return f"{v:.4f}"


def _el(parent, tag, text=None, **attrib):
    el = ET.SubElement(parent, f"{{{GBXML_NS}}}{tag}", attrib)
    if text is not None:
        el.text = str(text)
    return el


def _cartesian(parent, x, y, z=None):
    pt = _el(parent, "CartesianPoint")
    _el(pt, "Coordinate", _fmt(x))
    _el(pt, "Coordinate", _fmt(y))
    if z is not None:
        _el(pt, "Coordinate", _fmt(z))
    return pt


def _wall_edges(ring_m):
    n = len(ring_m)
    return [(ring_m[i], ring_m[(i + 1) % n]) for i in range(n)]


def _assign_wall_to_space(p0, p1, spaces):
    """Which space does this exterior wall belong to?

    Midpoint nudged inward along the inward normal; point-in-polygon wins.
    Falls back to nearest space centroid. Deterministic.
    """
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy) or 1.0
    # outward normal for CCW ring: right of direction
    nx, ny = dy / L, -dx / L
    ix, iy = mx - nx * 0.3, my - ny * 0.3  # 0.3 m inward
    for sp in spaces:
        if _pip((ix, iy), sp.polygon_m):
            return sp

    # fallback: nearest centroid
    def centroid(sp):
        n = len(sp.polygon_m)
        return (sum(p[0] for p in sp.polygon_m) / n, sum(p[1] for p in sp.polygon_m) / n)

    return min(spaces, key=lambda sp: math.hypot(centroid(sp)[0] - ix, centroid(sp)[1] - iy))


def _distribute_openings(openings, edges):
    """Assign opening units to walls proportional to wall length.

    Largest-remainder apportionment per category, so per-category totals are
    exact and the assignment is deterministic. Returns {wall_idx: [units]}.
    """
    lengths = [math.hypot(p1[0] - p0[0], p1[1] - p0[1]) for p0, p1 in edges]
    total_L = sum(lengths) or 1.0
    assign = {i: [] for i in range(len(edges))}
    for cat in ("window", "door"):
        units = [u for u in openings if u.category == cat]
        n = len(units)
        if not n:
            continue
        shares = [n * L / total_L for L in lengths]
        base = [int(math.floor(sh)) for sh in shares]
        rem = n - sum(base)
        order = sorted(
            range(len(edges)), key=lambda i: (shares[i] - base[i], -lengths[i]), reverse=True
        )
        for i in order[:rem]:
            base[i] += 1
        k = 0
        for i, cnt in enumerate(base):
            assign[i].extend(units[k : k + cnt])
            k += cnt
    for i in assign:
        assign[i].sort(key=lambda u: (u.category, u.tag))
    return assign


def _opening_type(category: str) -> str:
    # openingTypeEnum has no generic "Door": NonSlidingDoor is the closest.
    if category == "skylight":
        return "FixedSkylight"
    return "FixedWindow" if category == "window" else "NonSlidingDoor"


SKYLIGHT_SETBACK_M = 0.30  # min distance from a skylight to the roof edge
SKYLIGHT_CLEARANCE_M = 0.30  # min gap between two skylights
SKYLIGHT_GRID_STEP_M = 0.25  # candidate-centre spacing


def _place_skylights_on_roof(units, ring):
    """Place skylight units on a flat roof over ``ring`` (CCW, metres).

    Takeoff lines carry a count and schedule dimensions, never a position,
    so placement is synthesised the same way wall openings are spread evenly
    along their walls. Each skylight is an axis-aligned rectangle (width
    along x, height along y) that must sit fully inside the roof, set back
    SKYLIGHT_SETBACK_M from its edge, with SKYLIGHT_CLEARANCE_M between
    skylights. Larger units are placed first. The first goes nearest the
    roof's interior point, each next one at the candidate farthest from those
    already placed, so the layout spreads over the roof instead of packing
    into one corner (top-lighting depends on the spread).

    Deterministic. A unit that cannot fit is skipped with a note, never
    shrunk: a scaled skylight would quietly change glazing area, which the
    energy model is sensitive to.

    Returns (placements, notes); each placement is
    {"unit": u, "rect": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]}, CCW.
    """
    import numpy as np
    import shapely
    from shapely.geometry import Polygon

    placements, notes = [], []
    if not units:
        return placements, notes
    roof = Polygon(ring)
    if not roof.is_valid or roof.area <= 0:
        notes.append(f"{len(units)} skylight(s) skipped: roof outline is degenerate")
        return placements, notes
    usable = roof.buffer(-SKYLIGHT_SETBACK_M)
    minx, miny, maxx, maxy = roof.bounds
    xs = np.arange(minx, maxx + 1e-9, SKYLIGHT_GRID_STEP_M)
    ys = np.arange(miny, maxy + 1e-9, SKYLIGHT_GRID_STEP_M)
    cx, cy = (a.ravel() for a in np.meshgrid(xs, ys))
    anchor = roof.representative_point()
    taken = None  # union of placed skylights grown by the clearance
    centres = []
    order = sorted(units, key=lambda u: (-(u.width_m * u.height_m), u.tag))
    for u in order:
        hw, hh = u.width_m / 2.0, u.height_m / 2.0
        boxes = shapely.box(cx - hw, cy - hh, cx + hw, cy + hh)
        ok = shapely.contains(usable, boxes) if not usable.is_empty else np.zeros(len(cx), bool)
        if taken is not None:
            ok &= ~shapely.intersects(taken, boxes)
        if not ok.any():
            notes.append(
                f"{u.tag}: skipped, no room for a {u.width_m:.2f}x{u.height_m:.2f} m "
                f"skylight on the roof (setback {SKYLIGHT_SETBACK_M} m, "
                f"clearance {SKYLIGHT_CLEARANCE_M} m)"
            )
            continue
        idx = np.flatnonzero(ok)
        if centres:
            pc = np.array(centres)
            d = np.min(np.hypot(cx[idx][:, None] - pc[:, 0], cy[idx][:, None] - pc[:, 1]), axis=1)
            k = idx[int(np.argmax(d))]
        else:
            k = idx[int(np.argmin(np.hypot(cx[idx] - anchor.x, cy[idx] - anchor.y)))]
        x, y = float(cx[k]), float(cy[k])
        rect = [(x - hw, y - hh), (x + hw, y - hh), (x + hw, y + hh), (x - hw, y + hh)]
        placements.append({"unit": u, "rect": rect})
        centres.append((x, y))
        grown = boxes[k].buffer(SKYLIGHT_CLEARANCE_M, join_style="mitre")
        taken = grown if taken is None else taken.union(grown)
    return placements, notes


def _place_openings_on_wall(units, L: float, h: float):
    placements, notes = [], []

    total_width = sum(u.width_m for u in units)
    usable = L - 0.1
    scale = 1.0
    if total_width > usable:
        scale = usable / total_width
        notes.append(
            f"Openings scaled to {scale:.3f} to fit wall {L:.2f} m "
            f"(total width {total_width:.2f} m)"
        )

    pos = 0.05
    for j, u in enumerate(units):
        sill = WINDOW_SILL_M if u.category == "window" else DOOR_SILL_M
        oh = u.height_m
        if sill >= h:
            notes.append(f"{u.tag}: skipped – sill {sill:.2f} m ≥ wall height {h:.2f} m")
            continue
        if sill + oh > h:
            oh = h - sill
            notes.append(f"{u.tag}: height clamped to {oh:.2f} m (wall {h:.2f} m)")
        oh = max(0, oh)

        scaled_w = u.width_m * scale
        s0 = pos
        s1 = pos + scaled_w
        pos = s1 + 0.1

        if s1 - s0 < scaled_w * 0.5:
            notes.append(
                f"{u.tag}: width clamped ({u.width_m:.2f} -> {s1 - s0:.2f} m, wall {L:.2f} m)"
            )
        placements.append({"unit": u, "s0": s0, "s1": s1, "sill": sill, "height": oh})

    return placements, notes


def _validate_out_path(path: str | Path) -> Path:
    """Validate output path is safe: reject traversal beyond cwd.

    Allows absolute paths (user-intended destinations).
    For relative paths: resolves symlinks and rejects if the result escapes cwd.
    """
    path = Path(path)
    if not path.is_absolute():
        resolved = (Path.cwd() / path).resolve()
        cwd_resolved = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd_resolved)
        except ValueError:
            raise ValueError(
                f"Output path '{path}' resolves to '{resolved}' which escapes "
                f"the working directory '{cwd_resolved}'. Rejecting to prevent "
                "path traversal."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
