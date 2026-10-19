"""Join IFC wall ends onto neighbour centrelines (#575).

Revit and most IFC exporters stop a wall axis at the neighbour's face, or run
it on to the far face, rather than ending it on the neighbour's centreline.
matchline's IFC envelope segments are wall centrelines, so a face-stopped end
leaves exterior wall length and area per facade off by up to one wall
thickness per corner, and leaves wall loops open (#581).

Each straight wall end moves along its own axis onto the centreline of the
neighbour it clearly meets (L and T joins). "Clearly" means: the walls are not
near-parallel, the end lies within the neighbour's half-thickness (measured
along the moving wall) plus a small gap, and the meeting point falls on the
neighbour's run. An end with no such neighbour, or with two equally near ones
that disagree, stays where it is. Every moved end is recorded in provenance.

Approach and tolerances follow the Pascal editor IFC converter
(packages/ifc-converter/src/wall-joins.ts, commit 67f8041), MIT licensed,
Copyright (c) 2026 Pascal Group Inc. This is a port of the idea onto
matchline's envelope records, not of the code.
"""

from __future__ import annotations

import math

GAP_TOLERANCE_M = 0.05  # beyond the neighbour's half-thickness
MAX_CONNECTED_GAP_M = 0.6  # reach when IfcRelConnectsPathElements links the pair
MIN_JOIN_SINE = math.sin(math.radians(15.0))  # near-parallel walls never join end-to-side
MIN_WALL_LENGTH_M = 0.08  # a join may never shrink a wall below this
_ON_LINE_M = 1e-4  # already on the centreline
_TIE_M = 1e-4
_SAME_POINT_M = 1e-3

ENVELOPE_METHOD = "ifc_import:tier0:envelope"


def _gid(ew):
    note = (ew.provenance.note or "") if ew.provenance is not None else ""
    tok = note.split(" ", 1)[0]
    return tok[len("GlobalId=") :] if tok.startswith("GlobalId=") else ""


def _level(ew):
    return ew.id.split("-EW")[0] if "-EW" in ew.id else ""


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def _end_target(end, out, w_len, w_half, others):
    """Best (s, point, neighbour_gid) for one wall end, None, or "ambiguous".

    ``end`` is the end point, ``out`` the outward unit direction along the
    wall, ``w_half`` the moving wall's half thickness. ``others`` holds
    (gid, q0, dir, length, half_thickness, connected) for each neighbour.
    """
    ex, ey = end
    ox, oy = out
    cands = []
    for ngid, (qx, qy), (dx, dy), n_len, n_half, connected in others:
        denom = _cross(ox, oy, dx, dy)
        sin_t = abs(denom)
        if sin_t < MIN_JOIN_SINE:
            continue
        s = _cross(qx - ex, qy - ey, dx, dy) / denom  # along ``out`` to the centreline
        reach = n_half / sin_t + GAP_TOLERANCE_M
        if connected:
            reach = max(reach, MAX_CONNECTED_GAP_M)
        if abs(s) > reach:
            continue
        px, py = ex + ox * s, ey + oy * s
        u = (px - qx) * dx + (py - qy) * dy  # along the neighbour's run
        margin = w_half / sin_t + GAP_TOLERANCE_M
        if connected:
            margin = max(margin, MAX_CONNECTED_GAP_M)
        if not (-margin <= u <= n_len + margin):
            continue
        if w_len + s < MIN_WALL_LENGTH_M:
            continue
        cands.append((abs(s), s, (px, py), ngid))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[3]))
    best = cands[0]
    for c in cands[1:]:
        if c[0] - best[0] > _TIE_M:
            break
        if math.dist(c[2], best[2]) > _SAME_POINT_M:
            return "ambiguous"
    return best[1], best[2], best[3]


def join_wall_ends(envelope, thickness_by_gid, connected_pairs=frozenset()):
    """Move IFC envelope wall ends onto neighbour centrelines, in place.

    Only Tier 0 IFC envelope segments take part, per level. Targets are
    computed from the original geometry of every wall before any end moves,
    so the result does not depend on wall order. Returns a summary dict:
    {"moved": ends moved, "ambiguous": ends left because two neighbours tie}.
    """
    walls = [
        w
        for w in envelope
        if w.provenance is not None
        and w.provenance.method == ENVELOPE_METHOD
        and len(w.from_m) >= 2
        and len(w.to_m) >= 2
        and _gid(w)
    ]
    snap = {}
    for w in walls:
        (x0, y0), (x1, y1) = w.from_m, w.to_m
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1e-6:
            continue
        t = thickness_by_gid.get(_gid(w))
        half = 0.5 * t if t and t > 0 and math.isfinite(t) else 0.0
        snap[w.id] = ((x0, y0), ((x1 - x0) / L, (y1 - y0) / L), L, half)

    plans = {}
    counts = {"moved": 0, "ambiguous": 0}
    for w in sorted(walls, key=lambda w: w.id):
        if w.id not in snap:
            continue
        (x0, y0), (dx, dy), L, half = snap[w.id]
        g = _gid(w)
        others = [
            (
                _gid(n),
                snap[n.id][0],
                snap[n.id][1],
                snap[n.id][2],
                snap[n.id][3],
                frozenset((g, _gid(n))) in connected_pairs,
            )
            for n in walls
            if n is not w and n.id in snap and _level(n) == _level(w)
        ]
        ends = {
            "start": ((x0, y0), (-dx, -dy)),
            "end": ((x0 + dx * L, y0 + dy * L), (dx, dy)),
        }
        for which, (pt, out) in ends.items():
            tgt = _end_target(pt, out, L, half, others)
            if tgt == "ambiguous":
                counts["ambiguous"] += 1
                w.provenance.note += f"; {which} not joined: two neighbour centrelines tie"
                continue
            if tgt is None or abs(tgt[0]) < _ON_LINE_M:
                continue
            plans.setdefault(w.id, {})[which] = tgt

    by_id = {w.id: w for w in walls}
    for wid, ends in plans.items():
        w = by_id[wid]
        notes = []
        for which, (s, (px, py), ngid) in sorted(ends.items()):
            p = [round(px, 4), round(py, 4)]
            if which == "start":
                w.from_m = p
            else:
                w.to_m = p
            verb = "extended" if s > 0 else "trimmed"
            notes.append(f"{which} {verb} {abs(s):.3f} m onto centreline of {ngid}")
            counts["moved"] += 1
        L = math.dist(w.from_m, w.to_m)
        w.length_m = round(L, 4)
        if w.height_m:
            w.area_m2 = round(L * w.height_m, 4)
        w.provenance.note += "; " + "; ".join(notes)
    return counts


def connected_pairs_from_ifc(f):
    """GlobalId pairs IfcRelConnectsPathElements says are connected."""
    pairs = set()
    try:
        rels = f.by_type("IfcRelConnectsPathElements")
    except Exception:  # noqa: BLE001 -- schema without the entity
        return frozenset()
    for rel in rels:
        a = getattr(rel, "RelatingElement", None)
        b = getattr(rel, "RelatedElement", None)
        if a is not None and b is not None:
            pairs.add(frozenset((a.GlobalId, b.GlobalId)))
    return frozenset(pairs)
