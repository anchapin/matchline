"""Shading surfaces from the canonical model into BEM coordinates (roadmap item 5).

Each ``ShadingSurface`` is placed in its host wall's frame: ``along_m`` from
the wall's ``from_m`` end, ``z_m`` above the floor, ``depth_m`` out from the
exterior face. This module turns that into an absolute quad.

The outward side of a wall is not stored on ``EnvelopeWall``, so it is
decided geometrically: the side whose nearby point falls inside a space is
inside. A wall with spaces on neither side (or both) cannot be oriented and
its shading is skipped with a note, never guessed.

Both export adapters use this, each passing its own plan transform (one
flips y to north-up, the other keeps the linked model's frame), so the
shades land in the same frame as that adapter's walls.
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, List, Tuple

from bem_geometry import BEMShade

_PROBE_M = 0.05  # how far off the wall face to test inside/outside


def _inside(pt, poly) -> bool:
    x, y = pt
    n = len(poly)
    hit = False
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xc = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if xc > x:
                hit = not hit
    return hit


def _outward(p0, p1, polys, off=0.0) -> Tuple[float, float] | None:
    """Outward unit normal of a host segment, probing just past ``off`` (the wall face)."""
    L = math.dist(p0, p1)
    ux, uy = (p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    a = (uy, -ux)
    b = (-uy, ux)
    r = off + _PROBE_M
    in_a = any(_inside((mx + a[0] * r, my + a[1] * r), p) for p in polys)
    in_b = any(_inside((mx + b[0] * r, my + b[1] * r), p) for p in polys)
    if in_a == in_b:
        return None
    return b if in_a else a


def shades_from_model(
    model,
    transform: Callable[[Iterable[float]], Tuple[float, float]],
    space_polys: List[list],
) -> Tuple[List[BEMShade], List[str]]:
    """Absolute shade quads for every placeable ``model.shading`` entry.

    Args:
        model: canonical BuildingModel.
        transform: maps a canonical [x, y] to the adapter's plan frame.
        space_polys: the adapter's space polygons, in that same frame.

    Returns:
        (shades, notes). Every surface that could not be placed gets a note
        naming it and the reason.
    """
    shading = getattr(model, "shading", None) or []
    if not shading:
        return [], []
    walls = {w.id: w for w in getattr(model, "envelope", []) or []}
    shades: List[BEMShade] = []
    notes: List[str] = []
    for sh in shading:
        w = walls.get(sh.host_wall_id)
        if w is None or not w.from_m or not w.to_m:
            notes.append(f"shade {sh.id} skipped: no host wall geometry")
            continue
        p0, p1 = transform(w.from_m), transform(w.to_m)
        L = math.dist(p0, p1)
        if L < 1e-6:
            notes.append(f"shade {sh.id} skipped: host wall {w.id} has no length")
            continue
        fin = sh.kind == "fin"
        need = [sh.along_m, sh.z_m, sh.depth_m, sh.height_m if fin else sh.width_m]
        if any(v is None for v in need) or sh.depth_m <= 0:
            notes.append(f"shade {sh.id} skipped: placement or size incomplete")
            continue
        off = float(getattr(sh, "offset_m", 0.0) or 0.0)
        n = _outward(p0, p1, space_polys, off)
        if n is None:
            notes.append(f"shade {sh.id} skipped: cannot tell the outside of wall {w.id}")
            continue
        u = ((p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L)

        def at(s, d, z):
            return (p0[0] + u[0] * s + n[0] * (off + d), p0[1] + u[1] * s + n[1] * (off + d), z)

        D, z = sh.depth_m, sh.z_m
        if fin:
            s0 = sh.along_m
            verts = [
                at(s0, 0, z),
                at(s0, D, z),
                at(s0, D, z + sh.height_m),
                at(s0, 0, z + sh.height_m),
            ]
        else:
            s0, s1 = sh.along_m, sh.along_m + sh.width_m
            verts = [at(s0, 0, z), at(s1, 0, z), at(s1, D, z), at(s0, D, z)]
            # horizontal plate: CCW from above so the normal points up
            area2 = sum(
                verts[i][0] * verts[(i + 1) % 4][1] - verts[(i + 1) % 4][0] * verts[i][1]
                for i in range(4)
            )
            if area2 < 0:
                verts.reverse()
        shades.append(
            BEMShade(
                id=sh.id,
                kind=sh.kind,
                host_wall_id=w.id,
                vertices=verts,
                offset_m=off,
                outward=(float(n[0]), float(n[1])),
            )
        )
    return shades, notes
