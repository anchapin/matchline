"""Finished grade from a terrain surface (#641).

The ground is read only from the source: ``BuildingModel.terrain`` holds the
triangles of the IFC site terrain (``IfcSite`` body or ``IfcGeographicElement``
TERRAIN), never a default grade. ``Terrain`` answers the ground height at a
plan point and the ground profile along a wall; a point the triangles do not
cover has no grade, and a wall with any such point has no profile.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from shapely import STRtree
from shapely.geometry import Point, Polygon

GRADE_TOL_M = 0.05  # grade within this of a wall's base or top counts as at it
SAMPLE_M = 0.25  # spacing of ground samples along a wall


class Terrain:
    """Ground surface from triangles ``[[x, y, z], [x, y, z], [x, y, z]]``.

    Plan coordinates are whatever frame the triangles are in; callers query
    in the same frame. Where triangles overlap in plan the highest one is
    the ground (a terrain body's underside never wins).
    """

    def __init__(self, triangles: Sequence[Sequence[Sequence[float]]]):
        self.tris = []
        polys = []
        for t in triangles:
            pts = [(float(p[0]), float(p[1]), float(p[2])) for p in t]
            g = Polygon([(x, y) for x, y, _ in pts])
            if g.area <= 1e-12:
                continue  # vertical or degenerate facet: no plan footprint
            self.tris.append(pts)
            polys.append(g)
        self._polys = polys
        self._tree = STRtree(polys) if polys else None

    def __bool__(self) -> bool:
        return bool(self.tris)

    def at(self, x: float, y: float) -> Optional[float]:
        if self._tree is None:
            return None
        pt = Point(x, y)
        best = None
        for i in self._tree.query(pt):
            if not self._polys[i].buffer(1e-9).contains(pt):
                continue
            (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = self.tris[i]
            d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            a = ((y1 - y2) * (x - x2) + (x2 - x1) * (y - y2)) / d
            b = ((y2 - y0) * (x - x2) + (x0 - x2) * (y - y2)) / d
            z = a * z0 + b * z1 + (1 - a - b) * z2
            best = z if best is None else max(best, z)
        return best

    def profile(self, p0, p1, step: float = SAMPLE_M) -> Optional[List[Tuple[float, float]]]:
        """[(distance from p0, ground z)] along p0 -> p1, or None if not covered."""
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        n = max(1, math.ceil(L / step))
        out = []
        for k in range(n + 1):
            t = k / n
            z = self.at(p0[0] + t * dx, p0[1] + t * dy)
            if z is None:
                return None
            out.append((t * L, z))
        return out


__all__ = ["GRADE_TOL_M", "SAMPLE_M", "Terrain"]
