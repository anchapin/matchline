# bem_helpers.py — shared helpers for gbXML and IFC export
# Extracted from bem_export.py to avoid duplication and reduce module sizes.

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

from room_labels import point_in_polygon as _pip

GBXML_NS = "http://www.gbxml.org/schema"

WINDOW_SILL_M = 0.9
DOOR_SILL_M = 0.0


def _fmt(v: float) -> str:
    return f"{v:.4f}"


def _el(parent, tag, text=None, **attrib):
    el = ET.SubElement(parent, f"{{{GBXML_NS}}}{tag}", attrib)
    if text is not None:
        el.text = str(text)
    return el


def _cartesian(parent, x, y, z=None):
    pt = _el(parent, "CartesianPoint")
    _el(pt, "Coordinate", _fmt(x))
    _el(pt, "Coordinate", _fmt(y))
    if z is not None:
        _el(pt, "Coordinate", _fmt(z))
    return pt


def _wall_edges(ring_m):
    n = len(ring_m)
    return [(ring_m[i], ring_m[(i + 1) % n]) for i in range(n)]


def _assign_wall_to_space(p0, p1, spaces):
    """Which space does this exterior wall belong to?

    Midpoint nudged inward along the inward normal; point-in-polygon wins.
    Falls back to nearest space centroid. Deterministic.
    """
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy) or 1.0
    # outward normal for CCW ring: right of direction
    nx, ny = dy / L, -dx / L
    ix, iy = mx - nx * 0.3, my - ny * 0.3  # 0.3 m inward
    for sp in spaces:
        if _pip((ix, iy), sp.polygon_m):
            return sp

    # fallback: nearest centroid
    def centroid(sp):
        n = len(sp.polygon_m)
        return (sum(p[0] for p in sp.polygon_m) / n, sum(p[1] for p in sp.polygon_m) / n)

    return min(spaces, key=lambda sp: math.hypot(centroid(sp)[0] - ix, centroid(sp)[1] - iy))


def _edge_facades(ring_m, y_north=True):
    """Cardinal facade of each ring edge i -> i+1, from its outward normal.

    Works for either winding (outward side comes from the signed area).
    ``y_north`` says which way +y points in this ring's frame: True for
    x=east/y=north, False for the canonical y-down plan frame.
    """
    n = len(ring_m)
    if n < 3:
        return []
    a2 = sum(
        ring_m[i][0] * ring_m[(i + 1) % n][1] - ring_m[(i + 1) % n][0] * ring_m[i][1]
        for i in range(n)
    )
    out = []
    for i in range(n):
        (x0, y0), (x1, y1) = ring_m[i][:2], ring_m[(i + 1) % n][:2]
        dx, dy = x1 - x0, y1 - y0
        # CCW (a2 > 0): outward is right of travel; CW: left
        nx, ny = (dy, -dx) if a2 > 0 else (-dy, dx)
        if abs(nx) >= abs(ny):
            out.append("east" if nx > 0 else "west")
        else:
            north = ny > 0 if y_north else ny < 0
            out.append("north" if north else "south")
    return out


def _apportion(units, idxs, lengths, assign):
    """Largest-remainder split of units over edges idxs, by edge length."""
    n = len(units)
    if not n or not idxs:
        return
    total_L = sum(lengths[i] for i in idxs) or 1.0
    shares = {i: n * lengths[i] / total_L for i in idxs}
    base = {i: int(math.floor(shares[i])) for i in idxs}
    rem = n - sum(base.values())
    order = sorted(idxs, key=lambda i: (shares[i] - base[i], lengths[i], -i), reverse=True)
    for i in order[:rem]:
        base[i] += 1
    k = 0
    for i in sorted(idxs):
        assign[i].extend(units[k : k + base[i]])
        k += base[i]


def _edge_spaces(edges, spaces):
    """Space id owning each wall edge (``_assign_wall_to_space``), or [] when no spaces."""
    spaces = [sp for sp in (spaces or []) if getattr(sp, "polygon_m", None)]
    if not spaces:
        return []
    return [_assign_wall_to_space(p0, p1, spaces).sid for p0, p1 in edges]


