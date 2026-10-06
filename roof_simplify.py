"""Roof mode for geometry simplification (roadmap item 2, #616).

``simplify_roof`` merges adjacent roof facets greedily, cheapest first, under
two hard budgets measured against the ORIGINAL roof:

* geometric area (true sloped area), the same budget the wall simplifier
  keeps on the envelope, and
* solar aperture (``solar_aperture.py``, #615), so a merge that changes what
  the sun sees is refused even when the area barely moves.

A pair is a candidate when the facets are on one level, share a plan edge at
least MIN_SHARED_EDGE_M long whose heights agree on both planes, and either
their normals are within ``angle_tol_deg`` (near-coplanar) or the smaller one
is at most ``small_facet_frac`` of the roof area (a dormer or small hip end
absorbed into its host). The merged plane covers the plan union of the two,
with the area-weighted mean normal, through their area-weighted centroid.

The input list is never changed: the result carries new RoofPlanes plus a
report with both deltas, every merge and every refusal. Same input in any
order gives the same output and report.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import List

ANGLE_TOL_DEG = 5.0  # near-coplanar normals
SMALL_FACET_FRAC = 0.05  # facets this small (of the roof) may be absorbed
AREA_TOL = 0.02  # |area change| / original area
APERTURE_TOL = 0.02  # |solar aperture change| / original aperture
MIN_SHARED_EDGE_M = 0.1
EDGE_Z_TOL_M = 0.05  # heights on a shared edge must agree this closely
METHOD = "greedy_min_aperture_change"


@dataclass
class RoofSimplifyResult:
    planes: list
    area_delta_pct: float
    aperture_delta_pct: float
    area_tol: float = AREA_TOL
    aperture_tol: float = APERTURE_TOL
    method: str = METHOD
    merges: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    n_before: int = 0
    n_after: int = 0


def _unit_normal(vertices):
    from roof_geometry import newell_normal

    n = newell_normal(vertices)
    ln = math.sqrt(sum(x * x for x in n))
    if n[2] < 0:
        n = tuple(-x for x in n)
    return tuple(x / ln for x in n)


def _plane(rp):
    """(unit upward normal, d) with n . p = d."""
    n = _unit_normal(rp.vertices_m)
    v = rp.vertices_m
    d = sum(n[k] * sum(p[k] for p in v) / len(v) for k in range(3))
    return n, d


def _z_at(plane, x, y):
    n, d = plane
    return (d - n[0] * x - n[1] * y) / n[2]


def _plan(rp):
    from shapely.geometry import Polygon

    return Polygon([(v[0], v[1]) for v in rp.vertices_m]).buffer(0)


def _angle_deg(n1, n2):
    c = max(-1.0, min(1.0, sum(a * b for a, b in zip(n1, n2))))
    return math.degrees(math.acos(c))


def _shared_edge_ok(a, b, pa, pb):
    """Total length of shared plan edge on which both planes agree in height."""
    shared = pa.boundary.intersection(pb.boundary)
    segs = []
    for g in getattr(shared, "geoms", [shared]):
        if g.geom_type == "LineString":
            segs.append(g)
        elif g.geom_type == "MultiLineString":
            segs.extend(g.geoms)
    pla, plb = _plane(a), _plane(b)
    ok = 0.0
    for s in segs:
        coords = list(s.coords)
        for p, q in zip(coords, coords[1:]):
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            if abs(_z_at(pla, mx, my) - _z_at(plb, mx, my)) <= EDGE_Z_TOL_M:
                ok += math.dist(p, q)
    return ok


def _merge(a, b, pid):
    """One RoofPlane over the plan union of a and b, or (None, reason)."""
    from shapely.ops import unary_union

    from roof_geometry import RoofGeometryError, roof_plane

    pa, pb = _plan(a), _plan(b)
    u = unary_union([pa, pb]).buffer(0)
    if u.geom_type != "Polygon":
        return None, "plan union is not one polygon"
    if sum(type(u)(r).area for r in u.interiors) > 1e-6:
        return None, "merge would leave a hole"
    na, nb = _unit_normal(a.vertices_m), _unit_normal(b.vertices_m)
    wa, wb = a.area_m2, b.area_m2
    n = tuple((na[k] * wa + nb[k] * wb) / (wa + wb) for k in range(3))
    ln = math.sqrt(sum(x * x for x in n))
    n = tuple(x / ln for x in n)
    ca, cb = pa.centroid, pb.centroid
    za, zb = _z_at(_plane(a), ca.x, ca.y), _z_at(_plane(b), cb.x, cb.y)
    c = (
        (ca.x * wa + cb.x * wb) / (wa + wb),
        (ca.y * wa + cb.y * wb) / (wa + wb),
        (za * wa + zb * wb) / (wa + wb),
    )
    d = sum(n[k] * c[k] for k in range(3))
    ring = u.exterior.simplify(1e-6)
    verts = [(x, y, _z_at((n, d), x, y)) for x, y in list(ring.coords)[:-1]]
    try:
        merged = roof_plane(
            pid, verts, level_id=a.level_id, host_global_id=a.host_global_id or b.host_global_id
        )
    except RoofGeometryError as e:
        return None, f"merged facet rejected: {e}"
    merged.provenance = copy.deepcopy(a.provenance)
    return merged, ""


def _pct(new, old):
    return (new - old) / old if old else 0.0


def simplify_roof(
    planes,
    latitude_deg: float,
    angle_tol_deg: float = ANGLE_TOL_DEG,
    small_facet_frac: float = SMALL_FACET_FRAC,
    area_tol: float = AREA_TOL,
    aperture_tol: float = APERTURE_TOL,
) -> RoofSimplifyResult:
    """Simplify roof planes under area and solar-aperture budgets (#616)."""
    from solar_aperture import solar_aperture

    work: List = sorted((copy.deepcopy(p) for p in planes), key=lambda p: p.id)
    n0 = len(work)
    area0 = sum(p.area_m2 for p in work)
    ap0 = solar_aperture(work, latitude_deg).total
    members = {p.id: [p.id] for p in work}
    merges, skipped, refused = [], [], set()
    while True:
        total = sum(p.area_m2 for p in work)
        cands = []
        for i, a in enumerate(work):
            for b in work[i + 1 :]:
                if a.level_id != b.level_id or (a.id, b.id) in refused:
                    continue
                pa, pb = _plan(a), _plan(b)
                if _shared_edge_ok(a, b, pa, pb) < MIN_SHARED_EDGE_M:
                    continue
                ang = _angle_deg(_unit_normal(a.vertices_m), _unit_normal(b.vertices_m))
                small = min(a.area_m2, b.area_m2) <= small_facet_frac * total
                if ang > angle_tol_deg and not small:
                    continue
                host, other = (a, b) if (a.area_m2, b.id) >= (b.area_m2, a.id) else (b, a)
                merged, why = _merge(host, other, host.id)
                key = (a.id, b.id)
                if merged is None:
                    refused.add(key)
                    skipped.append({"pair": list(key), "reason": why})
                    continue
                trial = [p for p in work if p.id not in key] + [merged]
                da = _pct(sum(p.area_m2 for p in trial), area0)
                dap = _pct(solar_aperture(trial, latitude_deg).total, ap0)
                cands.append(
                    (abs(dap), abs(da), key, host.id, other.id, merged, trial, da, dap, ang)
                )
        if not cands:
            break
        progressed = False
        for c in sorted(cands, key=lambda c: (round(c[0], 12), round(c[1], 12), c[2])):
            _, _, key, hid, oid, merged, trial, da, dap, ang = c
            if abs(da) > area_tol or abs(dap) > aperture_tol:
                refused.add(key)
                which = "area" if abs(da) > area_tol else "solar aperture"
                skipped.append(
                    {
                        "pair": list(key),
                        "reason": f"{which} budget: area {da * 100:+.3f}%, aperture {dap * 100:+.3f}%",
                    }
                )
                continue
            members[hid] = sorted(members[hid] + members.pop(oid))
            merges.append(
                {
                    "into": hid,
                    "from": list(members[hid]),
                    "angle_deg": round(ang, 4),
                    "area_delta_pct": round(da * 100, 4),
                    "aperture_delta_pct": round(dap * 100, 4),
                }
            )
            work = sorted(trial, key=lambda p: p.id)
            progressed = True
            break
        if not progressed:
            break
    from building_model import Provenance

    for p in work:
        if len(members[p.id]) > 1:
            base = p.provenance
            p.provenance = Provenance(
                sheet_id=getattr(base, "sheet_id", ""),
                revision=getattr(base, "revision", 0),
                method="roof_simplify:merge",
                confidence=min(0.9, getattr(base, "confidence", 0.9) or 0.9),
                note="merged from " + ", ".join(members[p.id]),
            )
    return RoofSimplifyResult(
        planes=work,
        area_delta_pct=round(_pct(sum(p.area_m2 for p in work), area0) * 100, 4),
        aperture_delta_pct=round(_pct(solar_aperture(work, latitude_deg).total, ap0) * 100, 4),
        area_tol=area_tol,
        aperture_tol=aperture_tol,
        merges=merges,
        skipped=sorted(skipped, key=lambda s: (s["pair"], s["reason"])),
        n_before=n0,
        n_after=len(work),
    )


def roof_simplify_report(res: RoofSimplifyResult) -> dict:
    """Plain-dict report, the roof counterpart of ``simplify_report``."""
    return {
        "method": res.method,
        "n_planes_before": res.n_before,
        "n_planes_after": res.n_after,
        "area_delta_pct": res.area_delta_pct,
        "aperture_delta_pct": res.aperture_delta_pct,
        "area_tol_pct": round(res.area_tol * 100, 4),
        "aperture_tol_pct": round(res.aperture_tol * 100, 4),
        "merges": res.merges,
        "skipped": res.skipped,
        "planes": [
            {
                "id": p.id,
                "tilt_deg": round(p.tilt_deg, 4),
                "azimuth_deg": None if p.azimuth_deg is None else round(p.azimuth_deg, 4),
                "area_m2": round(p.area_m2, 6),
            }
            for p in res.planes
        ],
    }


def apply_roof_simplification(model, latitude_deg=None, **kw) -> RoofSimplifyResult:
    """Simplify ``model.roof_planes`` in the model (#617).

    The planes as they were go to ``model.source_roof_planes`` (only the
    first time, so repeated runs still compare against the true source) and
    the simplified set replaces ``model.roof_planes``; the
    ``roof_solar_aperture`` check then compares the two. Latitude from the
    caller, else ``model.site_latitude_deg``; raises without either.
    """
    lat = latitude_deg if latitude_deg is not None else getattr(model, "site_latitude_deg", None)
    if lat is None:
        raise ValueError("no latitude: pass latitude_deg or set model.site_latitude_deg")
    res = simplify_roof(model.roof_planes, lat, **kw)
    if not model.source_roof_planes:
        model.source_roof_planes = copy.deepcopy(model.roof_planes)
    model.roof_planes = res.planes
    return res
