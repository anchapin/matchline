"""Extract per-storey plan data from the BSI Clinic HVAC IFC (#707 detection half).

Writes a JSON cache: for each storey, room footprints, air terminals (class,
plan position, designer's space) and duct footprints (plan convex hulls).
The IFC is never committed; the cache is a local artifact too.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_clinic_hvac import _storey_of, footprint  # noqa: E402

# Revit system classification -> matchline mechanical symbol class.
TERMINAL_CLASS = {"Supply Air": "diffuser", "Return Air": "grille", "Exhaust Air": "grille"}


def _system(e):
    for rel in e.IsDefinedBy or []:
        ps = getattr(rel, "RelatingPropertyDefinition", None)
        if ps is not None and ps.is_a("IfcPropertySet"):
            for p in ps.HasProperties:
                if p.Name == "System Classification" and getattr(p, "NominalValue", None):
                    return p.NominalValue.wrappedValue
    return None


def _hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo, hi = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(hi) >= 2 and cross(hi[-2], hi[-1], p) <= 0:
            hi.pop()
        hi.append(p)
    return lo[:-1] + hi[:-1]


def extract(hvac_path):
    import ifcopenshell
    import ifcopenshell.geom as geom

    f = ifcopenshell.open(str(hvac_path))
    s = geom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    wanted = f.by_type("IfcSpace") + f.by_type("IfcFlowTerminal") + f.by_type("IfcFlowSegment")
    meshes = {}
    it = geom.iterator(s, f, multiprocessing.cpu_count(), include=wanted)
    if it.initialize():
        while True:
            sh = it.get()
            g = sh.geometry
            v = [tuple(g.verts[i : i + 3]) for i in range(0, len(g.verts), 3)]
            fa = [tuple(g.faces[i : i + 3]) for i in range(0, len(g.faces), 3)]
            meshes[sh.guid] = (v, fa)
            if not it.next():
                break
    out = defaultdict(lambda: {"rooms": [], "terminals": [], "ducts": []})
    for sp in f.by_type("IfcSpace"):
        if sp.GlobalId not in meshes:
            continue
        v, fa = meshes[sp.GlobalId]
        poly = footprint([(x, y) for x, y, _ in v], fa)
        if len(poly) >= 3:
            out[_storey_of(sp)]["rooms"].append(
                {"id": sp.GlobalId, "name": sp.Name, "long_name": sp.LongName, "polygon_m": poly}
            )
    for t in f.by_type("IfcFlowTerminal"):
        cls = TERMINAL_CLASS.get(_system(t))
        if cls is None or t.GlobalId not in meshes:
            continue
        v, _ = meshes[t.GlobalId]
        spaces = [
            r.RelatingStructure
            for r in t.ContainedInStructure or []
            if r.RelatingStructure.is_a("IfcSpace")
        ]
        out[_storey_of(t)]["terminals"].append(
            {
                "id": t.GlobalId,
                "cls": cls,
                "x": sum(p[0] for p in v) / len(v),
                "y": sum(p[1] for p in v) / len(v),
                "gt_space": spaces[0].GlobalId if spaces else None,
            }
        )
    for d in f.by_type("IfcFlowSegment"):
        if "Pipe" in (d.ObjectType or "") or d.GlobalId not in meshes:
            continue
        v, _ = meshes[d.GlobalId]
        h = _hull([(round(x, 3), round(y, 3)) for x, y, _ in v])
        if len(h) >= 3:
            out[_storey_of(d)]["ducts"].append(h)
    return dict(out)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    data = extract(src)
    Path(dst).write_text(json.dumps(data))
    for k, v in data.items():
        print(k, {kk: len(vv) for kk, vv in v.items()})
