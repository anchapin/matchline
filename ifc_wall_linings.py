"""Keep IFC lining and hidden walls out of the envelope (#577).

Many IFC exporters write tiles, skirting, rainscreen panels and furring as
their own IfcWall along a real wall's face, or leave a short wall buried
inside a thicker one. matchline mirrors every IfcWall into the BEM envelope,
so each of those adds opaque area a second time, joins into the wall graph and
can pull its own (often meaningless) U-value into the per-facade average.

A wall is a lining of a host wall on the same level when the two are parallel
and any of these hold:

* embedded: the host is thicker and the wall's body lies entirely inside the
  host's body along at least its own length;
* face to face: the wall is shorter than the host, laid against its face (or
  running through its body), over at least 90% of its length;
* cladding: the wall is at most 35 mm thick, the host is more than twice as
  thick, the wall sits on the host's face, and at least half its length is
  covered by such hosts.

Linings leave the envelope (so they drop out of envelope area, facade
classification, U averaging and wall-loop detection); their BimElement stays
in the inventory with ``role="lining"`` and a provenance note naming the host.
Never guessed: a wall with unknown thickness is neither lining nor host, and a
wall that hosts openings is kept in the envelope with a note, because removing
it would drop real glazing or doors.

The three tests and their tolerances are a close port of ``redundantWallIds``
in the Pascal editor IFC converter
(packages/ifc-converter/src/wall-joins.ts, commit 67f8041).

Portions Copyright (c) 2026 Pascal Group Inc., used under the MIT License:
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions: The above copyright
notice and this permission notice shall be included in all copies or
substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS",
WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.
"""

from __future__ import annotations

import math

MAX_CLADDING_THICKNESS_M = 0.035  # thickest wall still read as cladding
PARALLEL_SINE = 0.05  # |sin| between directions below this counts as parallel
EMBED_TOL_M = 0.01  # body-inside-body slack
EMBED_OVERLAP_SLACK_M = 0.05  # embedded wall may poke this far past the host run
FACE_TOL_M = 0.01  # face-to-face contact slack
LINING_OVERLAP_FRAC = 0.9  # face-to-face lining must overlap this much of its length
CLADDING_FACE_TOL_M = 0.02  # cladding may stand this far off the host face
CLADDING_LINED_FRAC = 0.5  # share of a cladding wall's length its hosts must cover

ENVELOPE_METHOD = "ifc_import:tier0:envelope"


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def find_linings(walls):
    """Map lining key -> (host key, kind) for parallel walls on one level.

    ``walls`` is a sequence of (key, level, p0, p1, thickness) with p0/p1 the
    centreline end points in metres. Walls with unknown or non-positive
    thickness, or zero length, take no part. Walls are visited in the given
    order; a wall already found to be a lining never hosts another (as in
    the Pascal converter), so pass a stable order.
    """
    segs = []
    for key, level, p0, p1, t in walls:
        if t is None or not math.isfinite(t) or t <= 0:
            continue
        L = math.dist(p0, p1)
        if L < 1e-6:
            continue
        d = ((p1[0] - p0[0]) / L, (p1[1] - p0[1]) / L)
        segs.append((key, level, tuple(p0), tuple(p1), d, L, float(t)))

    found = {}
    for key, level, s0, s1, sd, sL, st in segs:
        cladding = st <= MAX_CLADDING_THICKNESS_M
        lined = 0.0
        host = kind = None
        for okey, olevel, o0, _o1, od, oL, ot in segs:
            if okey == key or okey in found or olevel != level:
                continue
            if abs(_cross(sd[0], sd[1], od[0], od[1])) > PARALLEL_SINE:
                continue
            offset = abs(_cross(s0[0] - o0[0], s0[1] - o0[1], od[0], od[1]))

            def proj(p, o0=o0, od=od):
                return (p[0] - o0[0]) * od[0] + (p[1] - o0[1]) * od[1]

            a, b = sorted((proj(s0), proj(s1)))
            overlap = max(0.0, min(b, oL) - max(a, 0.0))
            if (
                ot > st
                and offset + st / 2 <= ot / 2 + EMBED_TOL_M
                and overlap >= sL - EMBED_OVERLAP_SLACK_M
            ):
                lined, host, kind = sL, okey, "embedded"
                break
            face = abs(offset - (ot + st) / 2) <= FACE_TOL_M
            through = offset < max(ot, st) / 2
            if sL < oL and (face or through) and overlap >= sL * LINING_OVERLAP_FRAC:
                lined, host = sL, okey
                kind = "face to face" if face else "runs through"
                break
            if cladding and ot > 2 * st and offset <= (ot + st) / 2 + CLADDING_FACE_TOL_M:
                lined += overlap
                if host is None:
                    host, kind = okey, "cladding"
        if host is not None and lined >= sL * CLADDING_LINED_FRAC:
            found[key] = (host, kind)
    return found


def _gid(ew):
    note = (ew.provenance.note or "") if ew.provenance is not None else ""
    tok = note.split(" ", 1)[0]
    return tok[len("GlobalId=") :] if tok.startswith("GlobalId=") else ""


def exclude_linings(model, thickness_by_gid):
    """Remove lining walls from ``model.envelope`` in place (#577).

    Only Tier 0 IFC envelope segments take part. Returns
    {"lining": walls excluded, "kept": linings kept because they host openings}.
    """
    walls = [
        w
        for w in model.envelope
        if w.provenance is not None
        and w.provenance.method == ENVELOPE_METHOD
        and len(w.from_m) >= 2
        and len(w.to_m) >= 2
        and _gid(w)
    ]
    found = find_linings(
        [
            (
                _gid(w),
                w.id.split("-EW")[0] if "-EW" in w.id else "",
                w.from_m,
                w.to_m,
                thickness_by_gid.get(_gid(w)),
            )
            for w in walls
        ]
    )
    counts = {"lining": 0, "kept": 0}
    if not found:
        return counts
    elements = {e.global_id: e for e in model.bim_elements}
    drop = set()
    for w in walls:
        g = _gid(w)
        if g not in found:
            continue
        host, kind = found[g]
        el = elements.get(g)
        if el is not None and el.openings:
            counts["kept"] += 1
            w.provenance.note += (
                f"; looks like a lining of {host} ({kind}) but hosts "
                f"{len(el.openings)} opening(s), so kept in the envelope"
            )
            continue
        counts["lining"] += 1
        drop.add(id(w))
        if el is not None:
            el.role = "lining"
            if el.provenance is not None:
                el.provenance.note += (
                    f"; lining of {host} ({kind}): excluded from envelope area, "
                    "facade classification, U averaging and wall loops (#577)"
                )
    model.envelope = [w for w in model.envelope if id(w) not in drop]
    return counts