def _distribute_openings(openings, edges, edge_facades=None, edge_spaces=None):
    """Assign opening units to walls.

    Most specific first. An opening whose ``space_sid`` and ``host_facade``
    both match at least one edge (``edge_spaces`` / ``edge_facades``) goes on
    that space's share of that facade, so a window in one room stays on that
    room's wall when the facade is split per space. Otherwise an opening with
    a matching ``host_facade`` goes on that facade's edges (split by length
    when the facade has several). Everything else -- no facade known, or no
    edge with that facade -- is apportioned over all edges by length.
    Largest-remainder apportionment per group, so totals are exact and the
    assignment is deterministic. Returns {wall_idx: [units]}.
    """
    lengths = [math.hypot(p1[0] - p0[0], p1[1] - p0[1]) for p0, p1 in edges]
    assign = {i: [] for i in range(len(edges))}
    facades = list(edge_facades or [])
    if len(facades) != len(edges):
        facades = []
    spaces = list(edge_spaces or [])
    if len(spaces) != len(edges) or not facades:
        spaces = []
    by_facade = {}
    by_space_facade = {}
    for i, f in enumerate(facades):
        if lengths[i] > 1e-6:
            by_facade.setdefault(f, []).append(i)
            if spaces and spaces[i]:
                by_space_facade.setdefault((spaces[i], f), []).append(i)
    all_idx = list(range(len(edges)))
    for cat in ("window", "door"):
        units = [u for u in openings if u.category == cat]
        loose = []
        groups = {}
        for u in units:
            f = getattr(u, "host_facade", "") or ""
            sid = getattr(u, "space_sid", "") or ""
            if (sid, f) in by_space_facade:
                groups.setdefault((sid, f), []).append(u)
            elif f in by_facade:
                groups.setdefault(("", f), []).append(u)
            else:
                loose.append(u)
        for key in sorted(groups):
            idxs = by_space_facade[key] if key[0] else by_facade[key[1]]
            _apportion(groups[key], idxs, lengths, assign)
        _apportion(loose, all_idx, lengths, assign)
    for i in assign:
        assign[i].sort(key=lambda u: (u.category, u.tag))
    return assign


def _opening_refs(model, unit) -> dict:
    """gbXML Opening attributes naming the opening's construction (#747).

    Glazing (window, skylight) references a WindowType, a door a
    Construction. Empty when the opening has no exported construction, and
    for glazing with no visible transmittance: OpenStudio builds simple
    glazing from a WindowType only when it has U, SHGC and visible
    Transmittance, and otherwise leaves an empty construction that will not
    simulate.
    """
    cid = getattr(unit, "construction_id", "") or ""
    info = (getattr(model, "opening_constructions", None) or {}).get(cid) if cid else None
    if not info:
        return {}
    if info["category"] == "door":
        return {"constructionIdRef": cid}
    if glazing_exportable(info):
        return {"windowTypeIdRef": cid}
    return {}


def glazing_exportable(info: dict) -> bool:
    """A glazing construction has the U, SHGC and VT a WindowType needs (#747)."""
    return all(info.get(k) is not None for k in ("u", "shgc", "vt"))


def _opening_type(category: str) -> str:
    # openingTypeEnum has no generic "Door": NonSlidingDoor is the closest.
    if category == "skylight":
        return "FixedSkylight"
    return "FixedWindow" if category == "window" else "NonSlidingDoor"


