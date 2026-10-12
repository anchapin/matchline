"""Split IFC wall segments at junctions and join flush in-line continuations (#576).

One IfcWall often runs past several rooms, so its single envelope segment
cannot say which room is behind which part of it, and a wall crossing
another mid-span (an X) leaves both runs unbroken where room faces meet.
After centreline moves (#579), linings (#577) and end joins (#575):

* **In-line continuations.** A thinner wall continuing a thicker one flush
  with one face leaves its end beside the other's end, offset sideways by
  half the thickness difference, where no L or T join can close it. That
  end moves sideways onto the other wall's end, so the two read as one
  continuous run. Only the thinner wall moves; equal thicknesses never do.
  Openings are held on spaces in absolute coordinates, so
  nothing else moves.
* **Junction splits.** Where two segments cross mid-span (X), both split at
  the crossing; where one segment's end lands mid-span on another (T), the
  through segment splits there. Each piece keeps its wall's GlobalId,
  construction and provenance, so facade classification can name the one
  space behind each piece. This is bookkeeping on the envelope records; the
  BimElement inventory (source geometry) is untouched.

Ambiguous cases stay as they are with a note: an in-line end with two
equally near candidates, and a wall with unknown thickness (no in-line join;
junction margins then use no thickness).

Approach and tolerances follow the Pascal editor IFC converter
(packages/ifc-converter/src/wall-joins.ts, joinInLineEnds and
splitCrossingWalls, commit 67f8041), MIT licensed, Copyright (c) 2026 Pascal
Group Inc. This is a port of the idea onto matchline's envelope records, not
of the code. Unlike Pascal, which splits only the thinner wall of an X for
its room faces, both walls split here, and T junctions split the through
wall, because the purpose is per-space wall attribution.
"""

from __future__ import annotations

import dataclasses
import math

from ifc_wall_joins import ENVELOPE_METHOD, MIN_JOIN_SINE, _cross, _gid, _level

INLINE_PARALLEL_SINE = 0.03  # near-parallel: within ~1.7 degrees
INLINE_LATERAL_EXTRA_M = 0.01  # beyond the thicker wall's half thickness
INLINE_MAX_GAP_M = 0.1  # along-axis gap between the two ends
INLINE_MAX_MOVE_FRAC = 0.1  # an end moves at most 10% of its wall's length
JUNCTION_MARGIN_M = 0.05  # beyond the thicker wall's half thickness, from each end
_ON_LINE_M = 1e-3  # an end on a centreline (after joins it sits on it exactly)
_SAME_POINT_M = 1e-4
_TIE_M = 1e-4


def _frame(w):
    (x0, y0), (x1, y1) = w.from_m, w.to_m
    L = math.hypot(x1 - x0, y1 - y0)
    if L <= 1e-9:
        return None
    return (x0, y0), ((x1 - x0) / L, (y1 - y0) / L), L


def _ifc_segments(envelope):
    """Tier 0 IFC segments with geometry, grouped by level."""
    by_level = {}
    for w in envelope:
        if w.provenance is None or w.provenance.method != ENVELOPE_METHOD:
            continue
        if not _gid(w) or len(w.from_m) < 2 or len(w.to_m) < 2 or _frame(w) is None:
            continue
        by_level.setdefault(_level(w), []).append(w)
    return by_level


def _note(w, text):
    if w.provenance is not None:
        w.provenance.note += f"; {text}"


def _resize(w):
    L = math.dist(w.from_m, w.to_m)
    w.length_m = round(L, 4)
    if w.height_m:
        w.area_m2 = round(L * w.height_m, 4)


def _on_some_centreline(p, self_w, segs):
    for o in segs:
        if o is self_w:
            continue
        fr = _frame(o)
        if fr is None:
            continue
        (qx, qy), (dx, dy), L = fr
        ox, oy = p[0] - qx, p[1] - qy
        along = ox * dx + oy * dy
        if abs(_cross(ox, oy, dx, dy)) <= _ON_LINE_M and -_ON_LINE_M <= along <= L + _ON_LINE_M:
            return True
    return False


