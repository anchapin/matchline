"""Check HVAC diffuser-to-room assignment against a real HVAC model (#707).

Uses buildingSMART's Medical-Dental Clinic test files (a real, redacted clinic,
CC BY 4.0: BSI (2020) "Medical-Dental Test Files," buildingSMART International,
https://github.com/buildingsmart-community/Community-Sample-Test-Files).
The IFC is ~27 MB and is never committed; download it and pass its path.

Ground truth is the designer's own containment: Revit places 437 of the 440
air terminals in an IfcSpace via IfcRelContainedInSpatialStructure. Matchline
gets only geometry: each space's floor footprint (as ``polygon_m``) and each
terminal's plan position, run through ``hvac_trace._assign_diffuser_room``.
``jitter_m`` moves every terminal a fixed distance in a random direction to
mimic a detector's localisation error.

This checks room assignment on real room shapes. It does NOT check symbol
detection: there is no rendered mechanical sheet here yet.

Usage:
    python scripts/validate_clinic_hvac.py Clinic_HVAC.ifc [--jitter 0 0.25 0.5]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hvac_trace import _assign_diffuser_room  # noqa: E402

SIMPLIFY_M = 0.02


def footprint(verts_xy, faces, simplify_m=SIMPLIFY_M):
    """Plan outline of a meshed solid: union of its triangles projected to XY.

    Returns the largest polygon's exterior ring (no repeated closing point).
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    tris = [Polygon([verts_xy[i] for i in f]) for f in faces]
    tris = [t.buffer(1e-6) for t in tris if t.area > 1e-9]
    if not tris:
        return []
    poly = unary_union(tris).buffer(-1e-6)
    if poly.geom_type != "Polygon":
        poly = max(poly.geoms, key=lambda g: g.area)
    poly = poly.simplify(simplify_m)
    return [(float(x), float(y)) for x, y in list(poly.exterior.coords)[:-1]]


def summarize(rows):
    """Per-storey counts from ``(storey, outcome, method)`` rows.

    outcome is ``correct``, ``wrong`` or ``review`` (no room assigned).
    """
    out = {}
    for st in sorted({r[0] for r in rows}):
        sub = [r for r in rows if r[0] == st]
        c = Counter(r[1] for r in sub)
        n = len(sub)
        out[st] = {
            "terminals": n,
            "correct": c["correct"],
            "wrong": c["wrong"],
            "review": c["review"],
            "accuracy": round(c["correct"] / n, 4) if n else None,
            "methods": dict(Counter(r[2] for r in sub)),
        }
    return out


def _storey_of(e):
    for r in getattr(e, "Decomposes", None) or []:
        if r.RelatingObject.is_a("IfcBuildingStorey"):
            return r.RelatingObject.Name
    for r in getattr(e, "ContainedInStructure", None) or []:
        o = r.RelatingStructure
        if o.is_a("IfcBuildingStorey"):
            return o.Name
        if o.is_a("IfcSpace"):
            return _storey_of(o)
    return None


def load(hvac_path):
    """(rooms by storey, terminals) from the HVAC IFC.

    terminals: dicts with x, y (m, world), storey, gt_space (GlobalId), system.
    """
    import ifcopenshell
    import ifcopenshell.geom as geom

    f = ifcopenshell.open(str(hvac_path))
    s = geom.settings()
    s.set(s.USE_WORLD_COORDS, True)

    def mesh(e):
        g = geom.create_shape(s, e).geometry
        v = [tuple(g.verts[i : i + 3]) for i in range(0, len(g.verts), 3)]
        fa = [tuple(g.faces[i : i + 3]) for i in range(0, len(g.faces), 3)]
        return v, fa

    rooms = defaultdict(list)
    for sp in f.by_type("IfcSpace"):
        v, fa = mesh(sp)
        poly = footprint([(x, y) for x, y, _ in v], fa)
        if len(poly) >= 3:
            rooms[_storey_of(sp)].append({"id": sp.GlobalId, "name": sp.Name, "polygon_m": poly})

    terminals = []
    for t in f.by_type("IfcFlowTerminal"):
        gt = [
            r.RelatingStructure
            for r in t.ContainedInStructure or []
            if r.RelatingStructure.is_a("IfcSpace")
        ]
        if not gt:
            continue  # designer gave no room: nothing to score against
        v, _ = mesh(t)
        system = None
        for rel in t.IsDefinedBy or []:
            ps = getattr(rel, "RelatingPropertyDefinition", None)
            if ps is not None and ps.is_a("IfcPropertySet"):
                for p in ps.HasProperties:
                    if p.Name == "System Classification" and p.NominalValue:
                        system = p.NominalValue.wrappedValue
        terminals.append(
            {
                "x": sum(p[0] for p in v) / len(v),
                "y": sum(p[1] for p in v) / len(v),
                "storey": _storey_of(gt[0]),
                "gt_space": gt[0].GlobalId,
                "system": system,
            }
        )
    return dict(rooms), terminals


def evaluate(rooms, terminals, jitter_m=0.0, seed=0):
    import numpy as np

    rng = np.random.default_rng([seed, 707])
    rows = []
    for t in terminals:
        a = rng.uniform(0, 2 * math.pi)
        x, y = t["x"] + jitter_m * math.cos(a), t["y"] + jitter_m * math.sin(a)
        rid, method, _ = _assign_diffuser_room(x, y, rooms.get(t["storey"], []))
        if rid is None:
            outcome = "review"
        else:
            outcome = "correct" if rid == t["gt_space"] else "wrong"
        rows.append((t["storey"], outcome, method))
    return summarize(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("hvac_ifc")
    ap.add_argument("--jitter", type=float, nargs="*", default=[0.0])
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    rooms, terminals = load(args.hvac_ifc)
    report = {
        "spaces": {k: len(v) for k, v in rooms.items()},
        "terminals_scored": len(terminals),
        "runs": {str(j): evaluate(rooms, terminals, j, args.seed) for j in args.jitter},
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
