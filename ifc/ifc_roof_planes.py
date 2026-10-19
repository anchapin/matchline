"""Roof planes from IFC roof geometry (roadmap item 2, #614).

Every IfcSlab with PredefinedType ROOF, every slab an IfcRoof aggregates, and
every IfcRoof that carries its own body is meshed in world coordinates. The
upward-facing triangles are grouped into planes (coplanar AND edge-connected,
so two separate facets on one slope stay two planes) and each group becomes a
``RoofPlane``: its outer boundary lifted onto the plane, in the canonical
frame (x east, y down, z above the level floor). Openings cut through the
slab leave holes in the mesh; the plane keeps its gross outline, the way BEM
hosts a skylight in its roof surface.

Nothing is fitted or guessed: an element the geometry kernel cannot mesh is
listed for review and gives no plane. A flat roof gives a tilt-0 plane.
"""

from __future__ import annotations

import math

ROOF_UP_NZ = 0.1  # faces steeper than ~84 deg are slab edges, not roof
COPLANAR_COS = 1 - 1e-6  # normals this close count as one plane
COPLANAR_OFFSET_M = 0.005  # plane offsets this close count as one plane
KEY_DIGITS = 4  # vertex welding: 0.1 mm
EDGE_PERP_COS = 0.05  # a face this close to perpendicular to a bigger one ...
EDGE_MAX_WIDTH_M = 0.6  # ... and no wider than this is the slab's cut edge


def roof_elements(f):
    """Roof elements to mesh, each once, in a stable GlobalId order."""
    out = {}
    for s in f.by_type("IfcSlab"):
        if getattr(s, "PredefinedType", None) == "ROOF":
            out[s.GlobalId] = s
    for r in f.by_type("IfcRoof"):
        parts = [
            o
            for rel in getattr(r, "IsDecomposedBy", None) or []
            for o in rel.RelatedObjects or []
            if o.is_a("IfcSlab") or o.is_a("IfcPlate") or o.is_a("IfcCovering")
        ]
        if parts:
            for p in parts:
                if p.is_a("IfcSlab"):
                    out[p.GlobalId] = p
        elif getattr(r, "Representation", None) is not None:
            out[r.GlobalId] = r
    return [out[g] for g in sorted(out)]


