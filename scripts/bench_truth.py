#!/usr/bin/env python3
"""Independent benchmark ground truth, read straight from the IFC (#1165).

Usage:
    python scripts/bench_truth.py --config scripts/benchmarks.json [--force]

For each benchmark building with an arch IFC on disk this writes
``truth.json`` next to the IFC (outside the repo, like the IFC itself). The
benchmark scores matchline's IFC path against that frozen file instead of
against a second ``import_ifc`` of the same file, which compared the importer
with itself (area error 0.0% by construction, wall length error hard-coded).

Truth is read with ifcopenshell only. This module must never import matchline's
IFC reader (``ifc.ifc_import``); ``tests/test_bench_truth.py`` pins that.

- rooms: every ``IfcSpace``, keyed by GlobalId. Area is
  ``Qto_SpaceBaseQuantities.NetFloorArea`` (falling back to ``GrossFloorArea``),
  else the downward-facing area of the space's tessellated body from
  ``ifcopenshell.geom``. ``area_source`` records which one was used.
- exterior wall length: walls with ``Pset_WallCommon.IsExternal`` True; length
  from ``Qto_WallBaseQuantities.Length``, else the 2D length of the wall's
  ``Axis`` representation, else the longest plan extent of the wall's
  tessellated body from ``ifcopenshell.geom`` (the same geometry policy the
  room areas use). A file with no wall flagged external has no exterior wall
  truth (``null`` plus a reason), never 0.
- units: quantities are stated in the file's units, so they are scaled to
  metres / square metres before they are read as truth: a length by the
  file's length scale and a quantity that declares its own ``Unit``, an
  area by the project's ``AREAUNIT`` (falling back to the length scale
  squared). A millimetre file's 10 m wall reads as 10 m, not 10000 m
  (#1363). Conversion-based units (inch, foot, square foot) are read
  through their conversion factor and keep their own dimension: an areal
  factor is used as it stands, a length factor is squared for an area
  (#1372).
- windows / doors: raw ``by_type`` counts; window area is the product of
  the two largest extents of the window's tessellated body from
  ``ifcopenshell.geom`` (the same geometry policy the room areas use),
  falling back to ``OverallWidth * OverallHeight`` where the body is not
  tessellable and both are stated: stated attributes can lie (gni-49
  carries the frame thickness, 0.1795 m, in the width field and swaps the
  two inconsistently, #1220).

``truth.json`` carries the IFC's sha256. The benchmark refuses a truth file
whose sha256 no longer matches the IFC, so truth cannot drift silently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Iterable
from pathlib import Path

TRUTH_SCHEMA = "matchline.bench_truth/2"
TRUTH_FILENAME = "truth.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _psets(el) -> dict:
    import ifcopenshell.util.element as ue

    try:
        return ue.get_psets(el)
    except Exception:
        return {}


def _qto_value(psets: dict, qset: str, names: tuple[str, ...]) -> float | None:
    q = psets.get(qset) or {}
    for n in names:
        v = q.get(n)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _length_scale(f) -> float:
    import ifcopenshell.util.unit as uu

    try:
        return float(uu.calculate_unit_scale(f))
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# Units: quantities are stated in the file's units, not in metres (#1363)
# ---------------------------------------------------------------------------

# Metres per SI prefix (the prefix alone; the exponent is applied by the
# caller, so a MILLI area unit is (1e-3) ** 2).
_SI_PREFIX = {
    "": 1.0,
    "YOTTA": 1e24,
    "ZETTA": 1e21,
    "EXA": 1e18,
    "PETA": 1e15,
    "TERA": 1e12,
    "GIGA": 1e9,
    "MEGA": 1e6,
    "KILO": 1e3,
    "HECTO": 1e2,
    "DECA": 1e1,
    "DECI": 1e-1,
    "CENTI": 1e-2,
    "MILLI": 1e-3,
    "MICRO": 1e-6,
    "NANO": 1e-9,
    "PICO": 1e-12,
    "FEMTO": 1e-15,
    "ATTO": 1e-18,
    "ZEPTO": 1e-21,
    "YOCTO": 1e-24,
}


# The dimension of an IFC unit type, as the power its scale is raised to.
_UNIT_DIMENSION = {"LENGTHUNIT": 1, "AREAUNIT": 2, "VOLUMEUNIT": 3}


def _measure_value(value) -> float | None:
    """The numeric payload of an ``IfcMeasureValue``.

    A conversion factor's ``ValueComponent`` is an IfcMeasureValue
    select: ifcopenshell surfaces it as a measure entity (e.g.
    ``IfcLengthMeasure``) whose numeric payload is ``wrappedValue``, so
    float() on the entity itself raises TypeError and the whole factor
    used to be swallowed to None (#1372). A plain number is accepted too.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    wrapped = getattr(value, "wrappedValue", None)
    if isinstance(wrapped, (int, float)) and not isinstance(wrapped, bool):
        return float(wrapped)
    return None


def _si_component_scale(unit) -> float:
    """The scale of the SI unit a conversion factor is stated in.

    Only a length component carries a prefix scale (25.4 with a
    millimetre component is 0.0254 m, one inch); an area or volume
    component is already stated in its own dimension and counts as 1.0.
    """
    if (
        unit is not None
        and unit.is_a("IfcSIUnit")
        and getattr(unit, "UnitType", None) == "LENGTHUNIT"
    ):
        return _SI_PREFIX.get((unit.Prefix or ""), 1.0)
    return 1.0


def _conversion_factor_scale(unit) -> float | None:
    """The scale of an ``IfcConversionBasedUnit``'s conversion factor.

    The factor states how much one of this unit is worth in the
    component's unit: 25.4 with a millimetre component is 0.0254 m for
    an inch, and 0.09290304 with a square-metre component is
    0.09290304 m^2 for a square foot (#1372). None when the factor
    cannot be read.
    """
    cf = getattr(unit, "ConversionFactor", None)
    if cf is None:
        return None
    value = _measure_value(getattr(cf, "ValueComponent", None))
    if value is None:
        return None
    return value * _si_component_scale(getattr(cf, "UnitComponent", None))


def _project_unit_scale(f, unit_type: str, exponent: int) -> float | None:
    """Scale to SI for the project's declared unit of ``unit_type``.

    None when the file declares no such unit, so the caller can fall back
    to the length scale raised to ``exponent`` (the historical behaviour
    for files that declare only a length unit).
    """
    if f is None:
        return None
    try:
        ua = f.by_type("IfcUnitAssignment")[0]
    except Exception:
        return None
    for u in ua.Units or []:
        if getattr(u, "UnitType", None) != unit_type:
            continue
        if u.is_a("IfcSIUnit"):
            return _SI_PREFIX.get((u.Prefix or ""), 1.0) ** exponent
        if u.is_a("IfcConversionBasedUnit"):
            factor = _conversion_factor_scale(u)
            if factor is None:
                return None
            if _UNIT_DIMENSION.get(getattr(u, "UnitType", None), 1) == 1:
                # a length factor: the caller's exponent applies to it
                return factor**exponent
            # an area or volume factor already carries its own dimension,
            # so it is used as it stands (#1372)
            return factor
    return None


def _quantity_unit_scale(q) -> float | None:
    """Scale of a quantity's own ``Unit``, or None when it carries none.

    A quantity that declares its own unit is stated in that unit and
    overrides the project unit for that quantity (#1351, #1360).

    An ``IfcConversionBasedUnit`` (inch, foot, square foot -- the common
    US-imperial shape) is resolved through its ``ConversionFactor``:
    the factor's value times the scale of the factor's own unit
    component (#1372).
    """
    unit = getattr(q, "Unit", None)
    if unit is None:
        return None
    if unit.is_a("IfcSIUnit"):
        return _SI_PREFIX.get((unit.Prefix or ""), 1.0)
    if unit.is_a("IfcConversionBasedUnit"):
        return _conversion_factor_scale(unit)
    return None


def _quantity_scale(q, exponent: int, fallback: float) -> float:
    """Scale for one quantity value, honouring the quantity's own unit.

    A quantity carrying its own ``Unit`` overrides the project unit for
    that quantity (#1351, #1360); without one, ``fallback`` (the
    project-derived scale) applies. ``exponent`` is the power the unit
    is raised to: 1 for lengths, 2 for areas, 3 for volumes.

    A conversion-based unit (#1364) already carries its own dimension:
    its factor reads metres per inch, or m^2 per square foot. Such a
    factor is therefore used as it stands for an area/volume quantity
    and only squared/cubed when it is a *length* factor (a foot used
    for an area quantity is a square foot) (#1372).
    """
    unit = getattr(q, "Unit", None)
    unit_scale = _quantity_unit_scale(q)
    if unit_scale is None:
        return fallback
    if unit is not None and unit.is_a("IfcConversionBasedUnit"):
        if _UNIT_DIMENSION.get(getattr(unit, "UnitType", None), 1) == 1:
            return unit_scale**exponent
        return unit_scale
    return unit_scale**exponent


def _qto_quantity(el, qset: str, names: tuple[str, ...]):
    """The IfcQuantity entity named one of ``names`` in ``qset`` on ``el``.

    The element's own quantity sets win over the ones inherited from its
    type -- the same precedence ``get_psets`` applies. None when the
    element carries no such quantity.
    """

    def _find(definitions):
        for psd in definitions or []:
            if psd is None or not psd.is_a("IfcElementQuantity"):
                continue
            if psd.Name != qset:
                continue
            for q in psd.Quantities or []:
                if q.Name in names:
                    return q
        return None

    for rel in getattr(el, "IsDefinedBy", None) or []:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        q = _find([rel.RelatingPropertyDefinition])
        if q is not None:
            return q
    try:
        import ifcopenshell.util.element as ue

        el_type = ue.get_type(el)
    except Exception:
        el_type = None
    if el_type is not None:
        return _find(getattr(el_type, "HasPropertySets", None))
    return None


def _quantity_value(q) -> float | None:
    """The numeric value of a quantity entity."""
    for attr in ("LengthValue", "AreaValue", "VolumeValue", "CountValue", "WeightValue"):
        v = getattr(q, attr, None)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _qto_measure(
    el,
    psets: dict,
    qset: str,
    names: tuple[str, ...],
    exponent: int,
    fallback_scale: float,
) -> float | None:
    """A Qto quantity read in SI (metres / square metres).

    The raw quantity is stated in the file's units, so it is scaled before
    it can be compared with a model in metres: a wall length of 10000 in a
    millimetre file is 10 m, not 10000 m (#1363). ``fallback_scale`` is the
    file-level scale for that kind of quantity (the length scale for
    lengths, the project's AREAUNIT for areas), and a quantity carrying its
    own ``Unit`` overrides it. A conversion-based unit keeps its own
    dimension: an areal factor is used as it stands, a length factor is
    raised to ``exponent`` (#1372).
    """
    q = _qto_quantity(el, qset, names)
    raw = _quantity_value(q) if q is not None else None
    if raw is None or raw <= 0:
        raw = _qto_value(psets, qset, names)
    if raw is None:
        return None
    if q is None:
        return raw * fallback_scale
    return raw * _quantity_scale(q, exponent, fallback_scale)


def _geom_floor_area(el) -> float | None:
    """Unsigned area (m2) of the horizontal faces at the body's lowest level.

    A space's floor is its lowest horizontal surface, whatever wound it:
    closed solids put their bottom face there pointing down, and the face-set
    shells whose ceiling is wound downward too (#1218: the duplex hallway's
    ceiling tessellates facing down just like its floor) are covered by
    taking only the lowest level. Summing every downward-facing face instead
    read that hallway twice and credited the molio ledge soffits to the
    floor, which the model's own plan-footprint paths never see. A triangle
    tessellated with both windings is one face of floor, not two, so
    coincident triangles count once.
    """
    try:
        import ifcopenshell.geom
        import numpy as np

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        shape = ifcopenshell.geom.create_shape(settings, el)
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
        faces = np.asarray(shape.geometry.faces, dtype=int).reshape(-1, 3)
    except Exception:
        return None
    if not len(faces):
        return None
    a, b, c = verts[faces[:, 0]], verts[faces[:, 1]], verts[faces[:, 2]]
    cross = np.cross(b - a, c - a)
    norm = np.linalg.norm(cross, axis=1)
    keep = norm > 0
    if not keep.any():
        return None
    kept = faces[keep]
    nz = np.where(norm[keep] > 0, cross[keep, 2] / np.where(norm[keep] == 0, 1, norm[keep]), 0.0)
    horiz = np.abs(nz) > 0.99
    if not horiz.any():
        return None
    z = verts[kept[:, 0], 2]
    zmin = z[horiz].min()
    floor = horiz & (np.abs(z - zmin) < 1e-4)
    if not floor.any():
        return None
    tri = kept[floor]
    cen = (verts[tri[:, 0], :2] + verts[tri[:, 1], :2] + verts[tri[:, 2], :2]) / 3.0
    area2 = norm[keep][floor]
    keys = np.round(np.column_stack([cen, area2[:, None]]), 6)
    unique = np.unique(keys, axis=0)
    area = float(unique[:, 2].sum() / 2.0)
    return area if area > 0 else None


def _profile_floor_area(sp, scale: float) -> float | None:
    """Declared footprint (m2) of a space body that is one vertical extrusion.

    The kernel under-tessellates some stepped profiles (#1218: the duplex
    stair space's bottom came back as a 3.8 m2 landing of a 20.5 m2
    footprint). When the body is exactly one IfcExtrudedAreaSolid extruded
    parallel to Z from a closed planar profile, the floor area is the
    profile area whatever the tessellation did, so only gross kernel
    failures (< half the declared footprint) switch to it.
    """
    rep = getattr(sp, "Representation", None)
    if rep is None:
        return None
    solids = []
    for r in rep.Representations or []:
        if r.RepresentationIdentifier != "Body":
            continue
        for it in r.Items or []:
            if not it.is_a("IfcExtrudedAreaSolid"):
                return None  # not a single-solid body
            solids.append(it)
    if len(solids) != 1:
        return None
    sol = solids[0]
    d = getattr(sol.ExtrudedDirection, "DirectionRatios", None) or (0, 0, 1)
    if abs(float(d[0])) > 1e-9 or abs(float(d[1])) > 1e-9:
        return None
    prof = sol.SweptArea
    if prof.is_a("IfcRectangleProfileDef"):
        xdim, ydim = float(prof.XDim), float(prof.YDim)
        return xdim * ydim * scale * scale
    if prof.is_a("IfcArbitraryClosedProfileDef"):
        curve = prof.OuterCurve
        if not curve.is_a("IfcPolyline"):
            return None
        pts = [p.Coordinates for p in curve.Points or []]
        if len(pts) < 3:
            return None
        s2 = 0.0
        for i in range(len(pts)):
            x1, y1 = float(pts[i][0]), float(pts[i][1])
            x2, y2 = float(pts[(i + 1) % len(pts)][0]), float(pts[(i + 1) % len(pts)][1])
            s2 += x1 * y2 - x2 * y1
        return abs(s2) / 2.0 * scale * scale
    return None


def _axis_length(el, scale: float) -> float | None:
    """2D length of the wall's Axis representation (model units * scale)."""
    rep = getattr(el, "Representation", None)
    for r in getattr(rep, "Representations", None) or []:
        if r.RepresentationIdentifier != "Axis":
            continue
        for item in r.Items or []:
            pts = None
            if item.is_a("IfcPolyline"):
                pts = [p.Coordinates for p in item.Points]
            elif item.is_a("IfcTrimmedCurve") and item.BasisCurve.is_a("IfcLine"):
                t1 = [t for t in item.Trim1 if t.is_a("IfcCartesianPoint")]
                t2 = [t for t in item.Trim2 if t.is_a("IfcCartesianPoint")]
                if t1 and t2:
                    pts = [t1[0].Coordinates, t2[0].Coordinates]
            elif item.is_a("IfcIndexedPolyCurve"):
                pts = list(item.Points.CoordList)
            if pts and len(pts) >= 2:
                length = sum(math.dist(tuple(p[:2]), tuple(q[:2])) for p, q in zip(pts, pts[1:]))
                return length * scale if length > 0 else None
    return None


def _geom_plan_length(el, scale: float) -> float | None:
    """Longest plan extent (m) of the element's tessellated body, or None.

    Fallback for walls with neither a Qto length nor an Axis: the body's
    vertices via ``ifcopenshell.geom`` (world coords), projected on the
    principal plan direction found by PCA. A straight wall's long axis is
    its length; a curved or L-shaped wall approximates it. Like
    ``_geom_floor_area``, the tessellation comes back in metres, so
    ``scale`` is accepted for symmetry but not applied.
    """
    try:
        import ifcopenshell.geom
        import numpy as np

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        shape = ifcopenshell.geom.create_shape(settings, el)
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
    except Exception:
        return None
    xy = np.unique(np.round(verts[:, :2], 9), axis=0)
    if len(xy) < 2:
        return None
    centered = xy - xy.mean(axis=0)
    cov = centered.T @ centered / len(centered)
    evals, evecs = np.linalg.eigh(cov)
    best = 0.0
    for i in range(2):
        d = evecs[:, i]
        proj = centered @ d
        best = max(best, float(proj.max() - proj.min()))
    return best if best > 0 else None


def _geom_window_area_m2(el) -> float | None:
    """Window area (m2) from the element's tessellated body, or None.

    A window body is a thin box: the frame's thickness is the smallest
    bounding-box extent, so the area is the product of the two largest
    extents of the tessellated body from ``ifcopenshell.geom``, in metres
    (a non-rectangular window approximates its opening). Like
    ``_geom_floor_area``, the same geometry policy the room areas use;
    ``gni-49`` proves the stated attributes cannot be trusted when a body
    exists (#1220). Returns None when the body cannot be tessellated or has
    fewer than two positive extents.
    """
    try:
        import ifcopenshell.geom
        import numpy as np

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)
        shape = ifcopenshell.geom.create_shape(settings, el)
        verts = np.asarray(shape.geometry.verts, dtype=float).reshape(-1, 3)
    except Exception:
        return None
    if len(verts) < 3:
        return None
    dims = np.sort(verts.max(axis=0) - verts.min(axis=0))
    if dims[1] <= 0:
        return None
    return float(dims[1] * dims[2])


