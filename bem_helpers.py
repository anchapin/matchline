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
    return "FixedWindow" if category == "window" else "NonSlidingDoor"


def _place_openings_on_wall(units, L: float, h: float):
    """Deterministic opening layout on one wall.

    Evenly spaces the wall's units along its length (centers at
    (j+0.5)*L/k). Returns (placements, notes); placements are dicts
    {unit, s0, s1, sill, height} with s measured from the wall START point
    p0 along the ring edge direction. Widths/heights are clamped to fit
    the wall; every clamp is reported in notes (never silent).
    """
    placements, notes = [], []
    k = len(units)
    for j, u in enumerate(units):
        sill = WINDOW_SILL_M if u.category == "window" else DOOR_SILL_M
        oh = u.height_m
        if sill + oh > h:
            oh = h - sill
            notes.append(f"{u.tag}: height clamped to {oh:.2f} m (wall {h:.2f} m)")
        c = (j + 0.5) * L / k
        s0 = max(0.05, c - u.width_m / 2)
        s1 = min(L - 0.05, c + u.width_m / 2)
        if s1 - s0 < u.width_m * 0.5:
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
