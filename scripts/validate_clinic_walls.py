"""Measure plan_walls (#740) on floor plans drawn from the BSI Clinic architectural IFC.

The IFC is held out: never committed, run on the development machine. For each
storey the walls are cut at ``CUT_M`` above the floor (the way a plan is drawn),
the cut outlines are unioned and drawn as stroked lines on a 1:100 vector sheet,
and room labels are placed at each IfcSpace centroid. ``extract_walls`` reads
that sheet; its rooms are compared with the IfcSpace footprints and its total
wall length with the IFC walls' cut length. ``rooms_wall_bounded`` compares the
same rooms with IfcSpaces joined across edges no wall stands on (corridor
segments, areas open to a corridor), which a plan cannot show.

    python scripts/validate_clinic_walls.py /path/Clinic_Architectural.ifc [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plan_walls import compare_rooms, extract_walls, wall_length_error  # noqa: E402
from scripts.validate_clinic_hvac import _storey_of, footprint  # noqa: E402

CUT_M = 1.2  # plan cut height above the storey floor
SCALE = 100  # 1:100
M_PER_PT = SCALE * 0.0254 / 72
MARGIN_PT = 72.0
TEXT_PT = 6.0
PLAN_STOREYS = ("First Floor", "Second Floor")


def section(verts: np.ndarray, faces: np.ndarray, z: float):
    """Segments where a triangle mesh crosses the plane at height z."""
    out = []
    for f in faces:
        p = verts[f]
        d = p[:, 2] - z
        if (d > 0).all() or (d < 0).all():
            continue
        pts = []
        for i in range(3):
            a, b = p[i], p[(i + 1) % 3]
            da, db = d[i], d[(i + 1) % 3]
            if da == 0:
                pts.append((a[0], a[1]))
            if (da < 0 < db) or (db < 0 < da):
                t = da / (da - db)
                q = a + t * (b - a)
                pts.append((q[0], q[1]))
        pts = list(dict.fromkeys((round(x, 6), round(y, 6)) for x, y in pts))
        if len(pts) == 2:
            out.append(tuple(pts))
    return out


def cut_polygons(segs):
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    if not segs:
        return []
    return list(polygonize(unary_union([LineString(s) for s in segs])))


def load(arch_path):
    """Per storey: (wall cut polygons, truth wall length m, rooms [(number, name, ring)],
    wall and door/window opening polygons cut at the plan height)."""
    import ifcopenshell
    import ifcopenshell.geom as geom

    f = ifcopenshell.open(str(arch_path))
    elev = {s.Name: float(s.Elevation) for s in f.by_type("IfcBuildingStorey")}
    s = geom.settings()
    s.set(s.USE_WORLD_COORDS, True)

    def mesh(e):
        # Revit walls carry an Axis curve first; the cut needs the Body solid
        reps = e.Representation.Representations if e.Representation else []
        body = [r for r in reps if r.RepresentationIdentifier == "Body"]
        sh = geom.create_shape(s, e, body[0]) if body else geom.create_shape(s, e)
        v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
        fc = np.array(sh.geometry.faces, dtype=int).reshape(-1, 3)
        return v, fc

    walls = defaultdict(list)
    bound = defaultdict(list)
    wall_len = defaultdict(float)
    for e in f.by_type("IfcWall") + f.by_type("IfcCurtainWall"):
        st = _storey_of(e)
        if st not in PLAN_STOREYS:
            continue
        try:
            v, fc = mesh(e)
        except Exception:
            continue
        polys = cut_polygons(section(v, fc, elev[st] + CUT_M))
        walls[st] += polys
        bound[st] += polys
        for p in polys:
            r = p.minimum_rotated_rectangle
            c = list(r.exterior.coords)
            e0, e1 = np.hypot(*np.subtract(c[1], c[0])), np.hypot(*np.subtract(c[2], c[1]))
            wall_len[st] += max(e0, e1)
    # door and window openings close an edge between two spaces as much as the wall does
    for o in f.by_type("IfcOpeningElement"):
        host = o.VoidsElements[0].RelatingBuildingElement if o.VoidsElements else None
        st = _storey_of(host) if host is not None else None
        if st not in PLAN_STOREYS:
            continue
        try:
            v, fc = mesh(o)
        except Exception:
            continue
        bound[st] += cut_polygons(section(v, fc, elev[st] + CUT_M))
    rooms = defaultdict(list)
    for sp in f.by_type("IfcSpace"):
        st = _storey_of(sp)
        if st not in PLAN_STOREYS:
            continue
        try:
            v, fc = mesh(sp)
        except Exception:
            continue
        ring = footprint(v[:, :2].tolist(), fc.tolist())
        if len(ring) >= 3:
            rooms[st].append((sp.Name, sp.LongName or "", ring))
    return walls, wall_len, rooms, bound


def sheet_for(wall_polys, rooms):
    """A 1:100 vector sheet (pdf_ingest layout) and the metre offset of its frame."""
    from shapely.ops import unary_union

    u = unary_union(wall_polys).simplify(0.001)
    minx, miny, maxx, maxy = u.bounds
    W = (maxx - minx) / M_PER_PT + 2 * MARGIN_PT
    H = (maxy - miny) / M_PER_PT + 2 * MARGIN_PT

    def pt(x, y):
        return [(x - minx) / M_PER_PT + MARGIN_PT, H - ((y - miny) / M_PER_PT + MARGIN_PT)]

    prims = []
    for g in getattr(u, "geoms", [u]):
        for ring in [g.exterior, *g.interiors]:
            c = list(ring.coords)[:-1]
            segs = [["M", [pt(*c[0])]]] + [["L", [pt(*q)]] for q in c[1:]] + [["Z", []]]
            prims.append(
                {
                    "kind": "polygon",
                    "stroked": True,
                    "filled": False,
                    "dashed": False,
                    "segments": segs,
                }
            )
    text = []
    from shapely.geometry import Polygon

    for number, name, ring in rooms:
        c = Polygon(ring).representative_point()
        x, y = pt(c.x, c.y)
        for i, t in enumerate([name, number]):
            w = 0.55 * TEXT_PT * len(t)
            y0 = y - TEXT_PT + i * (TEXT_PT + 1)
            text.append({"text": t, "bbox": [x - w / 2, y0, x + w / 2, y0 + TEXT_PT]})
    off = (minx - MARGIN_PT * M_PER_PT, miny - MARGIN_PT * M_PER_PT)
    return {"width_pt": W, "height_pt": H, "primitives": prims, "text": text}, off


OPEN_PROBE_M = 0.35  # half-width of the strip probed either side of a shared room edge
MIN_OPEN_M = 0.8  # open (wall-free) shared edge that joins two IfcSpaces into one region


def _open_edge_m(a, b, walls, gap_m, step_m=0.1):
    """Length of a's boundary facing b (within gap_m) with no wall between them.

    A point on a's edge faces b when the nearest point of b maps back to it
    (so the strip round a shared corner does not count); it is open when the
    segment between them does not cross wall material.
    """
    from shapely.geometry import LineString, Point
    from shapely.ops import nearest_points

    edge = a.exterior.intersection(b.buffer(gap_m))
    if edge.is_empty or edge.length == 0:
        return 0.0
    n = max(int(edge.length / step_m), 1)
    open_n = 0
    for k in range(n):
        p = edge.interpolate((k + 0.5) / n, normalized=True)
        q = nearest_points(b, p)[0]
        back = nearest_points(a.exterior, q)[0]
        if back.distance(p) > 0.05:
            continue
        probe = LineString([p, q]) if p.distance(q) > 1e-6 else Point(p.x, p.y)
        if probe.buffer(0.01).intersection(walls).area < 1e-5:
            open_n += 1
    return open_n * edge.length / n


def wall_bounded_truth(rooms, wall_polys, min_open_m=MIN_OPEN_M, probe_m=OPEN_PROBE_M):
    """Join IfcSpaces that share an edge no wall stands on (#740).

    IfcSpace boundaries include virtual ones: corridor segments, a waiting area
    open to a corridor, an alcove. A floor plan shows no line there, so no wall
    finder can recover them. Spaces whose shared edge has at least
    ``min_open_m`` outside IFC wall material are joined into one region; the
    result is the set of rooms a plan's walls can actually bound.
    Returns ``[(names, ring)]``.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = [Polygon(r).buffer(0) for _, _, r in rooms]
    walls = unary_union(list(wall_polys))
    parent = list(range(len(polys)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grown = [p.buffer(probe_m) for p in polys]
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            if (
                grown[i].intersects(polys[j])
                and _open_edge_m(polys[i], polys[j], walls, probe_m) >= min_open_m
            ):
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(len(polys)):
        groups[find(i)].append(i)
    out = []
    for idx in groups.values():
        u = unary_union([polys[i] for i in idx])
        if u.geom_type != "Polygon":
            u = u.buffer(probe_m).buffer(-probe_m)
        if u.geom_type != "Polygon":
            u = max(u.geoms, key=lambda g: g.area)
        out.append(([rooms[i][1] for i in idx], list(u.exterior.coords)))
    return out


def measure(arch_path):
    walls, wall_len, rooms, bound = load(arch_path)
    out = {}
    for st in PLAN_STOREYS:
        sheet, (ox, oy) = sheet_for(walls[st], rooms[st])
        res = extract_walls(sheet, M_PER_PT)
        pred = [[(x + ox, y + oy) for x, y in r.polygon_m] for r in res.rooms]
        cmp = compare_rooms(pred, [r for _, _, r in rooms[st]])
        cmp.pop("per_room")
        bounded = wall_bounded_truth(rooms[st], bound[st])
        cmp_b = compare_rooms(pred, [r for _, r in bounded])
        cmp_b.pop("per_room")
        cmp_b["joined_spaces"] = sum(len(n) for n, _ in bounded if len(n) > 1)
        kinds = defaultdict(int)
        for item in res.review:
            kinds[item["kind"]] += 1
        pl = res.stats["wall_length_m"]
        out[st] = {
            "rooms": cmp,
            "rooms_wall_bounded": cmp_b,
            "wall_length_m": {
                "pred": pl,
                "truth": round(wall_len[st], 2),
                "err": round(wall_length_error(pl, wall_len[st]), 4),
            },
            "openings": res.stats["openings"],
            "review": dict(kinds),
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ifc", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    out = measure(a.ifc)
    print(json.dumps(out, indent=2))
    if a.json:
        a.json.write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