def _is_external(psets: dict) -> bool | None:
    v = (psets.get("Pset_WallCommon") or {}).get("IsExternal")
    return v if isinstance(v, bool) else None


def build_truth(path: Path) -> dict:
    """Ground-truth quantities for one IFC, read with ifcopenshell only."""
    import ifcopenshell

    path = Path(path)
    f = ifcopenshell.open(str(path))
    scale = _length_scale(f)
    # areas are stated in the project's AREAUNIT when it declares one, and
    # in the file's length units squared when it does not (#1351, #1363)
    area_scale = _project_unit_scale(f, "AREAUNIT", 2)
    if area_scale is None:
        area_scale = scale * scale

    rooms = {}
    for sp in sorted(f.by_type("IfcSpace"), key=lambda e: e.GlobalId):
        ps = _psets(sp)
        area = _qto_measure(
            sp,
            ps,
            "Qto_SpaceBaseQuantities",
            ("NetFloorArea", "GrossFloorArea"),
            2,
            area_scale,
        )
        source = "qto" if area is not None else None
        if area is None:
            geom = _geom_floor_area(sp)
            prof = _profile_floor_area(sp, scale)
            if prof is not None and (geom is None or geom < 0.5 * prof):
                # the kernel lost the bottom of this extrusion (#1218)
                area, source = prof, "profile"
            elif geom is not None:
                area, source = geom, "geometry"
        rooms[sp.GlobalId] = {
            "name": (sp.Name or ""),
            "long_name": (getattr(sp, "LongName", None) or ""),
            "area_m2": area,
            "area_source": source,
        }

    walls = f.by_type("IfcWall")  # includes IfcWallStandardCase subtypes
    flagged = [w for w in walls if _is_external(_psets(w)) is not None]
    ext_len = 0.0
    ext_n = 0
    ext_geom = 0
    ext_unmeasured = 0
    for w in sorted(walls, key=lambda e: e.GlobalId):
        ps = _psets(w)
        if _is_external(ps) is not True:
            continue
        length = _qto_measure(w, ps, "Qto_WallBaseQuantities", ("Length",), 1, scale)
        if length is None:
            length = _axis_length(w, scale)
        source = "measured"
        if length is None:
            # body-geometry fallback, same policy as room areas: an
            # unmeasured wall must not shrink the total (#1205)
            length = _geom_plan_length(w, scale)
            source = "geometry"
        if length is None:
            ext_unmeasured += 1
            continue
        ext_len += length
        if source == "measured":
            ext_n += 1
        else:
            ext_geom += 1
    if ext_n or ext_geom:
        wall_len: float | None = ext_len
        wall_reason = ""
    elif not flagged:
        wall_len, wall_reason = None, "no wall states Pset_WallCommon.IsExternal"
    elif ext_unmeasured:
        wall_len, wall_reason = None, "exterior walls have no Qto length, Axis, or measurable body"
    else:
        wall_len, wall_reason = None, "no wall has Pset_WallCommon.IsExternal = True"

    windows = f.by_type("IfcWindow")
    win_area = 0.0
    win_sized = 0
    win_geom = 0
    for w in windows:
        area = _geom_window_area_m2(w)
        source = "geometry"
        if area is None:
            # stated attributes are the last resort: gni-49 shows they can
            # carry the frame thickness in the width field (#1220)
            ww, hh = getattr(w, "OverallWidth", None), getattr(w, "OverallHeight", None)
            if ww and hh:
                area = float(ww) * float(hh) * scale * scale
                source = "measured"
        if area is not None:
            win_area += area
            win_sized += 1
            if source == "geometry":
                win_geom += 1

    return {
        "schema": TRUTH_SCHEMA,
        "ifc_sha256": sha256_file(path),
        "ifc_schema": f.schema,
        "ifcopenshell_version": getattr(ifcopenshell, "version", ""),
        "rooms": rooms,
        "exterior_wall_length_m": wall_len,
        "exterior_wall_reason": wall_reason,
        "exterior_walls_measured": ext_n,
        "exterior_walls_from_geometry": ext_geom,
        "exterior_walls_unmeasured": ext_unmeasured,
        "windows": len(windows),
        "windows_sized": win_sized,
        "windows_from_geometry": win_geom,
        "window_area_m2": win_area if win_sized else None,
        "doors": len(f.by_type("IfcDoor")),
    }


