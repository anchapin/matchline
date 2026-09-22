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

from pathlib import Path
from typing import Dict, List

from bem_export import (
    BEMModel,
    BEMOpeningUnit,
    BEMSpace,
    _ensure_ccw,
    write_ifc4,
)

from building_model import BuildingModel, EnvelopeWall, Space


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
        vol = space.volume_m3 if space.volume_m3 is not None else (space.area_m2 or 0.0) * wall_height

        spaces.append(
            BEMSpace(
                sid=space.id,
                name=name,
                number=space.number,
                polygon_m=poly_ccw,
                area_m2=space.area_m2 or 0.0,
                volume_m3=vol or 0.0,
            )
        )

    # --- openings: flatten SpaceOpenings, deduplicate --------------------
    seen: Dict[tuple, BEMOpeningUnit] = {}
    for space in model.spaces.values():
        for op in space.openings:
            key = (op.tag, op.width_m, op.height_m)
            if key not in seen:
                seen[key] = BEMOpeningUnit(
                    category=op.category,
                    tag=op.tag,
                    width_m=op.width_m,
                    height_m=op.height_m,
                )
    openings = list(seen.values())

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

    return BEMModel(
        building_name=model.name,
        spaces=spaces,
        openings=openings,
        ring_m=ring_ccw,
        wall_height_m=wall_height,
        area_delta_pct=0.0,  # no simplification on export path
        simplify_tol_pct=0.0,
    )


def _build_ring(walls: List[EnvelopeWall]) -> List[tuple]:
    """Assemble an ordered ring from EnvelopeWall segments.

    Orders walls by facade direction (south -> east -> north -> west) then
    chains from_m -> to_m for each segment. The result is a closed polygon
    in y-north coordinates with CCW winding (after _ensure_ccw in _bem_from_model).
    """
    FACADE_ORDER = ["south", "east", "north", "west"]

    def _sort_key(w: EnvelopeWall) -> int:
        try:
            return FACADE_ORDER.index(w.facade.lower())
        except ValueError:
            return len(FACADE_ORDER)

    sorted_walls = sorted(walls, key=_sort_key)
    ring: List[tuple] = []

    for w in sorted_walls:
        if not ring:
            # Start: from_m in y-north
            ring.append((w.from_m[0], -w.from_m[1]))
            ring.append((w.to_m[0], -w.to_m[1]))
        else:
            # Append to_m, but skip if it equals the last point (duplicate)
            pt = (w.to_m[0], -w.to_m[1])
            if ring[-1] != pt:
                ring.append(pt)

    return ring


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
    """
    bem = _bem_from_model(model)
    return write_ifc4(bem, path)


def export_ifc(model_path: str, out_path: str) -> None:
    """Load a BuildingModel from JSON and export to IFC4.

    Args:
        model_path: path to BuildingModel JSON file
        out_path:   output IFC4 file path
    """
    model = BuildingModel.from_json(Path(model_path).read_text())
    _export_ifc(model, out_path)
