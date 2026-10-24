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

Scale (#1265)
-------------
The first implementation scanned every pair of planes on every pass, rebuilt
both plan polygons per pair and recomputed the whole scan after each single
merge, so a 385-plane roof took minutes per pass and never converged. Three
changes keep the same semantics but make the work proportional to the roof:

* **Plan cache** -- ``_PlaneCache`` computes the plan polygon, the plane
  equation and the unit normal of a facet once per run and reuses them
  across candidate pairs and across passes. Only a freshly merged facet
  invalidates an entry (it is a new object under the host's id).
* **Spatial index** -- candidate pairs come from a shapely ``STRtree`` over
  the plan polygons, so only facets whose outlines actually touch are
  considered; the boundary intersection in ``_shared_edge_ok`` runs on
  neighbours instead of on every pair.
* **Batched merges** -- one pass accepts several *disjoint* merges instead of
  exactly one, so a pass costs one scan instead of one scan per merge.

Batching order
--------------
Candidates of a pass are sorted by

    (abs(aperture change), abs(area change), (id_a, id_b))

i.e. cheapest first, ties broken by the sorted pair of plane ids. The sort
key is rounded to 12 decimals so the order never depends on floating point
noise. The sorted list is walked in that order and a candidate is accepted
when

* neither of its two planes has already been merged in this pass (no plane
  takes part in two merges of the same pass), and
* the area and aperture budgets still hold for the working set *with the
  merges already accepted in this pass applied*.

Accepted merges therefore never interact: they touch disjoint planes, so
each one sees the same geometry the next pass would have seen, and the
budget of each is checked against the state it really lands in. The order of
acceptance is a pure function of the input geometry (never of the order the
planes were handed in), which keeps "same input in any order, same output".

Refusal memory (#1269)
----------------------
A pair whose merge cannot be built (union not one polygon, merge would
leave a hole, merged facet rejected) is remembered by plane *identity*,
not by plane id: a merged facet takes over its host's id, so an id-keyed
refusal would wrongly block the new facet's candidates. A pair refused
for budget is not remembered at all: its merge is cached and the pair is
re-offered on every later pass against the state then current, and it is
reported in ``skipped`` once, not once per pass.
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
    stats: dict = field(default_factory=dict)


def _unit_normal(vertices):
    from geometry.roof_geometry import newell_normal

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


class _PlaneCache:
    """Per-facet geometry computed once per simplify run (#1265).

    The plan polygon, the plane equation, the unit normal and the solar
    contribution of a facet do not change while the facet sits in the
    working set, so they are computed once and reused for every candidate
    pair and every pass. A merged facet is a *new* object that takes over
    its host's id, so the identity check in ``_get`` recomputes the entry
    for it and nothing else is invalidated.
    """

    __slots__ = ("_plan", "_plane", "_normal", "_aperture", "computations", "plans")

    def __init__(self):
        self._plan = {}
        self._plane = {}
        self._normal = {}
        self._aperture = {}
        self.computations = 0
        self.plans = 0

    def _get(self, store, rp, make):
        hit = store.get(rp.id)
        if hit is not None and hit[0] is rp:
            return hit[1]
        val = make(rp)
        self.computations += 1
        store[rp.id] = (rp, val)
        return val

    def plan(self, rp):
        def make(p):
            self.plans += 1
            return _plan(p)

        return self._get(self._plan, rp, make)

    def plane(self, rp):
        return self._get(self._plane, rp, _plane)

    def normal(self, rp):
        return self._get(self._normal, rp, lambda p: _unit_normal(p.vertices_m))

    def aperture(self, rp, suns):
        """This facet's share of the solar aperture (see solar_aperture)."""
        from solar_aperture import cos_sum, plane_normal

        def make(p):
            a = float(p.area_m2 or 0.0)
            return a * cos_sum(plane_normal(p.tilt_deg, p.azimuth_deg), suns)

        return self._get(self._aperture, rp, make)

    def drop(self, rp):
        """Forget a facet that is leaving the working set."""
        for store in (self._plan, self._plane, self._normal, self._aperture):
            store.pop(rp.id, None)


def _shared_edge_ok(a, b, pa, pb, cache=None):
    """Total length of shared plan edge on which both planes agree in height.

    Both facets are planar, so the height difference along a shared segment
    is linear and its extreme value sits at an endpoint: the heights must
    agree at *both* ends of every segment, not only at its midpoint (#1271).
    """
    shared = pa.boundary.intersection(pb.boundary)
    segs = []
    for g in getattr(shared, "geoms", [shared]):
        if g.geom_type == "LineString":
            segs.append(g)
        elif g.geom_type == "MultiLineString":
            segs.extend(g.geoms)
    if cache is not None:
        pla, plb = cache.plane(a), cache.plane(b)
    else:
        pla, plb = _plane(a), _plane(b)
    ok = 0.0
    for s in segs:
        coords = list(s.coords)
        for p, q in zip(coords, coords[1:]):
            # z is linear along a segment of a planar facet, so the height
            # difference between the two planes is linear too and its
            # largest magnitude sits at an endpoint.  Probing the midpoint
            # alone therefore bounds nothing: both ends could disagree by
            # ~2x EDGE_Z_TOL_M while the midpoint agrees (#1271).  Probe
            # both endpoints (and the midpoint for good measure).
            mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
            probes = ((p[0], p[1]), (mx, my), (q[0], q[1]))
            if all(abs(_z_at(pla, x, y) - _z_at(plb, x, y)) <= EDGE_Z_TOL_M for x, y in probes):
                ok += math.dist(p, q)
    return ok


def _merge(a, b, pid, pa=None, pb=None, cache=None):
    """One RoofPlane over the plan union of a and b, or (None, reason)."""
    from shapely.ops import unary_union

    from geometry.roof_geometry import RoofGeometryError, roof_plane

    if pa is None:
        pa = _plan(a)
    if pb is None:
        pb = _plan(b)
    u = unary_union([pa, pb]).buffer(0)
    if u.geom_type != "Polygon":
        return None, "plan union is not one polygon"
    if sum(type(u)(r).area for r in u.interiors) > 1e-6:
        return None, "merge would leave a hole"
    if cache is not None:
        na, nb = cache.normal(a), cache.normal(b)
        pla, plb = cache.plane(a), cache.plane(b)
    else:
        na, nb = _unit_normal(a.vertices_m), _unit_normal(b.vertices_m)
        pla, plb = _plane(a), _plane(b)
    wa, wb = a.area_m2, b.area_m2
    n = tuple((na[k] * wa + nb[k] * wb) / (wa + wb) for k in range(3))
    ln = math.sqrt(sum(x * x for x in n))
    n = tuple(x / ln for x in n)
    ca, cb = pa.centroid, pb.centroid
    za, zb = _z_at(pla, ca.x, ca.y), _z_at(plb, cb.x, cb.y)
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


def _candidate_pairs(work, cache):
    """Pairs of planes whose plan outlines touch, in a deterministic order.

    A spatial index (STRtree) over the plan polygons keeps the scan linear
    in the size of the roof: two facets can only share a plan edge if their
    bounding boxes meet, so only those pairs are looked at. The pairs are
    returned sorted by index, the working set itself being sorted by id.
    """
    geoms = [cache.plan(p) for p in work]
    pairs = []
    if len(geoms) > 1:
        try:
            from shapely.strtree import STRtree

            tree = STRtree(geoms)
            for i, g in enumerate(geoms):
                for j in tree.query(g):
                    j = int(j)
                    if j > i:
                        pairs.append((i, j))
        except ImportError:  # pragma: no cover - shapely always ships STRtree
            for i, gi in enumerate(geoms):
                for j in range(i + 1, len(geoms)):
                    if gi.envelope.intersects(geoms[j].envelope):
                        pairs.append((i, j))
    pairs.sort()
    return [(work[i], work[j]) for i, j in pairs]


def simplify_roof(
    planes,
    latitude_deg: float,
    angle_tol_deg: float = ANGLE_TOL_DEG,
    small_facet_frac: float = SMALL_FACET_FRAC,
    area_tol: float = AREA_TOL,
    aperture_tol: float = APERTURE_TOL,
) -> RoofSimplifyResult:
    """Simplify roof planes under area and solar-aperture budgets (#616).

    Merges are taken cheapest first (smallest absolute aperture change, then
    smallest absolute area change, then by plane id) and several disjoint
    merges are accepted per pass; see the module docstring for the exact
    order and why it is deterministic (#1265).
    """
    from solar_aperture import solar_aperture, sun_vectors

    work: List = sorted((copy.deepcopy(p) for p in planes), key=lambda p: p.id)
    n0 = len(work)
    area0 = sum(p.area_m2 for p in work)
    ap0 = solar_aperture(work, latitude_deg).total
    members = {p.id: [p.id] for p in work}
    merges, skipped = [], []
    # #1269: refusal memory keyed by plane identity (never by plane id, which
    # a merged facet inherits), with strong refs so identity stays stable.
    build_refused: dict = {}
    merged_cache: dict = {}
    budget_reported: set = set()
    refs: dict = {}
    cache = _PlaneCache()
    suns = sun_vectors(latitude_deg)
    stats = {"passes": 0, "pair_scans": 0, "plan_computations": 0, "geometry_computations": 0}
    while True:
        total = sum(p.area_m2 for p in work)
        work_area = total
        work_ap = sum(cache.aperture(p, suns) for p in work)
        cands = []
        for a, b in _candidate_pairs(work, cache):
            stats["pair_scans"] += 1
            key = (a.id, b.id)
            ikey = (id(a), id(b))
            refs[ikey[0]] = a
            refs[ikey[1]] = b
            if a.level_id != b.level_id or ikey in build_refused:
                continue
            pa, pb = cache.plan(a), cache.plan(b)
            if _shared_edge_ok(a, b, pa, pb, cache) < MIN_SHARED_EDGE_M:
                continue
            ang = _angle_deg(cache.normal(a), cache.normal(b))
            small = min(a.area_m2, b.area_m2) <= small_facet_frac * total
            if ang > angle_tol_deg and not small:
                continue
            host, other = (a, b) if (a.area_m2, b.id) >= (b.area_m2, a.id) else (b, a)
            merged = merged_cache.get(ikey)
            if merged is None:
                merged, why = _merge(
                    host, other, host.id, cache.plan(host), cache.plan(other), cache
                )
                if merged is None:
                    build_refused[ikey] = why
                    skipped.append({"pair": list(key), "reason": why})
                    continue
                merged_cache[ikey] = merged
            cands.append(
                {
                    "key": key,
                    "ikey": ikey,
                    "host": host,
                    "other": other,
                    "merged": merged,
                    "ang": ang,
                    "dap": abs(
                        _pct(
                            work_ap
                            - cache.aperture(a, suns)
                            - cache.aperture(b, suns)
                            + cache.aperture(merged, suns),
                            ap0,
                        )
                    ),
                    "da": abs(_pct(work_area - a.area_m2 - b.area_m2 + merged.area_m2, area0)),
                }
            )
        if not cands:
            break
        # cheapest first; ties by the sorted pair of ids -> input order never
        # matters
        cands.sort(key=lambda c: (round(c["dap"], 12), round(c["da"], 12), c["key"]))
        stats["passes"] += 1
        touched = set()
        accepted = []
        for c in cands:
            key = c["key"]
            ikey = c["ikey"]
            if key[0] in touched or key[1] in touched:
                continue  # a plane takes part in at most one merge per pass
            host, other, merged = c["host"], c["other"], c["merged"]
            # budget against the working set as it stands now, i.e. with the
            # merges already accepted in this pass applied
            da = _pct(work_area - host.area_m2 - other.area_m2 + merged.area_m2, area0)
            dap = _pct(
                work_ap
                - cache.aperture(host, suns)
                - cache.aperture(other, suns)
                + cache.aperture(merged, suns),
                ap0,
            )
            if abs(da) > area_tol or abs(dap) > aperture_tol:
                # #1269: budget refusals are not remembered, so the pair is
                # re-offered on later passes against the state then current;
                # report each pair once, not once per pass.
                if ikey not in budget_reported:
                    budget_reported.add(ikey)
                    which = "area" if abs(da) > area_tol else "solar aperture"
                    skipped.append(
                        {
                            "pair": list(key),
                            "reason": (
                                f"{which} budget: area {da * 100:+.3f}%, aperture {dap * 100:+.3f}%"
                            ),
                        }
                    )
                continue
            touched.update(key)
            accepted.append((c, da, dap))
            work_area = work_area - host.area_m2 - other.area_m2 + merged.area_m2
            work_ap = (
                work_ap
                - cache.aperture(host, suns)
                - cache.aperture(other, suns)
                + cache.aperture(merged, suns)
            )
        if not accepted:
            break
        removed = set()
        for c, da, dap in accepted:
            key, host, other, merged, ang = (
                c["key"],
                c["host"],
                c["other"],
                c["merged"],
                c["ang"],
            )
            members[host.id] = sorted(members[host.id] + members.pop(other.id))
            merges.append(
                {
                    "into": host.id,
                    "from": list(members[host.id]),
                    "angle_deg": round(ang, 4),
                    "area_delta_pct": round(da * 100, 4),
                    "aperture_delta_pct": round(dap * 100, 4),
                }
            )
            removed.update(key)
            cache.drop(host)
            cache.drop(other)
        work = sorted(
            [p for p in work if p.id not in removed] + [c[0]["merged"] for c in accepted],
            key=lambda p: p.id,
        )
    stats["plan_computations"] = cache.plans
    stats["geometry_computations"] = cache.computations
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
        stats=stats,
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
        "stats": dict(res.stats),
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
