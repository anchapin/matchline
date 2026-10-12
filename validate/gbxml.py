"""gbXML validation checks."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional

from lxml import etree

from safe_xml import safe_xml_parse
from validate.types import CheckResult

if TYPE_CHECKING:
    from validate import _Ctx

# ---------------------------------------------------------------------------
# Export checks (only run when a path is given)
# ---------------------------------------------------------------------------

_GBXML_NS = "http://www.gbxml.org/schema"


def _check_gbxml_spaces(ctx: _Ctx) -> CheckResult:
    path = ctx.gbxml_path
    if not path:
        return CheckResult("gbxml_space_areas", "gbXML space areas", "skip", "no gbXML path given")
    try:
        root = safe_xml_parse(path).getroot()
    except etree.XMLSyntaxError as e:
        return CheckResult(
            "gbxml_space_areas", "gbXML space areas", "error", f"gbXML not well-formed: {e}"
        )
    ns = {"g": _GBXML_NS}
    spaces = root.findall(".//g:Space", ns)
    if len(spaces) != len(ctx.model.spaces):
        return CheckResult(
            "gbxml_space_areas",
            "gbXML space areas",
            "error",
            f"gbXML has {len(spaces)} Space elements but the model has "
            f"{len(ctx.model.spaces)} spaces",
            expected=len(ctx.model.spaces),
            actual=len(spaces),
        )
    bad = []
    for se in spaces:
        try:
            a = float(se.find("g:Area", ns).text)
            v = float(se.find("g:Volume", ns).text)
        except (AttributeError, TypeError, ValueError):
            bad.append(se.get("id"))
            continue
        if a <= 0 or v <= 0:
            bad.append(se.get("id"))
    if bad:
        return CheckResult(
            "gbxml_space_areas",
            "gbXML space areas",
            "error",
            f"{len(bad)} gbXML Space(s) with missing or non-positive Area/Volume",
            entities=bad[:20],
        )
    return CheckResult(
        "gbxml_space_areas",
        "gbXML space areas",
        "pass",
        f"{len(spaces)} gbXML spaces, all with positive Area and Volume",
    )


def _check_gbxml_opening_refs(ctx: _Ctx) -> CheckResult:
    path = ctx.gbxml_path
    if not path:
        return CheckResult(
            "gbxml_opening_refs", "gbXML opening refs", "skip", "no gbXML path given"
        )
    try:
        root = safe_xml_parse(path).getroot()
    except etree.XMLSyntaxError as e:
        return CheckResult(
            "gbxml_opening_refs", "gbXML opening refs", "error", f"gbXML not well-formed: {e}"
        )
    ns = {"g": _GBXML_NS}
    # every Opening must be nested under a Surface with an id
    orphans = 0
    n_open = 0
    for su in root.findall(".//g:Surface", ns):
        sid = su.get("id")
        for op in su.findall("g:Opening", ns):
            n_open += 1
            if not sid or not op.get("id"):
                orphans += 1
    if orphans:
        return CheckResult(
            "gbxml_opening_refs",
            "gbXML opening refs",
            "error",
            f"{orphans} opening(s) not hosted on an identified surface",
        )
    return CheckResult(
        "gbxml_opening_refs",
        "gbXML opening refs",
        "pass",
        f"{n_open} gbXML openings all hosted on surfaces",
    )


def _parse_cartesian_point(pt) -> Optional[tuple]:
    try:
        coords = [float(c.text) for c in pt]
        return (coords[0], coords[1]) if len(coords) >= 2 else None
    except (ValueError, IndexError):
        return None


def _check_gbxml_wall_areas(ctx: _Ctx) -> CheckResult:
    path = ctx.gbxml_path
    if not path:
        return CheckResult(
            "gbxml_wall_areas",
            "gbXML wall area cross-pipeline reconciliation",
            "skip",
            "no gbXML path given",
        )
    try:
        root = safe_xml_parse(path).getroot()
    except etree.XMLSyntaxError as e:
        return CheckResult(
            "gbxml_wall_areas",
            "gbXML wall area cross-pipeline reconciliation",
            "error",
            f"gbXML not well-formed: {e}",
        )
    ns = {"g": _GBXML_NS}
    space_height_map = {}
    for sp in ctx.model.spaces.values():
        if getattr(sp, "gbxml_id", None) and getattr(sp, "wall_height_m", 0):
            space_height_map[sp.gbxml_id] = sp.wall_height_m

    total_gbxml_area = 0.0
    for surf in root.findall(".//g:Surface", ns):
        if surf.get("surfaceType") not in ("ExteriorWall",):
            continue
        rg = surf.find("g:RectangularGeometry", ns)
        if rg is None:
            continue
        pts = rg.findall("g:CartesianPoint", ns)
        if len(pts) < 2:
            continue
        p0 = _parse_cartesian_point(pts[0])
        p1 = _parse_cartesian_point(pts[1])
        if p0 is None or p1 is None:
            continue
        length = math.sqrt((p1[0] - p0[0]) ** 2 + (p1[1] - p0[1]) ** 2)
        adj = surf.find("g:AdjacentSpaceId", ns)
        h = 0.0
        if adj is not None:
            sid = adj.get("spaceIdRef", "")
            h = space_height_map.get(sid, 0.0)
        if h <= 0:
            continue
        total_gbxml_area += length * h

    total_canonical = sum(w.area_m2 for w in ctx.model.envelope if getattr(w, "area_m2", 0) > 0)
    if total_canonical <= 0 or total_gbxml_area <= 0:
        return CheckResult(
            "gbxml_wall_areas",
            "gbXML wall area cross-pipeline reconciliation",
            "skip",
            f"canonical {total_canonical:.3f} m², gbXML {total_gbxml_area:.3f} m²",
        )
    rel_err = abs(total_gbxml_area - total_canonical) / total_canonical
    tol = ctx.tol_envelope if hasattr(ctx, "tol_envelope") else 0.02
    if rel_err > tol:
        return CheckResult(
            "gbxml_wall_areas",
            "gbXML wall area cross-pipeline reconciliation",
            "error",
            f"gbXML wall area {total_gbxml_area:.3f} m² vs canonical "
            f"{total_canonical:.3f} m²: discrepancy {rel_err * 100:.2f}% > tol {tol * 100:.1f}%",
            expected=total_canonical,
            actual=total_gbxml_area,
        )
    return CheckResult(
        "gbxml_wall_areas",
        "gbXML wall area cross-pipeline reconciliation",
        "pass",
        f"gbXML wall area {total_gbxml_area:.3f} m² matches canonical "
        f"{total_canonical:.3f} m² ({rel_err * 100:.2f}%)",
    )


def _check_ifc_counts(ctx: _Ctx) -> CheckResult:
    path = ctx.ifc_path
    if not path:
        return CheckResult("ifc_entity_counts", "IFC entity counts", "skip", "no IFC path given")
    try:
        import importlib.util

        if importlib.util.find_spec("ifcopenshell") is None:
            raise ImportError("ifcopenshell not found")
        import ifcopenshell
    except ImportError:
        return CheckResult(
            "ifc_entity_counts", "IFC entity counts", "skip", "IfcOpenShell not available"
        )
    try:
        f = ifcopenshell.open(str(path))
    except Exception as e:
        return CheckResult(
            "ifc_entity_counts", "IFC entity counts", "error", f"could not parse IFC file: {e}"
        )
    n_spaces = len(f.by_type("IfcSpace"))
    n_walls = len(f.by_type("IfcWall"))
    if n_spaces != len(ctx.model.spaces):
        return CheckResult(
            "ifc_entity_counts",
            "IFC entity counts",
            "error",
            f"IFC has {n_spaces} IfcSpace but the model has {len(ctx.model.spaces)} spaces",
            expected=len(ctx.model.spaces),
            actual=n_spaces,
        )
    if n_walls == 0:
        return CheckResult(
            "ifc_entity_counts", "IFC entity counts", "error", "IFC has no IfcWall entities"
        )
    return CheckResult(
        "ifc_entity_counts",
        "IFC entity counts",
        "pass",
        f"{n_spaces} IfcSpace, {n_walls} IfcWall -- counts match model",
    )
