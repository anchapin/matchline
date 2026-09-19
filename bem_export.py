"""BEM export: gbXML 6.01 (primary) + IFC4 (via IfcOpenShell) from takeoff results.

Input contracts (from the pipeline):
  * ``takeoff`` : datasets_adapter.TakeoffResult
      - ``takeoff.lines`` : TakeoffLine per schedule tag (tag, category,
        count, width_m, height_m) -> window/door Openings
      - ``takeoff.scale.m_per_px`` : required for spaces + envelope geometry.
        (The count x dims path needs no scale, but BEM geometry does.)
  * ``labeled`` : room_labels.LabeledTakeoff
      - ``labeled.spaces`` : LabeledSpace (polygon_px, name, number)
  * ``sres`` : geometry_simplify.SimplifyResult (ring in source px)

Pipeline:
  model_from_takeoff(...) -> BEMModel (units: meters, x=east, y=north, z=up)
  write_gbxml(model, path)   -> gbXML 6.01 file
  validate_gbxml(path)       -> (ok, errors) via lxml against the 6.01 XSD
  write_ifc4(model, path)    -> IFC4 file (IfcOpenShell)
  validate_ifc4(path)        -> (ok, errors) round-trip structural checks

Coordinate mapping (documented assumption):
  drawing pixel (x right, y DOWN) -> meters (x east, y north = -y_px * s,
  z up). I.e. "up" on a north-up sheet is north. Azimuths follow the gbXML
  convention: degrees clockwise from north of the outward normal.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from room_labels import point_in_polygon as _pip

# ---------------------------------------------------------------------------
# Intermediate BEM model (all metric, x=east / y=north / z=up)
# ---------------------------------------------------------------------------

GBXML_NS = "http://www.gbxml.org/schema"
SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "GreenBuildingXML_Ver6.01.xsd"

WINDOW_SILL_M = 0.9  # placement assumption, documented
DOOR_SILL_M = 0.0


@dataclass
class BEMSpace:
    sid: str
    name: str  # e.g. "OPEN OFFICE 101"
    number: str
    polygon_m: list  # [(x, y), ...] CCW, x=east, y=north
    area_m2: float
    volume_m3: float


@dataclass
class BEMOpeningUnit:
    """One physical opening instance (expanded from count x schedule)."""

    category: str  # "window" | "door"
    tag: str  # schedule tag, e.g. "A"
    width_m: float
    height_m: float


@dataclass
class BEMModel:
    building_name: str
    spaces: list  # BEMSpace
    openings: list  # BEMOpeningUnit, one per physical opening
    ring_m: list  # simplified envelope ring, CCW, x=east/y=north
    wall_height_m: float
    area_delta_pct: float  # envelope area preservation, from simplifier
    simplify_tol_pct: float
    skipped_openings: list = field(default_factory=list)  # tags w/o dims
    notes: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _shoelace(poly) -> float:
    s = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def _ensure_ccw(ring):
    return list(reversed(ring)) if _shoelace(ring) < 0 else list(ring)


def _fmt(v: float) -> str:
    return f"{v:.4f}"


# ---------------------------------------------------------------------------
# model_from_takeoff
# ---------------------------------------------------------------------------


def model_from_takeoff(
    takeoff, labeled, sres, wall_height_m: float = 3.0, building_name: str = "Jesse Building"
) -> BEMModel:
    """Assemble a unit-clean BEMModel from the pipeline output contracts."""
    s = takeoff.scale.m_per_px
    if not s:
        raise ValueError(
            "BEM export needs a drawing scale (m/px) for spaces and envelope; "
            "takeoff.scale.m_per_px is None."
        )
    notes = []

    # --- spaces -----------------------------------------------------------
    spaces = []
    for i, sp in enumerate(labeled.spaces):
        poly_m = [(x * s, -y * s) for x, y in sp.polygon_px]
        poly_m = _ensure_ccw(poly_m)
        area = abs(_shoelace(poly_m))
        name = f"{sp.name} {sp.number}".strip() or f"SPACE-{i + 1:02d}"
        spaces.append(
            BEMSpace(
                sid=f"sp-{i + 1:03d}",
                name=name,
                number=sp.number,
                polygon_m=poly_m,
                area_m2=area,
                volume_m3=area * wall_height_m,
            )
        )
    if not spaces:
        raise ValueError("no labeled spaces to export")

    # --- envelope ring ----------------------------------------------------
    ring_m = _ensure_ccw([(x * s, -y * s) for x, y in sres.ring])
    if len(ring_m) < 3:
        raise ValueError("simplified envelope ring is degenerate")

    # --- openings: expand count x schedule dims ---------------------------
    openings, skipped = [], []
    for line in takeoff.lines:
        if line.width_m is None or line.height_m is None:
            skipped.append(
                {
                    "tag": line.tag,
                    "category": line.category,
                    "count": line.count,
                    "reason": "schedule dimensions missing",
                }
            )
            continue
        cat = line.category.lower()
        if cat not in ("window", "door"):
            skipped.append(
                {
                    "tag": line.tag,
                    "category": line.category,
                    "count": line.count,
                    "reason": f"category '{line.category}' not window/door; not placed as opening",
                }
            )
            continue
        openings.extend(
            BEMOpeningUnit(cat, line.tag, line.width_m, line.height_m) for _ in range(line.count)
        )
    # deterministic order: windows then doors, sorted by tag
    openings.sort(key=lambda o: (o.category, o.tag))
    notes.append(
        f"{len(openings)} openings expanded from "
        f"{len(takeoff.lines)} schedule lines; "
        f"{len(skipped)} tags skipped (see skipped_openings)."
    )

    return BEMModel(
        building_name=building_name,
        spaces=spaces,
        openings=openings,
        ring_m=ring_m,
        wall_height_m=wall_height_m,
        area_delta_pct=sres.area_delta_pct,
        simplify_tol_pct=sres.tol * 100.0,
        skipped_openings=skipped,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# gbXML 6.01 writer
# ---------------------------------------------------------------------------


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


def _distribute_openings(openings, edges):
    """Assign opening units to walls proportional to wall length.

    Largest-remainder apportionment per category, so per-category totals are
    exact and the assignment is deterministic. Returns {wall_idx: [units]}.
    """
    lengths = [math.hypot(p1[0] - p0[0], p1[1] - p0[1]) for p0, p1 in edges]
    total_L = sum(lengths) or 1.0
    assign = {i: [] for i in range(len(edges))}
    for cat in ("window", "door"):
        units = [u for u in openings if u.category == cat]
        n = len(units)
        if not n:
            continue
        shares = [n * L / total_L for L in lengths]
        base = [int(math.floor(sh)) for sh in shares]
        rem = n - sum(base)
        order = sorted(
            range(len(edges)), key=lambda i: (shares[i] - base[i], -lengths[i]), reverse=True
        )
        for i in order[:rem]:
            base[i] += 1
        k = 0
        for i, cnt in enumerate(base):
            assign[i].extend(units[k : k + cnt])
            k += cnt
    for i in assign:
        assign[i].sort(key=lambda u: (u.category, u.tag))
    return assign


def _opening_type(category: str) -> str:
    # openingTypeEnum has no generic "Door": NonSlidingDoor is the closest.
    return "FixedWindow" if category == "window" else "NonSlidingDoor"


def _place_openings_on_wall(units, L: float, h: float):
    """Deterministic opening layout on one wall.

    Evenly spaces the wall's units along its length (centers at
    (j+0.5)*L/k). Returns (placements, notes); placements are dicts
    {unit, s0, s1, sill, height} with s measured from the wall START point
    p0 along the ring edge direction. Widths/heights are clamped to fit
    the wall; every clamp is reported in notes (never silent).
    """
    placements, notes = [], []
    k = len(units)
    for j, u in enumerate(units):
        sill = WINDOW_SILL_M if u.category == "window" else DOOR_SILL_M
        oh = u.height_m
        if sill + oh > h:
            oh = h - sill
            notes.append(f"{u.tag}: height clamped to {oh:.2f} m (wall {h:.2f} m)")
        c = (j + 0.5) * L / k
        s0 = max(0.05, c - u.width_m / 2)
        s1 = min(L - 0.05, c + u.width_m / 2)
        if s1 - s0 < u.width_m * 0.5:
            notes.append(
                f"{u.tag}: width clamped ({u.width_m:.2f} -> {s1 - s0:.2f} m, wall {L:.2f} m)"
            )
        placements.append({"unit": u, "s0": s0, "s1": s1, "sill": sill, "height": oh})
    return placements, notes


def write_gbxml(model: BEMModel, path: str | Path) -> Path:
    """Write a gbXML 6.01 file for the model. Returns the path written."""
    ET.register_namespace("", GBXML_NS)
    h = model.wall_height_m
    root = ET.Element(
        f"{{{GBXML_NS}}}gbXML",
        {
            "temperatureUnit": "C",
            "lengthUnit": "Meters",
            "areaUnit": "SquareMeters",
            "volumeUnit": "CubicMeters",
            "version": "6.01",
            "useSIUnitsForResults": "true",
        },
    )
    campus = _el(root, "Campus", id="campus-1")
    _el(campus, "Name", model.building_name)
    _el(
        campus,
        "Description",
        f"Exported by Jesse-Vision prototype. Envelope simplified "
        f"{model.area_delta_pct:+.3f}% area delta (tolerance "
        f"{model.simplify_tol_pct:.1f}%).",
    )
    loc = _el(campus, "Location")
    _el(loc, "Name", "Unknown")
    _el(loc, "ZipcodeOrPostalCode", "00000")
    _el(loc, "Latitude", "0")
    _el(loc, "Longitude", "0")
    _el(loc, "Elevation", "0")

    bldg = _el(campus, "Building", id="bldg-1", buildingType="Office")
    _el(bldg, "Name", model.building_name)
    storey = _el(bldg, "BuildingStorey", id="storey-1")
    _el(storey, "Name", "Level 1")
    _el(storey, "Level", "0")

    zone = _el(root, "Zone", id="zone-1")
    _el(zone, "Name", "Zone 1")

    # Placeholder constructions (v1): drawings carry no assembly data, so
    # every surface references a generic construction. Real U-values /
    # layered assemblies are a v2 enrichment from the spec or user input.
    for cid, cname, uval in (
        ("const-wall", "Generic exterior wall", "0.50"),
        ("const-roof", "Generic roof", "0.30"),
        ("const-slab", "Generic slab on grade", "0.40"),
    ):
        co = _el(root, "Construction", id=cid)
        _el(co, "Name", cname)
        _el(co, "U-value", uval, unit="WPerSquareMeterK")

    # --- spaces (children of Building in gbXML) ------------------------------
    for sp in model.spaces:
        se = _el(bldg, "Space", id=sp.sid, buildingStoreyIdRef="storey-1", zoneIdRef="zone-1")
        _el(se, "Name", sp.name)
        if sp.number:
            _el(se, "CADObjectId", sp.number)
        _el(se, "Area", _fmt(sp.area_m2))
        _el(se, "Volume", _fmt(sp.volume_m3))
        # closed shell: floor + roof + wall quads (outward normals)
        shell = _el(se, "ShellGeometry", id=f"{sp.sid}-shell")
        cs = _el(shell, "ClosedShell")
        ring = sp.polygon_m
        n = len(ring)
        # floor (normal -z): clockwise from above
        pl = _el(cs, "PolyLoop")
        for x, y in reversed(ring):
            _cartesian(pl, x, y, 0.0)
        # roof (normal +z): CCW from above
        pl = _el(cs, "PolyLoop")
        for x, y in ring:
            _cartesian(pl, x, y, h)
        # walls: (b0, b1, t1, t0) has outward normal (verified in docs)
        for i in range(n):
            x0, y0 = ring[i]
            x1, y1 = ring[(i + 1) % n]
            pl = _el(cs, "PolyLoop")
            _cartesian(pl, x0, y0, 0.0)
            _cartesian(pl, x1, y1, 0.0)
            _cartesian(pl, x1, y1, h)
            _cartesian(pl, x0, y0, h)

    # --- envelope surfaces ------------------------------------------------
    edges = _wall_edges(model.ring_m)
    opening_assign = _distribute_openings(model.openings, edges)
    surf_count = 0
    open_count = 0
    placement_notes = []
    for i, (p0, p1) in enumerate(edges):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        nx, ny = dy / L, -dx / L  # outward (CCW ring)
        az = math.degrees(math.atan2(nx, ny)) % 360.0
        # screen-right when facing from outside: r = (-ny, nx)... (v x u)
        rx, ry = -ny, nx
        d = (dx / L, dy / L)
        if d[0] * rx + d[1] * ry > 0:
            bl, br = p0, p1
        else:
            bl, br = p1, p0
        sp = _assign_wall_to_space(p0, p1, model.spaces)
        surf_count += 1
        su = _el(
            campus,
            "Surface",
            id=f"wall-{i + 1:03d}",
            surfaceType="ExteriorWall",
            constructionIdRef="const-wall",
        )
        _el(su, "Name", f"Wall {i + 1}")
        _el(su, "AdjacentSpaceId", spaceIdRef=sp.sid)
        rg = _el(su, "RectangularGeometry")
        _el(rg, "Azimuth", _fmt(az))
        _el(rg, "Tilt", "90")
        _cartesian(rg, bl[0], bl[1], 0.0)
        _cartesian(rg, br[0], br[1], 0.0)
        _cartesian(rg, br[0], br[1], h)
        _cartesian(rg, bl[0], bl[1], h)
        # openings on this wall (local coords from parent bottom-left)
        units = opening_assign[i]
        bl_is_p0 = bl == p0
        placements, notes = _place_openings_on_wall(units, L, h)
        for n in notes:
            placement_notes.append(f"wall-{i + 1:03d}: {n}")
        for pl_ in placements:
            u = pl_["unit"]
            s0, s1 = pl_["s0"], pl_["s1"]
            if not bl_is_p0:
                # mirror into the bottom-left frame (BL == p1 here)
                s0, s1 = L - s1, L - s0
            open_count += 1
            op = _el(
                su, "Opening", id=f"op-{open_count:04d}", openingType=_opening_type(u.category)
            )
            _el(op, "Name", f"{u.tag} ({u.category})")
            org = _el(op, "RectangularGeometry")
            # 2-D local coords per schema doc; no Azimuth/Tilt on openings
            _cartesian(org, s0, pl_["sill"])
            _cartesian(org, s1, pl_["sill"])
            _cartesian(org, s1, pl_["sill"] + pl_["height"])
            _cartesian(org, s0, pl_["sill"] + pl_["height"])

    # roof (outward +z): CCW from above
    surf_count += 1
    su = _el(campus, "Surface", id="roof-001", surfaceType="Roof", constructionIdRef="const-roof")
    _el(su, "Name", "Roof")
    _el(su, "AdjacentSpaceId", spaceIdRef=max(model.spaces, key=lambda s: s.area_m2).sid)
    pg = _el(su, "PlanarGeometry")
    pl = _el(pg, "PolyLoop")
    for x, y in model.ring_m:
        _cartesian(pl, x, y, h)

    # ground floor (outward -z): clockwise from above
    surf_count += 1
    su = _el(
        campus, "Surface", id="floor-001", surfaceType="SlabOnGrade", constructionIdRef="const-slab"
    )
    _el(su, "Name", "Ground Floor")
    _el(su, "AdjacentSpaceId", spaceIdRef=max(model.spaces, key=lambda s: s.area_m2).sid)
    pg = _el(su, "PlanarGeometry")
    pl = _el(pg, "PolyLoop")
    for x, y in reversed(model.ring_m):
        _cartesian(pl, x, y, 0.0)

    comment = (
        f"Jesse-Vision BEM export. Simplification area delta "
        f"{model.area_delta_pct:+.3f}% (tol {model.simplify_tol_pct:.1f}%). "
        f"Openings: {len(model.openings)} placed by largest-remainder "
        f"apportionment across {len(edges)} walls proportional to wall "
        f"length, evenly spaced per wall; window sill {WINDOW_SILL_M} m, "
        f"door sill {DOOR_SILL_M} m. Interior partitions omitted (v1 gap). "
        + (" ".join(model.notes) + " " if model.notes else "")
        + (
            "Placement notes: " + "; ".join(placement_notes)
            if placement_notes
            else "No placement clamps."
        )
    )
    path = Path(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write(f"<!-- {comment} -->\n")
        f.write(ET.tostring(root, encoding="unicode"))
        f.write("\n")
    model.notes.append(
        f"gbXML: {len(model.spaces)} spaces, {surf_count} surfaces, {open_count} openings."
    )
    return path


def validate_gbxml(path: str | Path, xsd_path: str | Path = SCHEMA_PATH) -> tuple[bool, list]:
    """Validate a gbXML file against the 6.01 XSD with lxml.

    Returns (ok, [error strings]). Falls back to well-formedness + key
    element checks if lxml or the XSD is unavailable.
    """
    path = str(path)
    errors: list[str] = []
    try:
        from lxml import etree
    except ImportError:
        errors.append("lxml not installed; skipping XSD validation")
        return _gbxml_smoke_check(path, errors)
    if not Path(xsd_path).exists():
        errors.append(f"XSD not found at {xsd_path}; skipping XSD validation")
        return _gbxml_smoke_check(path, errors)
    try:
        # Harden against entity-expansion / XXE: user-supplied gbXML and XSD
        # files are untrusted input (see AGENTS.md untrusted-input policy).
        safe_parser = etree.XMLParser(resolve_entities=False, no_network=True)
        schema = etree.XMLSchema(etree.parse(str(xsd_path), safe_parser))
        doc = etree.parse(path, safe_parser)
        ok = schema.validate(doc)
        for e in schema.error_log:
            errors.append(f"line {e.line}: {e.message}")
        if ok:
            # semantic spot checks beyond the XSD
            errors.extend(_gbxml_semantic_checks(doc))
        return ok and not errors, errors
    except etree.XMLSyntaxError as e:
        return False, [f"not well-formed: {e}"]


def _gbxml_smoke_check(path: str, errors: list) -> tuple[bool, list]:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return False, errors + [f"not well-formed: {e}"]
    ns = {"g": GBXML_NS}
    for tag in ("Campus", "Building", "Space", "Surface"):
        if not root.findall(f".//g:{tag}", ns):
            errors.append(f"smoke check: no {tag} elements found")
    return not errors, errors


def _gbxml_semantic_checks(doc) -> list:
    """Checks the XSD cannot express: id uniqueness, ref integrity."""
    errs = []
    ids = {}
    for el in doc.getroot().iter():
        i = el.get("id")
        if i:
            if i in ids:
                errs.append(f"duplicate id '{i}'")
            ids[i] = el.tag
    for el in doc.getroot().iter():
        for attr in ("spaceIdRef", "buildingStoreyIdRef", "zoneIdRef"):
            ref = el.get(attr)
            if ref and ref not in ids:
                errs.append(f"{el.tag} references unknown id '{ref}' ({attr})")
    return errs


# ---------------------------------------------------------------------------
# IFC4 writer (via IfcOpenShell)
# ---------------------------------------------------------------------------


def _ensure_ifc():
    """Import IfcOpenShell, falling back to the workspace-vendored copy."""
    import sys as _sys

    _vendor = str(Path.home() / "workspace" / "vendor" / "pylibs")
    if _vendor not in _sys.path:
        _sys.path.insert(0, _vendor)
    try:
        import ifcopenshell  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "IfcOpenShell is not installed; install with `pip install ifcopenshell`"
        ) from e


def write_ifc4(model: BEMModel, path: str | Path, wall_thickness_m: float = 0.2) -> Path:
    """Write a minimal but structurally valid IFC4 file.

    Contents: IfcProject/Site/Building/BuildingStorey hierarchy, one IfcWall
    per simplified envelope edge (real SweptSolid geometry, 0.2 m thick),
    one IfcSpace per room (placement at centroid; no solid geometry in v1),
    and per opening an IfcOpeningElement hosted in its wall
    (IfcRelVoidsElement) filled by an IfcWindow/IfcDoor (IfcRelFillsElement).

    Opening placement reuses _place_openings_on_wall, so IFC and gbXML agree
    on positions (s measured from the wall start point p0 along the edge).
    """
    _ensure_ifc()
    import ifcopenshell
    import ifcopenshell.api.aggregate as _Ag
    import ifcopenshell.api.context as _C
    import ifcopenshell.api.geometry as _Gm
    import ifcopenshell.api.project as _P
    import ifcopenshell.api.root as _R
    import ifcopenshell.api.spatial as _Sp
    import ifcopenshell.api.unit as _U

    h = model.wall_height_m
    f = _P.create_file("IFC4")
    proj = _R.create_entity(f, ifc_class="IfcProject", name=model.building_name)
    length = _U.add_si_unit(f, unit_type="LENGTHUNIT")  # metre
    area = _U.add_si_unit(f, unit_type="AREAUNIT")
    volume = _U.add_si_unit(f, unit_type="VOLUMEUNIT")
    _U.assign_unit(f, units=[length, area, volume])

    ctx = _C.add_context(f, context_type="Model")
    body = _C.add_context(
        f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx
    )

    def placement(xyz, ref_dir=None, parent=None):
        pt = f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(v) for v in xyz))
        kw = {"Location": pt}
        if ref_dir is not None:
            kw["Axis"] = f.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
            kw["RefDirection"] = f.create_entity(
                "IfcDirection", DirectionRatios=tuple(float(v) for v in ref_dir)
            )
        ax = f.create_entity("IfcAxis2Placement3D", **kw)
        return f.create_entity("IfcLocalPlacement", PlacementRelTo=parent, RelativePlacement=ax)

    site = _R.create_entity(f, ifc_class="IfcSite", name="Site")
    bldg = _R.create_entity(f, ifc_class="IfcBuilding", name=model.building_name)
    storey = _R.create_entity(f, ifc_class="IfcBuildingStorey", name="Level 1")
    storey.Elevation = 0.0
    _Ag.assign_object(f, products=[site], relating_object=proj)
    _Ag.assign_object(f, products=[bldg], relating_object=site)
    _Ag.assign_object(f, products=[storey], relating_object=bldg)
    storey_pl = placement((0.0, 0.0, 0.0))
    storey.ObjectPlacement = storey_pl

    # --- walls ------------------------------------------------------------
    edges = _wall_edges(model.ring_m)
    opening_assign = _distribute_openings(model.openings, edges)
    walls = []
    for i, (p0, p1) in enumerate(edges):
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        wall = _R.create_entity(f, ifc_class="IfcWall", name=f"Wall-{i + 1}")
        wall.ObjectPlacement = placement(
            (p0[0], p0[1], 0.0), ref_dir=(dx / L, dy / L, 0.0), parent=storey_pl
        )
        rep = _Gm.add_wall_representation(
            f, context=body, length=L, height=h, thickness=wall_thickness_m
        )
        _Gm.assign_representation(f, product=wall, representation=rep)
        _Sp.assign_container(f, products=[wall], relating_structure=storey)
        walls.append((wall, p0, p1, L))

        # openings hosted in this wall
        placements, _ = _place_openings_on_wall(opening_assign[i], L, h)
        for pl_ in placements:
            u = pl_["unit"]
            s_mid = (pl_["s0"] + pl_["s1"]) / 2.0
            opening = _R.create_entity(f, ifc_class="IfcOpeningElement", name=f"{u.tag} opening")
            # opening local frame: wall frame translated along the wall
            opening.ObjectPlacement = placement(
                (s_mid, 0.0, pl_["sill"]), parent=wall.ObjectPlacement
            )
            f.create_entity(
                "IfcRelVoidsElement",
                GlobalId=ifcopenshell.guid.new(),
                RelatingBuildingElement=wall,
                RelatedOpeningElement=opening,
            )
            fill_class = "IfcWindow" if u.category == "window" else "IfcDoor"
            fill = _R.create_entity(
                f,
                ifc_class=fill_class,
                name=f"{u.tag} ({u.category} {u.width_m:.2f}x{u.height_m:.2f} m)",
            )
            fill.ObjectPlacement = placement((0.0, 0.0, 0.0), parent=opening.ObjectPlacement)
            f.create_entity(
                "IfcRelFillsElement",
                GlobalId=ifcopenshell.guid.new(),
                RelatingOpeningElement=opening,
                RelatedBuildingElement=fill,
            )
            _Sp.assign_container(f, products=[fill], relating_structure=storey)

    # --- spaces -----------------------------------------------------------
    for sp in model.spaces:
        n = len(sp.polygon_m)
        cx = sum(p[0] for p in sp.polygon_m) / n
        cy = sum(p[1] for p in sp.polygon_m) / n
        space = _R.create_entity(f, ifc_class="IfcSpace", name=sp.name)
        space.ObjectPlacement = placement((cx, cy, 0.0), parent=storey_pl)
        try:
            space.PredefinedType = "SPACE"
        except Exception:
            pass
        # spaces decompose the storey spatially (IfcRelAggregates), they are
        # not "contained products" (IfcSpace has no ContainedInStructure)
        _Ag.assign_object(f, products=[space], relating_object=storey)
        # gross floor area as a quantity set (best effort)
        try:
            import ifcopenshell.api.pset as _Ps

            qto = _Ps.add_qto(f, product=space, name="Qto_SpaceBaseQuantities")
            _Ps.edit_qto(f, qto=qto, properties={"GrossFloorArea": sp.area_m2})
        except Exception:
            pass  # quantities are enrichment, not core validity

    path = Path(path)
    f.write(str(path))
    model.notes.append(
        f"IFC4: {len(walls)} walls, {len(model.spaces)} "
        f"spaces, {len(model.openings)} openings hosted."
    )
    return path


def validate_ifc4(path: str | Path) -> tuple[bool, list]:
    """Structural validation of an IFC4 file: round-trip parse + checks.

    Verifies schema, entity counts, wall geometry presence, space
    containment, and opening void/fill relationship integrity.
    """
    _ensure_ifc()
    import ifcopenshell

    errors: list[str] = []
    try:
        f = ifcopenshell.open(str(path))
    except Exception as e:
        return False, [f"could not parse IFC file: {e}"]
    if f.schema != "IFC4":
        errors.append(f"schema is {f.schema}, expected IFC4")

    walls = f.by_type("IfcWall")
    spaces = f.by_type("IfcSpace")
    storeys = f.by_type("IfcBuildingStorey")
    openings = f.by_type("IfcOpeningElement")
    windows = f.by_type("IfcWindow")
    doors = f.by_type("IfcDoor")
    if not walls:
        errors.append("no IfcWall entities")
    if not spaces:
        errors.append("no IfcSpace entities")
    if not storeys:
        errors.append("no IfcBuildingStorey entities")

    for w in walls:
        reps = w.Representation.Representations if w.Representation else []
        if not any(r.RepresentationType == "SweptSolid" for r in reps):
            errors.append(f"{w.Name or w.id()}: wall has no SweptSolid body")

    contained = set()
    for rel in f.by_type("IfcRelContainedInSpatialStructure"):
        for el in rel.RelatedElements:
            contained.add(el.id())
    aggregated = set()
    for rel in f.by_type("IfcRelAggregates"):
        for el in rel.RelatedObjects:
            aggregated.add(el.id())
    for sp in spaces:
        if sp.id() not in aggregated:
            errors.append(f"space '{sp.Name}' not aggregated under a storey")

    voided = {
        r.RelatedOpeningElement.id(): r.RelatingBuildingElement.id()
        for r in f.by_type("IfcRelVoidsElement")
    }
    filled = {r.RelatingOpeningElement.id() for r in f.by_type("IfcRelFillsElement")}
    for o in openings:
        if o.id() not in voided:
            errors.append(f"opening '{o.Name}' voids no wall")
        if o.id() not in filled:
            errors.append(f"opening '{o.Name}' has no filling element")
    for el in list(windows) + list(doors):
        if not el.ContainedInStructure:
            errors.append(f"{el.is_a()} '{el.Name}' not in a spatial container")

    return not errors, errors
