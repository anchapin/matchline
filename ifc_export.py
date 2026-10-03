"""IFC export: BuildingModel -> IFC4 via BEMModel adapter.

This module provides the export half of the IFC round-trip:
  ifc_import.py   : IFC  -> BuildingModel  (import)
  ifc_export.py   : BuildingModel -> IFC4 (export)

The adapter converts BuildingModel to BEMModel (which write_ifc4 already
understands), then calls bem_export.write_ifc4.

No new ifcopenshell calls here — all IFC geometry goes through write_ifc4.
Future extensions (zones, lighting) can layer on top of _bem_from_model.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List

from bem_export import (
    BEMModel,
    BEMOpeningUnit,
    BEMSpace,
    _ensure_ccw,
    _validate_out_path,
    validate_ifc4,
    write_ifc4,
)
from bem_helpers import _edge_facades
from building_model import BuildingModel, EnvelopeWall, SpaceLighting
from validate import validate_bem_conservation

# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


def _bem_from_model(model: BuildingModel) -> BEMModel:
    """Convert a BuildingModel to a BEMModel for IFC/gbXML export.

    Coordinate frame: canonical (building_model) is y-down (drawing frame).
    IFC/gbXML use y-north. This function flips the y axis on all polygons
    and ensures CCW winding for the envelope ring.

    Args:
        model: the canonical BuildingModel

    Returns:
        BEMModel ready for write_ifc4 / write_gbxml
    """
    wall_height = float(model.levels[0].wall_height_m) if model.levels else 3.0

    # --- spaces -----------------------------------------------------------
    spaces: List[BEMSpace] = []
    for space in model.spaces.values():
        # polygon: y-down -> y-north, then ensure CCW
        poly_north = [(x, -y) for x, y in space.polygon_m]
        poly_ccw = _ensure_ccw(poly_north)

        # name uses Space.label which is "{name} {number}" e.g. "OPEN OFFICE 101"
        name = space.label
        # volume: use stored value or derive from area * wall_height
        vol = (
            space.volume_m3 if space.volume_m3 is not None else (space.area_m2 or 0.0) * wall_height
        )

        spaces.append(
            BEMSpace(
                sid=space.id,
                name=name,
                number=space.number,
                polygon_m=poly_ccw,
                area_m2=space.area_m2 or 0.0,
                volume_m3=vol or 0.0,
                lighting_w=(space.lighting or SpaceLighting()).total_w or 0.0,
            )
        )

    # --- openings: flatten SpaceOpenings, one unit each ------------------
    wall_units: List[BEMOpeningUnit] = []
    seen_keys: set = set()
    sky: List[BEMOpeningUnit] = []
    for space in model.spaces.values():
        for op in space.openings:
            if op.category == "skylight":
                # Roof glazing (roadmap item 3, wave 2b): one unit per
                # skylight, no dedupe (two SK-1s over two rooms are two
                # skylights), tied to its space so the writer keeps it over
                # that room on the roof slab.
                sky.append(
                    BEMOpeningUnit(
                        category="skylight",
                        tag=op.tag,
                        width_m=op.width_m,
                        height_m=op.height_m,
                        space_sid=space.id,
                    )
                )
                continue
            # One unit per physical opening. Two openings are the same window
            # only when they match on tag, size, sill, facade AND position:
            # that is a duplicate detection (two elevations of one facade,
            # #504) and is written once. The old key (tag, w, h) also merged
            # distinct windows of the same type -- two type-A windows on one
            # wall became one -- and disagreed with the gbXML adapter.
            key = (
                op.tag,
                op.width_m,
                op.height_m,
                op.sill_m,
                op.host_facade,
                op.s_center_m,
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            wall_units.append(
                BEMOpeningUnit(
                    category=op.category,
                    tag=op.tag,
                    width_m=op.width_m,
                    height_m=op.height_m,
                    host_facade=op.host_facade or "",
                )
            )
    openings = wall_units + sky

    # --- ring: build from EnvelopeWall segments in canonical facade order -
    if model.envelope:
        ring = _build_ring(model.envelope)
    else:
        # Fallback: derive ring from the first space's polygon
        if spaces:
            ring = list(spaces[0].polygon_m)
        else:
            ring = []

    # ring_m must be CCW in y-north for write_ifc4
    ring_ccw = _ensure_ccw(ring) if ring else []

    from bem_shading import shades_from_model

    shades, shade_notes = shades_from_model(
        model, lambda p: (p[0], -p[1]), [sp.polygon_m for sp in spaces]
    )
    return BEMModel(
        building_name=model.name,
        spaces=spaces,
        openings=openings,
        ring_m=ring_ccw,
        ring_facades=_edge_facades(ring_ccw, y_north=True),
        wall_height_m=wall_height,
        area_delta_pct=0.0,  # no simplification on export path
        simplify_tolerance=0.0,
        zones=[(z.id, z.space_ids) for z in model.zones.values()],
        zone_terminals={
            z.id: [(d.id, d.tag, d.x_m, -d.y_m) for d in z.diffusers]
            for z in model.zones.values()
            if z.diffusers
        },
        shades=shades,
        notes=shade_notes,
    )


def _build_ring(walls: List[EnvelopeWall]) -> List[tuple]:
    """Assemble an ordered ring from EnvelopeWall segments.

    Starts at the first wall in facade order (south -> east -> north -> west)
    and then follows shared endpoints, taking each segment in whichever
    direction continues the chain. EnvelopeWall from_m/to_m carry no common
    winding (a south wall may run west->east while the east wall runs
    north->south), so chaining on to_m alone can skip a corner and close the
    ring with a diagonal. When no segment touches the current end (a gap in
    the envelope), the next wall in facade order is taken, entered at its
    nearer endpoint. Returns y-north points; _bem_from_model makes it CCW.
    """
    FACADE_ORDER = ["south", "east", "north", "west"]
    eps = 1e-6

    def _sort_key(w: EnvelopeWall) -> int:
        try:
            return FACADE_ORDER.index(w.facade.lower())
        except ValueError:
            return len(FACADE_ORDER)

    def _north(p) -> tuple:
        return (float(p[0]), -float(p[1]))

    def _same(a, b) -> bool:
        return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps

    segs = [
        (_north(w.from_m), _north(w.to_m))
        for w in sorted(walls, key=_sort_key)
        if w.from_m and w.to_m
    ]
    if not segs:
        return []
    a, b = segs.pop(0)
    ring: List[tuple] = [a, b]
    while segs:
        end = ring[-1]
        nxt = None
        for i, (p0, p1) in enumerate(segs):
            if _same(p0, end):
                nxt = (i, p1)
                break
            if _same(p1, end):
                nxt = (i, p0)
                break
        if nxt is not None:
            i, pt = nxt
            segs.pop(i)
            if not _same(pt, ring[-1]):
                ring.append(pt)
            continue
        # gap: take the next wall in facade order, nearer endpoint first
        p0, p1 = segs.pop(0)
        d0 = math.dist(p0, end)
        d1 = math.dist(p1, end)
        first, second = (p0, p1) if d0 <= d1 else (p1, p0)
        for pt in (first, second):
            if not _same(pt, ring[-1]):
                ring.append(pt)
    if len(ring) > 1 and _same(ring[0], ring[-1]):
        ring.pop()
    return ring


def _validate_in_path(path: str | Path) -> Path:
    """Validate an input path is safe: must resolve within cwd.

    Raises ValueError if a relative path escapes the current working directory.
    Absolute paths are allowed as user-intended destinations.
    """
    path = Path(path)
    if not path.is_absolute():
        resolved = Path(path).resolve()
        cwd_resolved = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd_resolved)
        except ValueError:
            raise ValueError(
                f"Input path '{path}' resolves to '{resolved}' which escapes "
                f"the working directory '{cwd_resolved}'. Rejecting to prevent "
                "path traversal."
            )
    if not path.is_file():
        raise ValueError(f"Input path '{path}' is not a file or does not exist.")
    return path


# ---------------------------------------------------------------------------
# IFC export
# ---------------------------------------------------------------------------


def _export_ifc(model: BuildingModel, path: str | Path) -> Path:
    """Export a BuildingModel to an IFC4 file.

    Args:
        model: the canonical BuildingModel
        path: output IFC file path

    Returns:
        the Path that was written

    Raises:
        ValueError: if the model fails conservation-law validation
    """
    bem = _bem_from_model(model)
    # Block invalid BEM data per conservation laws before writing IFC
    bem_results = validate_bem_conservation(bem)
    failed = [r for r in bem_results if r.severity == "error"]
    if failed:

        def _fmt(r):
            a = f"{r.actual:.3f}" if r.actual is not None else "N/A"
            e = f"{r.expected:.3f}" if r.expected is not None else "N/A"
            return f"  - {r.name}: actual={a}, expected={e}"

        msgs = [_fmt(r) for r in failed]
        raise ValueError(
            f"Conservation-law validation failed ({len(failed)} check(s) failed):\n"
            + "\n".join(msgs)
        )
    ifc_path = write_ifc4(bem, path)
    ok, errors = validate_ifc4(ifc_path)
    if not ok:
        raise ValueError(
            f"IFC schema validation failed ({len(errors)} error(s)):\n"
            + "\n".join(f"  - {e}" for e in errors)
        )
    return ifc_path


def export_ifc(model_path: str, out_path: str) -> None:
    """Load a BuildingModel from JSON and export to IFC4.

    Args:
        model_path: path to BuildingModel JSON file
        out_path:   output IFC4 file path
    """
    _validate_in_path(model_path)
    model = BuildingModel.from_json(Path(model_path).read_text())
    _export_ifc(model, _validate_out_path(out_path))
