"""Geometry simplification for BEM (building energy modeling) handoff.

Problem: drawings describe buildings with far more wall segments than an
energy model needs -- every jog, notch, and over-segmented CAD edge becomes
a heat-transfer surface in EnergyPlus/OpenStudio, and simulation cost scales
with surface count. But envelope area drives conduction/infiltration loads,
so simplification must not move the area.

Pipeline
--------
1. Build the envelope footprint: union of floor-area (Room etc.) polygons ->
   exterior ring of the union. Each ring edge is one wall surface.
2. Simplify the ring with area-budgeted greedy vertex removal (merges
   collinear segments for free, removes small notches/jogs), plus optional
   Douglas-Peucker for over-segmented curved outlines.
3. Hard area constraint: total envelope surface area must stay within
   ``tol`` (default 2%, configurable 1-5%) of the original. Any op that
   would breach the budget is skipped and recorded.

Auditability: every output surface maps back to the source surfaces it
replaces (provenance), and ``simplify_report`` returns counts, area delta,
and the per-surface mapping.

Works on the ``Region`` polygon contract from datasets_adapter.py
(``polygon_px`` vertex lists), but the core functions take plain vertex
lists so they are usable standalone.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np

try:
    from shapely.geometry import LineString, Polygon
    from shapely.ops import unary_union
except ImportError as e:  # pragma: no cover - packaging guarantees shapely
    raise ImportError(
        "shapely is required by geometry_simplify; install the project with "
        "`pip install -e .` (or `pip install matchline`)"
    ) from e


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


def footprint_from_regions(polygons: list[list]) -> list:
    """Union a set of floor polygons -> exterior ring of the building envelope.

    Returns the ring as a list of (x, y) vertices (no duplicated closing
    vertex). Interior holes are dropped: BEM envelope cares about the outer
    boundary. If the union is a MultiPolygon (disjoint wings), the largest
    part's exterior is used.
    """
    polys = []
    for p in polygons:
        p = np.asarray(p, dtype=np.float64)
        if len(p) >= 3:
            polys.append(Polygon(p))
    if not polys:
        return []
    merged = unary_union(polys)
    if merged.geom_type == "MultiPolygon":
        merged = max(merged.geoms, key=lambda g: g.area)
    ring = np.asarray(merged.exterior.coords, dtype=np.float64)
    if len(ring) > 1 and np.allclose(ring[0], ring[-1]):
        ring = ring[:-1]
    return [tuple(v) for v in ring]


def ring_edges(ring) -> list[tuple[tuple, tuple]]:
    """Ordered wall segments of a ring: edge i runs ring[i] -> ring[i+1]."""
    n = len(ring)
    return [(tuple(ring[i]), tuple(ring[(i + 1) % n])) for i in range(n)]


def envelope_area(ring, wall_height: float | None = None) -> float:
    """Total envelope surface area.

    With a wall height: walls (perimeter x height) + roof + floor.
    Without: footprint area only. Units follow the input coordinates.
    """
    poly = Polygon(np.asarray(ring, dtype=np.float64))
    a = poly.area
    if wall_height is None:
        return a
    return 2.0 * a + poly.length * wall_height


def ring_perimeter(ring) -> float:
    return float(Polygon(np.asarray(ring, dtype=np.float64)).length)


# ---------------------------------------------------------------------------
# Simplification primitives
# ---------------------------------------------------------------------------


def _tri_area(a, b, c) -> float:
    """Absolute area change from removing vertex b between neighbors a, c."""
    return 0.5 * abs((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0]))


def _seg_intersects_ring(p, q, ring_pts, skip_a, skip_b) -> bool:
    """Would the new edge p->q cross any existing ring edge?

    ``ring_pts``: dict idx -> point for alive vertices; skip the two edges
    incident to the removed vertex (they share endpoints by construction).
    Touching at shared endpoints is allowed; proper crossings are not.
    """
    new = LineString([p, q])
    ids = list(ring_pts.keys())
    m = len(ids)
    for k in range(m):
        i0, i1 = ids[k], ids[(k + 1) % m]
        if i0 in (skip_a, skip_b) and i1 in (skip_a, skip_b):
            continue  # edge incident to the removed vertex
        e = LineString([ring_pts[i0], ring_pts[i1]])
        if new.crosses(e):
            return True
    return False


def _douglas_peucker(pts: np.ndarray, eps: float) -> np.ndarray:
    """Douglas-Peucker on an open polyline."""
    if len(pts) < 3:
        return pts
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 - i0 < 2:
            continue
        a, b = pts[i0], pts[i1]
        seg = b - a
        L = float(np.hypot(*seg))
        if L == 0:
            d = np.hypot(*(pts[i0 + 1 : i1] - a).T)
        else:
            d = np.abs(np.cross(seg, pts[i0 + 1 : i1] - a)) / L
        rel = np.argmax(d)
        k = rel + i0 + 1
        if d[rel] > eps:
            keep[k] = True
            stack.append((i0, k))
            stack.append((k, i1))
    return pts[keep]


@dataclass
class SimplifyResult:
    ring: list  # simplified ring vertices
    original_count: int  # original surface (edge) count
    simplified_count: int  # simplified surface count
    original_area: float
    simplified_area: float
    area_delta_pct: float
    tol: float = 0.02
    method: str = "greedy_min_area_loss"  # simplification method
    confidence: float = 1.0  # 0..1, based on area_delta vs tol
    provenance: list = field(default_factory=list)
    # provenance[i] = {"surface": i, "from": [orig edge idx...], "note": str}
    skipped: list = field(default_factory=list)
    # skipped entries: {"op": str, "reason": str}
    valid: bool = True


def simplify_ring(
    ring,
    tol: float = 0.02,
    min_feature: float | None = None,
    wall_height: float | None = None,
    dp_eps: float | None = None,
    max_single_step: float = 0.0025,
) -> SimplifyResult:
    """Simplify a footprint ring under a hard envelope-area budget.

    Parameters
    ----------
    ring : [(x, y), ...], closed implicitly (no duplicate end vertex).
    tol : total allowed relative area change (0.02 = 2%). Use 0.01-0.05.
    min_feature / max_single_step : per-removal area cap as a fraction of
        the original envelope area; blocks single large bites even if the
        total budget would allow them. Default 0.25% of area.
    wall_height : if given, the area budget applies to full envelope area
        (walls + roof + floor); else to footprint area.
    dp_eps : Douglas-Peucker epsilon in input units, applied first for
        over-segmented curved outlines. None skips it.

    Method: greedy minimum-area-loss vertex removal on a heap. Collinear
    vertices cost exactly 0, so collinear merging happens naturally; small
    notches cost little and go next. A removal is applied only if it fits
    the per-step cap and the cumulative budget; otherwise it is skipped and
    logged. Self-intersection is guarded per removal.
    """
    ring = [tuple(map(float, v)) for v in ring]
    n0 = len(ring)
    if n0 < 4:
        a = envelope_area(ring, wall_height)
        return SimplifyResult(
            ring=ring,
            original_count=n0,
            simplified_count=n0,
            original_area=a,
            simplified_area=a,
            area_delta_pct=0.0,
            tol=tol,
            method="greedy_min_area_loss",
            confidence=1.0,
            provenance=[
                {"surface": i, "from": [i], "note": "unchanged (too few vertices)"}
                for i in range(n0)
            ],
        )
    orig_area = envelope_area(ring, wall_height)
    if orig_area <= 0:
        raise ValueError("degenerate ring: non-positive area")

    work = [tuple(v) for v in ring]
    orig_idx = list(range(n0))  # work[i] came from original vertex orig_idx[i]
    skipped: list[dict] = []

    # --- Optional Douglas-Peucker pass for over-segmented curves -----------
    if dp_eps:
        pts = np.asarray(work)
        d = np.hypot(*(pts - pts[0]).T)
        cut = int(np.argmax(d[1:])) + 1
        opened = np.vstack([pts[cut:], pts[: cut + 1]])
        opened_idx = orig_idx[cut:] + orig_idx[: cut + 1]
        kept = _douglas_peucker(opened, dp_eps)
        keep_mask = [any(np.allclose(k, o) for k in kept) for o in opened]
        new_work = [tuple(w) for w, m in zip(opened, keep_mask) if m]
        new_idx = [ii for ii, m in zip(opened_idx, keep_mask) if m]
        if len(new_work) >= 3:
            trial = Polygon(np.asarray(new_work))
            if trial.is_valid:
                d_area = abs(envelope_area(new_work, wall_height) - orig_area) / orig_area
                if d_area <= tol:
                    work, orig_idx = new_work, new_idx
                else:
                    skipped.append(
                        {
                            "op": "douglas_peucker",
                            "reason": f"area delta {d_area:.3%} > tol {tol:.1%}",
                        }
                    )
            else:
                skipped.append({"op": "douglas_peucker", "reason": "result invalid"})
        else:
            skipped.append({"op": "douglas_peucker", "reason": "result degenerate"})

    # --- Greedy minimum-area-loss vertex removal ---------------------------
    nv = len(work)
    alive = [True] * nv
    alive_count = nv
    prev = [(i - 1) % nv for i in range(nv)]
    nxt = [(i + 1) % nv for i in range(nv)]
    ver = [0] * nv  # generation counter vs stale heap entries
    removed_area = 0.0  # cumulative absolute area change
    single_cap = (min_feature if min_feature is not None else max_single_step) * orig_area
    over_cap: set[int] = set()  # vertices currently too costly per step
    over_budget: set[int] = set()  # vertices currently too costly for budget

    def push(i):
        a, b, c = work[prev[i]], work[i], work[nxt[i]]
        ver[i] += 1
        heapq.heappush(heap, (_tri_area(a, b, c), ver[i], i))

    heap: list = []
    for i in range(nv):
        push(i)

    while heap and alive_count > 4:
        c, v, i = heapq.heappop(heap)
        if not alive[i] or v != ver[i]:
            continue  # stale entry
        if c > single_cap:
            if i not in over_cap:
                over_cap.add(i)
                skipped.append(
                    {
                        "op": "vertex_removal",
                        "reason": f"vertex {orig_idx[i]}: single-step "
                        f"area {c:.4g} exceeds per-step cap "
                        f"{single_cap:.4g}",
                    }
                )
            continue
        over_cap.discard(i)
        if (removed_area + c) / orig_area > tol:
            if i not in over_budget:
                over_budget.add(i)
                skipped.append(
                    {
                        "op": "vertex_removal",
                        "reason": f"vertex {orig_idx[i]}: removal "
                        f"would breach tol {tol:.1%} "
                        f"(cumulative "
                        f"{(removed_area + c) / orig_area:.3%})",
                    }
                )
            continue
        over_budget.discard(i)
        p, q = prev[i], nxt[i]
        # topology guard: new edge p->q must not cross the ring
        ring_pts = {j: work[j] for j in range(nv) if alive[j]}
        if _seg_intersects_ring(work[p], work[q], ring_pts, p, i):
            skipped.append(
                {
                    "op": "vertex_removal",
                    "reason": f"vertex {orig_idx[i]}: removal would self-intersect",
                }
            )
            over_cap.add(i)  # do not reconsider
            continue
        # apply removal
        alive[i] = False
        alive_count -= 1
        removed_area += c
        nxt[p] = q
        prev[q] = p
        if alive[p]:
            push(p)
        if alive[q]:
            push(q)

    # re-walk the ring in order from the first alive vertex
    start = next(i for i in range(nv) if alive[i])
    order, i = [], start
    while True:
        order.append(i)
        i = nxt[i]
        if i == start:
            break
    new_ring = [work[i] for i in order]
    new_orig = [orig_idx[i] for i in order]

    poly = Polygon(np.asarray(new_ring, dtype=np.float64))
    if not poly.is_valid or len(new_ring) < 3:
        skipped.append({"op": "final", "reason": "simplified ring invalid; returning original"})
        return SimplifyResult(
            ring=ring,
            original_count=n0,
            simplified_count=n0,
            original_area=orig_area,
            simplified_area=orig_area,
            area_delta_pct=0.0,
            tol=tol,
            method="greedy_min_area_loss",
            confidence=0.0,
            provenance=[
                {"surface": i, "from": [i], "note": "rollback: invalid result"} for i in range(n0)
            ],
            skipped=skipped,
            valid=False,
        )

    new_area = envelope_area(new_ring, wall_height)
    delta = (new_area - orig_area) / orig_area

    # Provenance: output surface s spans original vertices
    # new_orig[s] -> new_orig[s+1]; every original edge in between is listed.
    provenance = []
    m = len(new_orig)
    for s in range(m):
        a0, a1 = new_orig[s], new_orig[(s + 1) % m]
        if a1 >= a0:
            src_edges = list(range(a0, a1))
        else:
            src_edges = list(range(a0, n0)) + list(range(0, a1))
        note = "merged %d source segments" % len(src_edges) if len(src_edges) > 1 else "unchanged"
        provenance.append({"surface": s, "from": src_edges, "note": note})

    conf = max(0.0, 1.0 - abs(delta) / tol) if tol > 0 else 1.0
    return SimplifyResult(
        ring=new_ring,
        original_count=n0,
        simplified_count=m,
        original_area=orig_area,
        simplified_area=new_area,
        area_delta_pct=delta * 100.0,
        tol=tol,
        method="greedy_min_area_loss",
        confidence=conf,
        provenance=provenance,
        skipped=skipped,
        valid=True,
    )


def simplify_report(res: SimplifyResult) -> dict:
    """Plain-dict audit report for a SimplifyResult."""
    return {
        "original_surface_count": res.original_count,
        "simplified_surface_count": res.simplified_count,
        "reduction_pct": (100.0 * (res.original_count - res.simplified_count) / res.original_count)
        if res.original_count
        else 0.0,
        "original_area": res.original_area,
        "simplified_area": res.simplified_area,
        "area_delta_pct": res.area_delta_pct,
        "tolerance_pct": res.tol * 100.0,
        "within_tolerance": abs(res.area_delta_pct) <= res.tol * 100.0,
        "method": res.method,
        "confidence": res.confidence,
        "per_surface_mapping": res.provenance,
        "skipped_ops": res.skipped,
        "valid": res.valid,
    }