def truth_path_for(arch_ifc: Path) -> Path:
    return Path(arch_ifc).parent / TRUTH_FILENAME


def shared_truth_dirs(entries: Iterable[tuple[str, Path]]) -> dict[Path, list[str]]:
    """Truth directories that more than one building would write to (#1259).

    Frozen truth lives in one file name (``truth.json``) next to the IFC, so
    two buildings staged in one shared directory collide: the second
    building's truth.json overwrites the first's and the harness then reads
    whichever landed last (each file is pinned to its own IFC's sha256, so
    the loser reads as a sha256 mismatch). Stage every building in its own
    directory instead: ``<root>/<building>/arch.ifc`` +
    ``<root>/<building>/truth.json``.

    Returns ``{truth_dir: [building names]}`` for the offending directories.
    """
    # Resolve before grouping (#1273): `b1/../b1/arch.ifc` and `b1/arch.ifc`
    # are one directory, and a lexical Path comparison would let the #1259
    # refusal be dodged by config spelling.
    by_dir: dict[Path, list[str]] = {}
    for name, arch_ifc in entries:
        by_dir.setdefault(Path(arch_ifc).expanduser().resolve().parent, []).append(name)
    return {d: sorted(names) for d, names in by_dir.items() if len(names) > 1}


def write_truth(arch_ifc: Path, out: Path | None = None) -> Path:
    truth = build_truth(arch_ifc)
    out = out or truth_path_for(arch_ifc)
    out.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    return out


