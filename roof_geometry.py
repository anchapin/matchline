"""Orientation and area of planar roof facets (roadmap item 2, #613).

Canonical frame: plan x east, plan y down the sheet (so north is -y), z up.
Azimuths are compass bearings of the upward-facing normal, degrees clockwise
from north, matching ``SpaceOpening.azimuth_deg`` for skylights.

The normal comes from Newell's method, which is exact for any planar polygon
whatever its winding or vertex count. A facet whose corners are not coplanar
within ``PLANAR_TOL_M`` is rejected: fitting a plane through it would hide a
modelling error behind a made-up tilt.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

PLANAR_TOL_M = 0.005  # max corner distance from the facet plane
FLAT_TILT_DEG = 0.5  # below this a facet is flat and has no azimuth
MIN_AREA_M2 = 1e-6


class RoofGeometryError(ValueError):
    """A facet that cannot be given an honest orientation."""


def _pts(vertices) -> list:
    pts = [tuple(float(c) for c in v) for v in vertices]
    if len(pts) >= 2 and math.dist(pts[0], pts[-1]) < 1e-12:
        pts = pts[:-1]  # closed ring given
    if len(pts) < 3 or any(len(p) != 3 for p in pts):
        raise RoofGeometryError("a roof facet needs at least three [x, y, z] corners")
    return pts


def newell_normal(vertices) -> Tuple[float, float, float]:
    """Un-normalised Newell normal; its length is twice the polygon area."""
    pts = _pts(vertices)
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[(i + 1) % n]
        nx += (y0 - y1) * (z0 + z1)
        ny += (z0 - z1) * (x0 + x1)
        nz += (x0 - x1) * (y0 + y1)
    return nx, ny, nz


def sloped_area(vertices) -> float:
    """True area of a planar facet."""
    return math.sqrt(sum(c * c for c in newell_normal(vertices))) / 2.0


def plan_area(vertices) -> float:
    """Area of the facet projected onto the plan."""
    pts = _pts(vertices)
    a = 0.0
    for i in range(len(pts)):
        x0, y0, _ = pts[i]
        x1, y1, _ = pts[(i + 1) % len(pts)]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def plane_orientation(
    vertices, planar_tol_m: float = PLANAR_TOL_M
) -> Tuple[float, Optional[float], float]:
    """(tilt_deg, azimuth_deg, sloped_area_m2) of one planar facet.

    Winding does not matter: the normal is taken on the upward side. A
    vertical facet (tilt 90) is returned as such; deciding it is a wall and
    not a roof is the caller's call.

    Raises:
        RoofGeometryError: fewer than three corners, zero area, or corners
            off the plane by more than ``planar_tol_m``.
    """
    pts = _pts(vertices)
    nx, ny, nz = newell_normal(pts)
    ln = math.sqrt(nx * nx + ny * ny + nz * nz)
    if ln / 2.0 < MIN_AREA_M2:
        raise RoofGeometryError("degenerate roof facet (zero area)")
    if nz < 0:
        nx, ny, nz = -nx, -ny, -nz
    ux, uy, uz = nx / ln, ny / ln, nz / ln
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    cz = sum(p[2] for p in pts) / len(pts)
    off = max(abs((p[0] - cx) * ux + (p[1] - cy) * uy + (p[2] - cz) * uz) for p in pts)
    if off > planar_tol_m:
        raise RoofGeometryError(
            f"roof facet corners are {off:.3f} m off one plane (tolerance {planar_tol_m} m)"
        )
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, uz))))
    if tilt < FLAT_TILT_DEG:
        return 0.0, None, ln / 2.0
    # north is -y in the canonical frame, east is +x
    az = math.degrees(math.atan2(ux, -uy)) % 360.0
    return tilt, az, ln / 2.0


def roof_plane(
    pid: str,
    vertices: Sequence[Sequence[float]],
    level_id: str = "",
    host_global_id: str = "",
    provenance=None,
):
    """A ``RoofPlane`` with tilt, azimuth and area computed from its corners."""
    from building_model import RoofPlane

    tilt, az, area = plane_orientation(vertices)
    return RoofPlane(
        id=pid,
        level_id=level_id,
        vertices_m=[[float(c) for c in v] for v in _pts(vertices)],
        tilt_deg=tilt,
        azimuth_deg=az,
        area_m2=area,
        host_global_id=host_global_id,
        provenance=provenance,
    )
