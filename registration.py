"""Sheet registration: align discipline sheets into arch-plan coordinates.

The architectural floor plan is the reference frame (canonical meters,
y-down). Every other sheet is registered into it:

  PLAN SHEETS (lighting, mechanical): sheet px -> canonical meters via an
  affine map. In production this comes from title-block scale + image
  alignment (the ifc-overlay least-squares approach); the prototype takes
  (origin_px, px_per_m) from sheet metadata, with a general tie-point
  least-squares fitter available.

  ELEVATIONS: sheet px -> (s_m along the facade, z_m height). Two paths:
    * GRID PATH: shared column-grid labels (A, B, ...) between the plan
      and the elevation give tie points; a 1D affine fit maps elevation
      u_px -> facade meters. High confidence.
    * GEOMETRIC FALLBACK (required by domain input): no grids. The
      facade's reference corner (e.g. west end of the south wall) maps to
      the elevation drawing's wall origin, and title-block scale gives
      px/m. "Room 101 spans 10-30 ft from the east wall" maps to the same
      interval on the elevation. Confidence is penalized vs the grid path
      and links built on it are flagged for review.

  After registration, fixtures/sensors/diffusers land in spaces by
  point-in-polygon in canonical meters (assign_points_to_spaces).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from building_model import Provenance


# ---------------------------------------------------------------------------
# 2D affine: sheet px -> canonical meters
# ---------------------------------------------------------------------------

@dataclass
class Affine2D:
    """x_m = a*px + b*py + tx ; y_m = c*px + d*py + ty."""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, px: float, py: float) -> tuple:
        return (self.a * px + self.b * py + self.tx,
                self.c * px + self.d * py + self.ty)

    @staticmethod
    def from_scale_translate(px_per_m: float, ox_px: float, oy_px: float):
        """Canonical (0,0) sits at sheet px (ox_px, oy_px)."""
        s = 1.0 / px_per_m
        return Affine2D(a=s, d=s, tx=-ox_px * s, ty=-oy_px * s)

    @staticmethod
    def from_tie_points(pts_px, pts_m):
        """Least-squares affine from >= 3 tie points."""
        P = np.asarray(pts_px, dtype=float)
        M = np.asarray(pts_m, dtype=float)
        n = len(P)
        if n < 3:
            raise ValueError("need >= 3 tie points for affine fit")
        A = np.zeros((2 * n, 6))
        A[0::2, 0:3] = np.c_[P[:, 0], P[:, 1], np.ones(n)]
        A[1::2, 3:6] = np.c_[P[:, 0], P[:, 1], np.ones(n)]
        z = M.reshape(-1)
        (a, b, tx, c, d, ty), *_ = np.linalg.lstsq(A, z, rcond=None)
        return Affine2D(a=a, b=b, c=c, d=d, tx=tx, ty=ty)


@dataclass
class PlanRegistration:
    sheet_id: str
    discipline: str
    method: str            # "title_block" | "tie_points"
    confidence: float
    affine: Affine2D
    provenance: Provenance = None

    def to_meters(self, px: float, py: float) -> tuple:
        return self.affine.apply(px, py)


# ---------------------------------------------------------------------------
# Facades (plan side)
# ---------------------------------------------------------------------------

@dataclass
class Facade:
    """One exterior wall run, in canonical meters (y-down)."""
    name: str              # "south" | "north" | "east" | "west"
    ref_corner_m: tuple    # reference corner (west end for south/north,
                           # north end for east/west)
    length_m: float
    fixed_coord_m: float   # the constant plan coordinate (y=D for south)
    axis: str = "x"        # facade runs along x (south/north) or y (east/west)

    def plan_point(self, s_m: float) -> tuple:
        """Canonical (x, y) of facade distance s_m from the ref corner."""
        x0, y0 = self.ref_corner_m
        return (x0 + s_m, y0) if self.axis == "x" else (x0, y0 + s_m)


# ---------------------------------------------------------------------------
# Elevation registration: (u_px, v_px) -> (s_m, z_m)
# ---------------------------------------------------------------------------

GRID_CONFIDENCE = 0.95
GEOMETRIC_CONFIDENCE = 0.65   # penalized: no independent check on the
                              # drawing origin; links built on it go to
                              # the review queue (REVIEW_CONFIDENCE = 0.80)


@dataclass
class FacadeRegistration:
    sheet_id: str
    facade: str
    method: str            # "grid" | "geometric"
    confidence: float
    a_s: float             # s_m = a_s * u_px + b_s
    b_s: float
    a_z: float             # z_m = a_z * v_px + b_z  (z up from grade)
    b_z: float
    grid_labels_used: list = field(default_factory=list)
    provenance: Provenance = None

    def to_facade(self, u_px: float, v_px: float) -> tuple:
        return (self.a_s * u_px + self.b_s, self.a_z * v_px + self.b_z)


def register_elevation_grid(sheet_id: str, facade: Facade,
                            plan_grid_m: dict,
                            elev_bubbles: list,
                            v_ground_px: float, elev_px_per_m: float,
                            revision: int) -> FacadeRegistration:
    """Grid path: shared column-grid labels tie plan <-> elevation.

    plan_grid_m: {label: plan coordinate along the facade axis} (meters).
    elev_bubbles: [{label, u_px}] grid bubbles read off the elevation.
    v_ground_px: elevation px of the ground/floor line (z = 0).
    """
    xs, us = [], []
    used = []
    for bub in elev_bubbles:
        lab = bub["label"]
        if lab in plan_grid_m:
            xs.append(plan_grid_m[lab])
            us.append(bub["u_px"])
            used.append(lab)
    if len(used) < 2:
        raise ValueError(
            f"grid path needs >= 2 shared grid labels, got {used}")
    # 1D affine fit: s_m = a*u_px + b
    U = np.array(us)
    X = np.array(xs)
    A = np.c_[U, np.ones_like(U)]
    (a_s, b_s), *_ = np.linalg.lstsq(A, X, rcond=None)
    resid = float(np.abs((a_s * U + b_s) - X).max())
    conf = GRID_CONFIDENCE if resid < 0.05 else GRID_CONFIDENCE - 0.10
    prov = Provenance(sheet_id=sheet_id, revision=revision,
                      method="grid_registration", confidence=conf,
                      note=f"grid labels {used}, max residual {resid:.3f} m")
    return FacadeRegistration(
        sheet_id=sheet_id, facade=facade.name, method="grid",
        confidence=conf, a_s=float(a_s), b_s=float(b_s),
        a_z=-1.0 / elev_px_per_m, b_z=v_ground_px / elev_px_per_m,
        grid_labels_used=used, provenance=prov)


def register_elevation_geometric(sheet_id: str, facade: Facade,
                                wall_u0_px: float, elev_px_per_m: float,
                                v_ground_px: float,
                                revision: int) -> FacadeRegistration:
    """Geometric fallback: no grids on the elevation.

    The facade reference corner maps to the elevation drawing's wall
    origin (u0_px); title-block scale gives px/m. A room spanning
    [10, 30] ft from the corner on the plan maps to the same interval
    on the elevation. Confidence is penalized: a real drawing's wall
    origin is an assumption, so every link built on this registration
    is flagged for review.
    """
    a_s = 1.0 / elev_px_per_m
    b_s = -wall_u0_px / elev_px_per_m   # s=0 at the wall origin
    prov = Provenance(
        sheet_id=sheet_id, revision=revision, method="geometric_fallback",
        confidence=GEOMETRIC_CONFIDENCE,
        note=(f"facade '{facade.name}' ref corner assumed at wall drawing "
              f"origin u0={wall_u0_px:.0f}px; scale {elev_px_per_m:.1f} px/m "
              f"from title block"))
    return FacadeRegistration(
        sheet_id=sheet_id, facade=facade.name, method="geometric",
        confidence=GEOMETRIC_CONFIDENCE, a_s=a_s, b_s=b_s,
        a_z=-1.0 / elev_px_per_m, b_z=v_ground_px / elev_px_per_m,
        provenance=prov)


# ---------------------------------------------------------------------------
# Point-in-polygon assignment (canonical meters)
# ---------------------------------------------------------------------------

def point_in_polygon(pt, poly) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xinters = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < xinters:
                inside = not inside
        j = i
    return inside


def assign_points_to_spaces(points: list, spaces: list) -> dict:
    """Map each point to a space id by point-in-polygon (canonical meters).

    points: [{id, x_m, y_m}]. Returns {point_id: space_id or None}.
    First enclosing space wins; spaces do not overlap in v1 (union-cleaned
    footprints); a miss returns None (reported, never dropped).
    """
    out = {}
    for p in points:
        hit = None
        for s in spaces:
            if point_in_polygon((p["x_m"], p["y_m"]), s.polygon_m):
                hit = s.id
                break
        out[p["id"]] = hit
    return out


def interval_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Length of overlap between [a0,a1] and [b0,b1]."""
    return max(0.0, min(a1, b1) - max(a0, b0))


def match_interval_to_segments(s0: float, s1: float,
                               segments: list) -> tuple:
    """Match a facade interval [s0,s1] to wall segments [{id, s0, s1, ...}].

    Returns (best_segment, overlap_fraction, ambiguous). Ambiguous when the
    interval spans segments with no clear majority -- those links go to
    the review queue.
    """
    w = max(s1 - s0, 1e-9)
    scored = [(interval_overlap(s0, s1, g["s0"], g["s1"]) / w, g)
              for g in segments]
    scored.sort(key=lambda t: -t[0])
    if not scored or scored[0][0] <= 0:
        return None, 0.0, True
    best_frac, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    ambiguous = best_frac < 0.5 or (best_frac - second) < 0.2
    return best, best_frac, ambiguous