class TruthMismatch(RuntimeError):
    """truth.json was frozen from a different IFC than the one on disk."""


def load_or_freeze_truth(arch_ifc: Path) -> tuple[dict, str]:
    """(truth, how) for ``arch_ifc``: ``how`` is ``frozen`` or ``built``.

    Reuses a frozen ``truth.json`` when its sha256 matches the IFC; writes one
    when none exists; raises ``TruthMismatch`` when the IFC changed under it.
    """
    p = truth_path_for(arch_ifc)
    if p.exists():
        truth = json.loads(p.read_text())
        if truth.get("schema") != TRUTH_SCHEMA:
            raise TruthMismatch(f"{p} has schema {truth.get('schema')!r}, want {TRUTH_SCHEMA}")
        if truth.get("ifc_sha256") != sha256_file(arch_ifc):
            raise TruthMismatch(
                f"{p} was frozen from a different IFC (sha256 mismatch);"
                " regenerate with scripts/bench_truth.py --force"
            )
        return truth, "frozen"
    write_truth(arch_ifc, p)
    return json.loads(p.read_text()), "built"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True, help="benchmarks.json (see DATASETS.md)")
    ap.add_argument("--force", action="store_true", help="overwrite existing truth.json")
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text())
    base = Path(args.config).resolve().parent
    entries: list[tuple[str, Path]] = []
    for e in cfg.get("buildings", []):
        v = e.get("arch_ifc")
        if not v:
            continue
        ifc = Path(v).expanduser()
        ifc = ifc if ifc.is_absolute() else base / ifc
        entries.append((e["name"], ifc))

    # #1259: two buildings in one directory would write the same truth.json
    # and the harness would read whichever landed last.
    shared = shared_truth_dirs(entries)
    if shared:
        for d, names in sorted(shared.items()):
            print(
                f"error: buildings {', '.join(names)} share the truth directory {d};"
                " stage each building in its own directory (#1259)",
                file=sys.stderr,
            )
        return 2

    n_written = 0
    for e in cfg.get("buildings", []):
        v = e.get("arch_ifc")
        if not v:
            continue
        ifc = Path(v).expanduser()
        ifc = ifc if ifc.is_absolute() else base / ifc
        if not ifc.exists():
            print(f"{e['name']}: missing ({ifc})")
            continue
        out = truth_path_for(ifc)
        if out.exists() and not args.force:
            print(f"{e['name']}: {out} exists (use --force to rebuild)")
            continue
        write_truth(ifc, out)
        n_written += 1
        print(f"{e['name']}: wrote {out}")
    print(f"{n_written} truth file(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