def _roof_outline(ring, regions=None):
    """The flat roof's outline: the envelope ring, or, when the ring is
    degenerate (an envelope with fewer than three usable edges), the union of
    the space footprints when that union is one polygon. None if neither
    gives a roof. Returned CCW so the roof's outward normal is +z.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    if ring and len(ring) >= 3:
        roof = Polygon(ring)
        if roof.is_valid and roof.area > 0:
            return [tuple(map(float, pt)) for pt in roof.exterior.coords[:-1]]
    polys = [Polygon(pg) for pg in (regions or {}).values() if pg and len(pg) >= 3]
    polys = [pg for pg in polys if pg.is_valid and pg.area > 0]
    if not polys:
        return None
    u = unary_union(polys)
    if u.geom_type != "Polygon" or u.area <= 0:
        return None
    ext = u.exterior if u.exterior.is_ccw else u.exterior.reverse()
    return [tuple(map(float, pt)) for pt in ext.coords[:-1]]


SKYLIGHT_SETBACK_M = 0.30  # min distance from a skylight to the roof edge
SKYLIGHT_CLEARANCE_M = 0.30  # min gap between two skylights
SKYLIGHT_GRID_STEP_M = 0.25  # candidate-centre spacing


def _place_skylights_on_roof(units, ring, regions=None):
    """Place skylight units on a flat roof over ``ring`` (CCW, metres).

    Takeoff lines carry a count and schedule dimensions, never a position,
    so placement is synthesised the same way wall openings are spread evenly
    along their walls. Each skylight is an axis-aligned rectangle (width
    along x, height along y) that must sit fully inside the roof, set back
    SKYLIGHT_SETBACK_M from its edge, with SKYLIGHT_CLEARANCE_M between
    skylights. Larger units are placed first. The first goes nearest the
    roof's interior point, each next one at the candidate farthest from those
    already placed, so the layout spreads over the roof instead of packing
    into one corner (top-lighting depends on the spread).

    ``regions`` maps a space id to its polygon (same frame as ``ring``). A
    unit whose ``space_sid`` is in it is confined to that space's share of
    the roof, so a skylight the drawings put over room 101 stays over 101;
    a unit with no known space may go anywhere on the roof.

    Deterministic. A unit that cannot fit is skipped with a note, never
    shrunk: a scaled skylight would quietly change glazing area, which the
    energy model is sensitive to.

    Returns (placements, notes); each placement is
    {"unit": u, "rect": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]}, CCW.
    """
    import numpy as np
    import shapely
    from shapely.geometry import Polygon

    placements, notes = [], []
    if not units:
        return placements, notes
    outline = _roof_outline(ring, regions)
    roof = Polygon(outline) if outline else Polygon()
    if roof.is_empty or not roof.is_valid or roof.area <= 0:
        notes.append(f"{len(units)} skylight(s) skipped: roof outline is degenerate")
        return placements, notes
    usable_roof = roof.buffer(-SKYLIGHT_SETBACK_M)
    usable_by_sid = {}
    for sid, poly in (regions or {}).items():
        # a space with no usable footprint simply has no region: its
        # skylights fall back to the whole roof rather than failing the export
        if not poly or len(poly) < 3:
            continue
        reg = Polygon(poly)
        if reg.is_valid and reg.area > 0:
            usable_by_sid[sid] = roof.intersection(reg).buffer(-SKYLIGHT_SETBACK_M)
    minx, miny, maxx, maxy = roof.bounds
    xs = np.arange(minx, maxx + 1e-9, SKYLIGHT_GRID_STEP_M)
    ys = np.arange(miny, maxy + 1e-9, SKYLIGHT_GRID_STEP_M)
    cx, cy = (a.ravel() for a in np.meshgrid(xs, ys))
    taken = None  # union of placed skylights grown by the clearance
    centres = []
    order = sorted(units, key=lambda u: (-(u.width_m * u.height_m), u.tag))
    for u in order:
        hw, hh = u.width_m / 2.0, u.height_m / 2.0
        sid = getattr(u, "space_sid", "") or ""
        usable = usable_by_sid.get(sid, usable_roof)
        where = f"space {sid}" if sid in usable_by_sid else "the roof"
        boxes = shapely.box(cx - hw, cy - hh, cx + hw, cy + hh)
        ok = shapely.contains(usable, boxes) if not usable.is_empty else np.zeros(len(cx), bool)
        if taken is not None:
            ok &= ~shapely.intersects(taken, boxes)
        if not ok.any():
            notes.append(
                f"{u.tag}: skipped, no room for a {u.width_m:.2f}x{u.height_m:.2f} m "
                f"skylight over {where} (setback {SKYLIGHT_SETBACK_M} m, "
                f"clearance {SKYLIGHT_CLEARANCE_M} m)"
            )
            continue
        idx = np.flatnonzero(ok)
        if centres:
            pc = np.array(centres)
            d = np.min(np.hypot(cx[idx][:, None] - pc[:, 0], cy[idx][:, None] - pc[:, 1]), axis=1)
            k = idx[int(np.argmax(d))]
        else:
            anchor = (usable if not usable.is_empty else roof).representative_point()
            k = idx[int(np.argmin(np.hypot(cx[idx] - anchor.x, cy[idx] - anchor.y)))]
        x, y = float(cx[k]), float(cy[k])
        rect = [(x - hw, y - hh), (x + hw, y - hh), (x + hw, y + hh), (x - hw, y + hh)]
        placements.append({"unit": u, "rect": rect})
        centres.append((x, y))
        grown = boxes[k].buffer(SKYLIGHT_CLEARANCE_M, join_style="mitre")
        taken = grown if taken is None else taken.union(grown)
    return placements, notes


def _place_openings_on_wall(units, L: float, h: float):
    placements, notes = [], []

    total_width = sum(u.width_m for u in units)
    usable = L - 0.1
    scale = 1.0
    if total_width > usable:
        scale = usable / total_width
        notes.append(
            f"Openings scaled to {scale:.3f} to fit wall {L:.2f} m "
            f"(total width {total_width:.2f} m)"
        )

    pos = 0.05
    for j, u in enumerate(units):
        sill = WINDOW_SILL_M if u.category == "window" else DOOR_SILL_M
        oh = u.height_m
        if sill >= h:
            notes.append(f"{u.tag}: skipped – sill {sill:.2f} m ≥ wall height {h:.2f} m")
            continue
        if sill + oh > h:
            oh = h - sill
            notes.append(f"{u.tag}: height clamped to {oh:.2f} m (wall {h:.2f} m)")
        oh = max(0, oh)

        scaled_w = u.width_m * scale
        s0 = pos
        s1 = pos + scaled_w
        pos = s1 + 0.1

        if s1 - s0 < scaled_w * 0.5:
            notes.append(
                f"{u.tag}: width clamped ({u.width_m:.2f} -> {s1 - s0:.2f} m, wall {L:.2f} m)"
            )
        placements.append({"unit": u, "s0": s0, "s1": s1, "sill": sill, "height": oh})

    return placements, notes


def _validate_out_path(path: str | Path) -> Path:
    """Validate output path is safe: reject traversal beyond cwd.

    Allows absolute paths (user-intended destinations).
    For relative paths: resolves symlinks and rejects if the result escapes cwd.
    """
    path = Path(path)
    if not path.is_absolute():
        resolved = (Path.cwd() / path).resolve()
        cwd_resolved = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd_resolved)
        except ValueError:
            raise ValueError(
                f"Output path '{path}' resolves to '{resolved}' which escapes "
                f"the working directory '{cwd_resolved}'. Rejecting to prevent "
                "path traversal."
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