def join_inline_ends(envelope, thickness_by_gid):
    """Move flush in-line continuation ends onto the neighbour's end. Returns counts."""
    counts = {"joined": 0, "ambiguous": 0}
    for segs in _ifc_segments(envelope).values():
        for w in segs:
            t_w = thickness_by_gid.get(_gid(w))
            if not t_w:
                continue
            fr = _frame(w)
            if fr is None:
                continue
            _, (ux, uy), w_len = fr
            for at_end in (False, True):
                p = tuple(w.to_m if at_end else w.from_m)
                if _on_some_centreline(p, w, segs):
                    continue
                cands = []
                for o in segs:
                    if o is w:
                        continue
                    t_o = thickness_by_gid.get(_gid(o))
                    ofr = _frame(o)
                    # only the thinner wall's end moves, onto the thicker one
                    if not t_o or ofr is None or t_o <= t_w:
                        continue
                    (qx, qy), (dx, dy), _ = ofr
                    if abs(_cross(ux, uy, dx, dy)) > INLINE_PARALLEL_SINE:
                        continue
                    lateral = abs(_cross(p[0] - qx, p[1] - qy, dx, dy))
                    if lateral > max(t_w, t_o) / 2 + INLINE_LATERAL_EXTRA_M:
                        continue
                    for end in (tuple(o.from_m), tuple(o.to_m)):
                        dist = math.dist(end, p)
                        gap = abs((end[0] - p[0]) * dx + (end[1] - p[1]) * dy)
                        if gap > INLINE_MAX_GAP_M or dist < _SAME_POINT_M:
                            continue
                        if dist > w_len * INLINE_MAX_MOVE_FRAC:
                            continue
                        cands.append((dist, end, _gid(o)))
                if not cands:
                    continue
                cands.sort(key=lambda c: (c[0], c[1]))
                if (
                    len(cands) > 1
                    and cands[1][0] - cands[0][0] <= _TIE_M
                    and cands[1][1] != cands[0][1]
                ):
                    counts["ambiguous"] += 1
                    _note(w, "in-line end left as-is: two equally near continuations (#576)")
                    continue
                dist, end, ogid = cands[0]
                if at_end:
                    w.to_m = [end[0], end[1]]
                else:
                    w.from_m = [end[0], end[1]]
                _resize(w)
                _note(w, f"end moved {dist:.3f} m sideways onto in-line wall {ogid} (#576)")
                counts["joined"] += 1
    return counts


def _split_points(w, segs, thickness_by_gid):
    """Distances along ``w`` where an X crossing or a T end splits it."""
    fr = _frame(w)
    (ax, ay), (ux, uy), a_len = fr
    t_a = thickness_by_gid.get(_gid(w)) or 0.0
    pts = []
    for o in segs:
        if o is w:
            continue
        ofr = _frame(o)
        if ofr is None:
            continue
        (bx, by), (dx, dy), b_len = ofr
        t_b = thickness_by_gid.get(_gid(o)) or 0.0
        margin = max(t_a, t_b) / 2 + JUNCTION_MARGIN_M
        denom = _cross(ux, uy, dx, dy)
        if abs(denom) >= MIN_JOIN_SINE:
            # X: the two centrelines cross inside both runs
            along_a = _cross(bx - ax, by - ay, dx, dy) / denom
            along_b = _cross(bx - ax, by - ay, ux, uy) / denom
            if margin < along_a < a_len - margin and margin < along_b < b_len - margin:
                pts.append((along_a, _gid(o), "crossing"))
                continue
        # T: one of the other segment's ends lands mid-span on w
        for e in (o.from_m, o.to_m):
            ox, oy = e[0] - ax, e[1] - ay
            along = ox * ux + oy * uy
            if abs(_cross(ox, oy, ux, uy)) <= _ON_LINE_M and margin < along < a_len - margin:
                pts.append((along, _gid(o), "T junction"))
    pts.sort()
    out = []
    for s, g, kind in pts:
        if out and s - out[-1][0] <= _ON_LINE_M:
            continue
        out.append((s, g, kind))
    return out


def split_at_junctions(envelope, thickness_by_gid):
    """Split segments at X crossings and T ends, in place. Returns the number of splits."""
    by_level = _ifc_segments(envelope)
    plan = {}
    for segs in by_level.values():
        for w in segs:
            pts = _split_points(w, segs, thickness_by_gid)
            if pts:
                plan[id(w)] = pts
    if not plan:
        return 0
    n_splits = 0
    out = []
    for w in envelope:
        pts = plan.get(id(w))
        if not pts:
            out.append(w)
            continue
        (ax, ay), (ux, uy), a_len = _frame(w)
        cuts = [0.0] + [s for s, _, _ in pts] + [a_len]
        why = ", ".join(f"{kind} with {g} at {s:.3f} m" for s, g, kind in pts)
        for k in range(len(cuts) - 1):
            s0, s1 = cuts[k], cuts[k + 1]
            prov = dataclasses.replace(w.provenance) if w.provenance is not None else None
            piece = dataclasses.replace(
                w,
                id=f"{w.id}.{k + 1}",
                from_m=w.from_m if k == 0 else [round(ax + ux * s0, 6), round(ay + uy * s0, 6)],
                to_m=w.to_m
                if k == len(cuts) - 2
                else [round(ax + ux * s1, 6), round(ay + uy * s1, 6)],
                provenance=prov,
            )
            _resize(piece)
            _note(piece, f"piece {k + 1} of {len(cuts) - 1}, split at {why} (#576)")
            out.append(piece)
        n_splits += len(pts)
    envelope[:] = out
    return n_splits