def world_mesh(el):
    """(vertices [(x, y, z)] in metres, IFC world frame, faces [(i, j, k)]).

    None when the geometry kernel fails on the element.
    """
    try:
        import ifcopenshell.geom as _g

        st = _g.settings()
        st.set("use-world-coords", True)
        shape = _g.create_shape(st, el)
    except (RuntimeError, ValueError):
        return None
    v = shape.geometry.verts
    fa = shape.geometry.faces
    verts = [(float(v[i]), float(v[i + 1]), float(v[i + 2])) for i in range(0, len(v), 3)]
    faces = [(fa[i], fa[i + 1], fa[i + 2]) for i in range(0, len(fa), 3)]
    return verts, faces


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def top_facets(verts, faces):
    """Groups of upward triangles, one per plane: [(unit normal, d, [tri])].

    A triangle is a tuple of three (x, y, z) points; d is n . p on the plane.
    """
    key = lambda p: tuple(round(c, KEY_DIGITS) for c in p)  # noqa: E731
    tris = []
    for i, j, k in faces:
        a, b, c = verts[i], verts[j], verts[k]
        n = _cross(tuple(b[m] - a[m] for m in range(3)), tuple(c[m] - a[m] for m in range(3)))
        ln = math.sqrt(sum(x * x for x in n))
        if ln < 1e-12:
            continue
        n = tuple(x / ln for x in n)
        if n[2] <= ROOF_UP_NZ:
            continue
        d = sum(n[m] * a[m] for m in range(3))
        tris.append(
            (n, d, (a, b, c), {frozenset((key(p), key(q))) for p, q in ((a, b), (b, c), (c, a))})
        )
    parent = list(range(len(tris)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_edge = {}
    for t, (_, _, _, edges) in enumerate(tris):
        for e in edges:
            by_edge.setdefault(e, []).append(t)
    for ts in by_edge.values():
        for a in ts:
            for b in ts:
                if a < b:
                    na, da = tris[a][0], tris[a][1]
                    nb, db = tris[b][0], tris[b][1]
                    if (
                        sum(na[m] * nb[m] for m in range(3)) >= COPLANAR_COS
                        and abs(da - db) <= COPLANAR_OFFSET_M
                    ):
                        parent[find(a)] = find(b)
    groups = {}
    for t in range(len(tris)):
        groups.setdefault(find(t), []).append(t)
    out = []
    for ts in groups.values():
        area = [
            math.sqrt(
                sum(
                    x * x
                    for x in _cross(
                        tuple(tris[t][2][1][m] - tris[t][2][0][m] for m in range(3)),
                        tuple(tris[t][2][2][m] - tris[t][2][0][m] for m in range(3)),
                    )
                )
            )
            for t in ts
        ]
        w = sum(area) or 1.0
        n = tuple(sum(tris[t][0][m] * a for t, a in zip(ts, area)) / w for m in range(3))
        ln = math.sqrt(sum(x * x for x in n))
        n = tuple(x / ln for x in n)
        d = sum(tris[t][1] * a for t, a in zip(ts, area)) / w
        out.append((n, d, [tris[t][2] for t in ts]))
    return out


def _tri_area(t):
    a, b, c = t
    return 0.5 * math.sqrt(
        sum(
            x * x
            for x in _cross(
                tuple(b[m] - a[m] for m in range(3)), tuple(c[m] - a[m] for m in range(3))
            )
        )
    )


def drop_slab_edges(groups):
    """Remove the cut edges of a thick sloped slab from its upward faces.

    A pitched slab extruded along its normal has an upslope edge face that
    also points up (a 30 deg roof's ridge-side cut faces up at 60 deg). It is
    perpendicular to the slab's top and only as wide as the slab is thick, so
    a group perpendicular to a larger group and no wider than
    EDGE_MAX_WIDTH_M (area over its longest chord) is dropped.
    """
    sized = []
    for n, d, tris in groups:
        area = sum(_tri_area(t) for t in tris)
        pts = [p for t in tris for p in t]
        chord = max(
            (math.dist(a, b) for i, a in enumerate(pts) for b in pts[i + 1 :]),
            default=0.0,
        )
        sized.append((area, chord, (n, d, tris)))
    keep = []
    for area, chord, g in sized:
        width = area / chord if chord else 0.0
        edge = width <= EDGE_MAX_WIDTH_M and any(
            a2 > area and abs(sum(g[0][m] * g2[0][m] for m in range(3))) <= EDGE_PERP_COS
            for a2, _, g2 in sized
        )
        if not edge:
            keep.append(g)
    return keep


def facet_outline(n, d, tris):
    """Outer boundary of one plane's triangles, as (x, y, z) world points.

    The triangles are merged in plan (their plane is never vertical) and the
    outer ring lifted back onto the plane. None for a degenerate group.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = [Polygon([(p[0], p[1]) for p in t]) for t in tris]
    merged = unary_union([p.buffer(0) for p in polys if p.area > 0])
    if merged.is_empty:
        return None
    if merged.geom_type != "Polygon":
        merged = max(merged.geoms, key=lambda g: g.area)
    ring = merged.exterior.simplify(1e-6)
    pts = list(ring.coords)[:-1]
    if len(pts) < 3:
        return None
    return [(x, y, (d - n[0] * x - n[1] * y) / n[2]) for x, y in pts]


def read_roof_planes(model, f, level_by_storey, prov):
    """Fill ``model.roof_planes`` from the file's roof geometry (#614).

    Each plane gets provenance ``ifc_import:tier0:roof_plane`` (0.9) naming
    its host GlobalId; z is above the host's level elevation. Elements the
    kernel cannot mesh, or that give no upward face, go to the review queue
    and give no plane. Returns (planes, roof elements read, elements flagged).
    """
    from roof_geometry import RoofGeometryError, roof_plane

    elements = roof_elements(f)
    if not elements:
        return 0, 0, 0
    level_of = {}
    for rel in f.by_type("IfcRelContainedInSpatialStructure"):
        lid = level_by_storey.get(getattr(rel.RelatingStructure, "GlobalId", None))
        for o in rel.RelatedElements or []:
            if lid:
                level_of[o.GlobalId] = lid
                for drel in getattr(o, "IsDecomposedBy", None) or []:
                    for part in drel.RelatedObjects or []:
                        level_of.setdefault(part.GlobalId, lid)
    elev = {lv.id: lv.elevation_z_m for lv in model.levels}
    default_lid = model.levels[-1].id if model.levels else ""
    flagged = 0
    for el in elements:
        gid = el.GlobalId
        lid = level_of.get(gid, default_lid)
        z0 = elev.get(lid, 0.0)
        mesh = world_mesh(el)
        groups = drop_slab_edges(top_facets(*mesh)) if mesh else []
        planes = []
        for n, d, tris in groups:
            pts = facet_outline(n, d, tris)
            if pts is None:
                continue
            planes.append([(x, -y, z - z0) for x, y, z in pts])
        if not planes:
            flagged += 1
            why = (
                "the geometry kernel could not mesh it"
                if mesh is None
                else "it has no upward-facing surface"
            )
            model.flag_for_review(
                "roof_plane",
                f"Roof element {gid} gave no roof plane: {why}",
                0.4,
                prov("ifc_import:tier0:roof_plane", 0.4, why, gid),
            )
            continue
        planes.sort(
            key=lambda c: (
                round(sum(p[0] for p in c) / len(c), 3),
                round(sum(p[1] for p in c) / len(c), 3),
            )
        )
        for k, canon in enumerate(planes):
            try:
                rp = roof_plane(
                    f"RF-{gid}-{k + 1}",
                    canon,
                    level_id=lid,
                    host_global_id=gid,
                    provenance=prov(
                        "ifc_import:tier0:roof_plane", 0.9, "upward face of roof body", gid
                    ),
                )
            except RoofGeometryError:
                continue
            model.roof_planes.append(rp)
    return len(model.roof_planes), len(elements), flagged


def orient_skylights(model):
    """Tilt and azimuth of each skylight from the roof plane above it.

    A skylight takes the plane on its host whose plan outline contains its
    plan centre; with no host match, any plane on the same level. Skylights
    over no plane keep tilt/azimuth None. Returns the number oriented.
    """
    from shapely.geometry import Point, Polygon

    if not model.roof_planes:
        return 0
    shapes = [(rp, Polygon([(v[0], v[1]) for v in rp.vertices_m])) for rp in model.roof_planes]
    seen = {}
    n = 0
    for be in model.bim_elements:
        for bo in be.openings:
            if bo.category != "skylight" or not bo.plan_center_m:
                continue
            pt = Point(bo.plan_center_m[0], bo.plan_center_m[1])
            hits = [rp for rp, poly in shapes if poly.buffer(1e-6).contains(pt)]
            host = [rp for rp in hits if rp.host_global_id == getattr(be, "global_id", "")]
            pick = (host or hits or [None])[0]
            if pick is None:
                continue
            bo.tilt_deg, bo.azimuth_deg = pick.tilt_deg, pick.azimuth_deg
            seen[bo.fill_global_id or id(bo)] = (pick.tilt_deg, pick.azimuth_deg)
            n += 1
    for sp in model.spaces.values():
        for op in sp.openings:
            if op.category == "skylight":
                hit = seen.get(getattr(op, "fill_global_id", None))
                if hit:
                    op.tilt_deg, op.azimuth_deg = hit
    return n
