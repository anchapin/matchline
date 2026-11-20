"""The proposal mapper: a valid proposal -> a canonical BuildingModel.

``map_proposal(doc)`` runs the validator first (L4: the mapper never
re-checks and never fixes), then compiles the document into the canonical
model. It is a pure function: the same document always produces the same
model (L1) and the same report.

The mapping laws it implements (design #1417):

L1  Determinism. Element order follows document order; every derived id
    comes from a stated rule and nothing else.
L2  No silent loss. Every proposal element is either mapped or named in
    the mapping report with a reason; the per-level counts reconcile.
L3  Provenance completeness. Every mapped fact carries a Provenance with
    sheet_id ``proposal:{proposal id}``, method ``proposal_mapping``,
    confidence 1.0 and a note naming the source proposal element id.
L5  Proposal is not a model. The output is a canonical BuildingModel; the
    proposal is never a parallel source of truth.
L6  Frame fidelity. Proposal coordinates are canonical frame metres; no
    axis flip, no scaling, no unit conversion.
L7  Host integrity. After splitting, every opening has exactly one host
    segment (the validator refuses straddles, so the mapper never guesses).
L8  Space identity. The canonical space id is ``{level}-{number}``.
L9  Envelope rule. Walls split at vertices; a segment mirrors into
    EnvelopeWall when spaces border exactly one side of it.
L10 Interior openings. An interior opening is stored once, on the lower-id
    bordering space, with ``adjacent_space_id`` naming the other space.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from building_model import (
    BimElement,
    BimOpening,
    BuildingModel,
    EnvelopeWall,
    Level,
    Provenance,
    Space,
    SpaceOpening,
)

from .validate import validate_proposal

#: The provenance method every mapped fact carries (L3).
PROVENANCE_METHOD = "proposal_mapping"

#: How far past the wall face the adjacency probe reaches, in metres.
PROBE_EPS_M = 0.01
#: How many samples along a segment the adjacency shares are counted with.
SAMPLES = 41

#: Length tolerance for the geometric comparisons, in metres.
EPS = 1e-9


@dataclass
class ReportEntry:
    """One flagged or unmapped proposal element, with the reason."""

    level_id: str
    kind: str  # "wall" | "space" | "opening" | "slab" | "segment" | "envelope"
    element_id: str
    reason: str
    unmapped: bool = False

    def as_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "kind": self.kind,
            "element_id": self.element_id,
            "reason": self.reason,
            "unmapped": self.unmapped,
        }


@dataclass
class LevelCounts:
    """Proposed vs mapped element counts for one level (L2)."""

    level_id: str
    walls_proposed: int = 0
    walls_mapped: int = 0
    walls_reported: int = 0
    spaces_proposed: int = 0
    spaces_mapped: int = 0
    spaces_reported: int = 0
    openings_proposed: int = 0
    openings_mapped: int = 0
    openings_reported: int = 0
    slabs_proposed: int = 0
    slabs_mapped: int = 0
    slabs_reported: int = 0

    def reconciles(self) -> bool:
        """True when every proposed element is mapped or reported."""
        return (
            self.walls_proposed == self.walls_mapped + self.walls_reported
            and self.spaces_proposed == self.spaces_mapped + self.spaces_reported
            and self.openings_proposed == self.openings_mapped + self.openings_reported
            and self.slabs_proposed == self.slabs_mapped + self.slabs_reported
        )

    def as_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "walls": {
                "proposed": self.walls_proposed,
                "mapped": self.walls_mapped,
                "reported": self.walls_reported,
            },
            "spaces": {
                "proposed": self.spaces_proposed,
                "mapped": self.spaces_mapped,
                "reported": self.spaces_reported,
            },
            "openings": {
                "proposed": self.openings_proposed,
                "mapped": self.openings_mapped,
                "reported": self.openings_reported,
            },
            "slabs": {
                "proposed": self.slabs_proposed,
                "mapped": self.slabs_mapped,
                "reported": self.slabs_reported,
            },
        }


@dataclass
class MappingReport:
    """What the mapper did with every proposal element (L2)."""

    proposal_id: str
    entries: list[ReportEntry] = field(default_factory=list)
    levels: list[LevelCounts] = field(default_factory=list)

    def add(
        self,
        level_id: str,
        kind: str,
        element_id: str,
        reason: str,
        unmapped: bool = False,
    ) -> ReportEntry:
        entry = ReportEntry(
            level_id=level_id,
            kind=kind,
            element_id=element_id,
            reason=reason,
            unmapped=unmapped,
        )
        self.entries.append(entry)
        return entry

    @property
    def unmapped(self) -> list[ReportEntry]:
        """The elements that were not mapped (L2)."""
        return [e for e in self.entries if e.unmapped]

    @property
    def flags(self) -> list[ReportEntry]:
        """The elements that were mapped but flagged."""
        return [e for e in self.entries if not e.unmapped]

    def counts(self, level_id: str) -> Optional[LevelCounts]:
        for counts in self.levels:
            if counts.level_id == level_id:
                return counts
        return None

    def reconciles(self) -> bool:
        """True when every level's proposed counts equal mapped plus reported."""
        return all(counts.reconciles() for counts in self.levels)

    def as_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "entries": [e.as_dict() for e in self.entries],
            "levels": [c.as_dict() for c in self.levels],
            "reconciles": self.reconciles(),
        }


# --- geometry (stdlib only) -------------------------------------------------


def _polygon_area(ring: list) -> float:
    """The area of a plan ring, in square metres."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _perimeter(ring: list) -> float:
    n = len(ring)
    return sum(math.dist(ring[i], ring[(i + 1) % n]) for i in range(n))


def _point_in_polygon(point: tuple, ring: list) -> bool:
    """Ray-casting point-in-polygon test on a plan ring."""
    x, y = point
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
    return inside


def _left_normal(a: list, b: list) -> tuple[float, float]:
    """The left normal of the travel direction a -> b (canonical y-down frame)."""
    length = math.dist(a, b)
    if length == 0:
        return (0.0, 0.0)
    ux = (b[0] - a[0]) / length
    uy = (b[1] - a[1]) / length
    return (-uy, ux)


def _side_hits(
    a: list, b: list, thickness: float, spaces: list[tuple[str, list]]
) -> tuple[dict[str, int], dict[str, int]]:
    """Count the samples each space gets on the left and on the right of a-b."""
    length = math.dist(a, b)
    if length == 0:
        return {}, {}
    lx, ly = _left_normal(a, b)
    offset = thickness / 2.0 + PROBE_EPS_M
    left: dict[str, int] = {}
    right: dict[str, int] = {}
    for i in range(SAMPLES):
        t = (i + 0.5) / SAMPLES
        cx = a[0] + (b[0] - a[0]) * t
        cy = a[1] + (b[1] - a[1]) * t
        for counts, sx, sy in ((left, lx, ly), (right, -lx, -ly)):
            point = (cx + sx * offset, cy + sy * offset)
            for space_id, ring in spaces:
                if _point_in_polygon(point, ring):
                    counts[space_id] = counts.get(space_id, 0) + 1
                    break
    return left, right


def _space_at(
    a: list,
    b: list,
    s_along: float,
    thickness: float,
    side: int,
    normal: tuple[float, float],
    spaces: list[tuple[str, list]],
) -> Optional[str]:
    """The space bordering the segment at distance s_along, on one side."""
    length = math.dist(a, b)
    if length == 0:
        return None
    t = s_along / length
    cx = a[0] + (b[0] - a[0]) * t
    cy = a[1] + (b[1] - a[1]) * t
    offset = thickness / 2.0 + PROBE_EPS_M
    lx, ly = normal
    point = (cx + side * lx * offset, cy + side * ly * offset)
    for space_id, ring in spaces:
        if _point_in_polygon(point, ring):
            return space_id
    return None


def _facade_for(a: list, b: list, bounds: dict) -> str:
    """The facade name of an axis-aligned segment on the level boundary."""
    minx, maxx, miny, maxy = bounds["minx"], bounds["maxx"], bounds["miny"], bounds["maxy"]
    horizontal = abs(b[1] - a[1]) < 1e-9
    vertical = abs(b[0] - a[0]) < 1e-9
    if horizontal and abs(a[1] - miny) < 1e-9:
        return "north"
    if horizontal and abs(a[1] - maxy) < 1e-9:
        return "south"
    if vertical and abs(a[0] - maxx) < 1e-9:
        return "east"
    if vertical and abs(a[0] - minx) < 1e-9:
        return "west"
    return ""


def _facade_reason(a: list, b: list, bounds: dict) -> str:
    """Why a facade name could not be assigned to an exterior segment."""
    horizontal = abs(b[1] - a[1]) < 1e-9
    vertical = abs(b[0] - a[0]) < 1e-9
    if not (horizontal or vertical):
        return (
            "exterior segment is not axis-aligned, so no facade name was assigned "
            "and the facade is left empty"
        )
    return (
        "exterior segment is not on the level boundary, so no facade name was "
        "assigned and the facade is left empty"
    )


def _wall_segments(points: list, closed: bool) -> list[tuple[list, list]]:
    """The segments of a wall run (the closing segment included when closed)."""
    segments = [(points[i], points[i + 1]) for i in range(len(points) - 1)]
    if closed and len(points) > 2:
        segments.append((points[-1], points[0]))
    return segments


def _host_segment(starts: list[float], lengths: list[float], offset: float, width: float):
    """The index of the one segment that hosts an opening, or None."""
    for index, start in enumerate(starts):
        if start - EPS <= offset and offset + width <= start + lengths[index] + EPS:
            return index
    return None


def _provenance(proposal_id: str, source_id: str, note: str = "") -> Provenance:
    """The provenance every mapped fact carries (L3)."""
    text = f"mapped from proposal element {source_id}"
    if note:
        text = f"{text}; {note}"
    return Provenance(
        sheet_id=f"proposal:{proposal_id}",
        revision=1,
        method=PROVENANCE_METHOD,
        confidence=1.0,
        note=text,
    )


# --- the mapper -------------------------------------------------------------


def map_proposal(doc: dict) -> tuple[BuildingModel, MappingReport]:
    """Compile a valid proposal into the canonical model plus a report.

    Raises ValueError when the document has any validation error: a
    document with an error is not mapped at all (L4).
    """
    validation = validate_proposal(doc)
    if validation.errors:
        detail = "; ".join(
            f"{d.path or 'document'}: {d.code}: {d.message}" for d in validation.errors
        )
        raise ValueError(
            f"proposal {doc.get('id')!r} has {len(validation.errors)} validation "
            f"error(s), so it is not mapped: {detail}"
        )

    proposal_id = str(doc.get("id", ""))
    report = MappingReport(proposal_id=proposal_id)
    model = BuildingModel(name=proposal_id)
    wall_types = doc.get("wallTypes") if isinstance(doc.get("wallTypes"), dict) else {}

    for level in doc.get("levels", []):
        _map_level(model, report, level, wall_types, proposal_id)

    return model, report


def _map_level(
    model: BuildingModel,
    report: MappingReport,
    level: dict,
    wall_types: dict,
    proposal_id: str,
) -> None:
    """Map one level of the proposal."""
    level_id = str(level.get("id", ""))
    level_height = float(level.get("height_m", 0.0))
    elevation = float(level.get("elevation_m", 0.0))
    counts = LevelCounts(level_id=level_id)
    report.levels.append(counts)

    model.levels.append(
        Level(
            id=level_id,
            name=str(level.get("name", level_id)),
            elevation_z_m=elevation,
            wall_height_m=level_height,
        )
    )

    # --- spaces (first: the walls need them for adjacency) -----------------
    spaces: list[tuple[str, list]] = []
    for space in level.get("spaces", []):
        counts.spaces_proposed += 1
        number = str(space.get("number", ""))
        space_id = f"{level_id}-{number}"
        ring = [[float(p[0]), float(p[1])] for p in space.get("polygon", [])]
        area = _polygon_area(ring)
        height = float(space["height_m"]) if "height_m" in space else level_height
        source_id = str(space.get("id") or number)
        note = ""
        usage = space.get("usage")
        if usage:
            note = f"usage {usage}"
        model.spaces[space_id] = Space(
            id=space_id,
            level_id=level_id,
            name=str(space.get("name", "")),
            number=number,
            polygon_m=ring,
            area_m2=area,
            volume_m3=area * height,
            height_m=height,
            core_provenance=_provenance(proposal_id, source_id, note),
        )
        spaces.append((space_id, ring))
        counts.spaces_mapped += 1

    # --- walls -------------------------------------------------------------
    bounds = _level_bounds(level)
    for wall in level.get("walls", []):
        counts.walls_proposed += 1
        wall_id = str(wall.get("id", ""))
        thickness, layers = _wall_thickness(wall, wall_types)
        wall_height = float(wall["height_m"]) if "height_m" in wall else level_height
        points = [[float(p[0]), float(p[1])] for p in wall.get("points", [])]
        segments = _wall_segments(points, bool(wall.get("closed")))
        lengths = [math.dist(a, b) for a, b in segments]
        starts: list[float] = []
        acc = 0.0
        for length in lengths:
            starts.append(acc)
            acc += length

        segment_elements: list[BimElement] = []
        segment_info: list[dict] = []
        for index, (a, b) in enumerate(segments):
            length = lengths[index]
            element = BimElement(
                global_id=f"{wall_id}#{index}",
                ifc_class="IfcWall",
                name=wall_id,
                level_id=level_id,
                length_m=length,
                thickness_m=thickness,
                height_m=wall_height,
                material_layers=[dict(layer) for layer in layers],
                provenance=_provenance(proposal_id, wall_id),
            )
            model.bim_elements.append(element)
            segment_elements.append(element)

            left, right = _side_hits(a, b, thickness, spaces)
            facade = ""
            space_id = ""
            is_exterior = False
            inner_side = 0
            if left and not right:
                is_exterior, inner_side = True, 1
            elif right and not left:
                is_exterior, inner_side = True, -1
            elif left and right:
                is_exterior = False
            else:
                report.add(
                    level_id,
                    "wall",
                    f"{wall_id}#{index}",
                    "no space borders this wall segment on either side",
                )
            if is_exterior:
                shares = left if inner_side == 1 else right
                space_id = _largest_share(shares)
                facade = _facade_for(a, b, bounds)
                if not facade:
                    report.add(
                        level_id,
                        "envelope",
                        f"{level_id}-{wall_id}#{index}",
                        _facade_reason(a, b, bounds),
                    )
                model.envelope.append(
                    EnvelopeWall(
                        id=f"{level_id}-{wall_id}#{index}",
                        facade=facade,
                        from_m=[a[0], a[1]],
                        to_m=[b[0], b[1]],
                        length_m=length,
                        height_m=wall_height,
                        area_m2=length * wall_height,
                        provenance=_provenance(proposal_id, wall_id),
                        space_id=space_id,
                    )
                )
            segment_info.append(
                {
                    "facade": facade,
                    "is_exterior": is_exterior,
                    "inner_side": inner_side,
                    "normal": _left_normal(a, b),
                }
            )

        # --- openings hosted by this run -----------------------------------
        for opening in wall.get("openings", []):
            counts.openings_proposed += 1
            opening_id = str(opening.get("id", ""))
            offset = float(opening.get("offset_m", 0.0))
            width = float(opening.get("width_m", 0.0))
            height = float(opening.get("height_m", 0.0))
            sill = float(opening["sill_m"]) if "sill_m" in opening else 0.0
            tag = str(opening.get("tag", ""))
            kind = str(opening.get("kind", ""))
            host = _host_segment(starts, lengths, offset, width)
            if host is None:
                report.add(
                    level_id,
                    "opening",
                    opening_id,
                    "no single wall segment hosts this opening, so it was not mapped",
                    unmapped=True,
                )
                counts.openings_reported += 1
                continue
            local = offset - starts[host]
            s_center = local + width / 2.0
            segment_elements[host].openings.append(
                BimOpening(
                    id=opening_id,
                    category=kind,
                    tag=tag,
                    width_m=width,
                    height_m=height,
                    sill_m=sill,
                    s_center_m=s_center,
                    host_global_id=f"{wall_id}#{host}",
                    fill_global_id=opening_id,
                    provenance=_provenance(proposal_id, opening_id),
                )
            )
            info = segment_info[host]
            a, b = segments[host]
            sides = _bordering_spaces(a, b, s_center, thickness, info, spaces)
            if not sides:
                report.add(
                    level_id,
                    "opening",
                    opening_id,
                    "no space borders this opening at its position, so it is left "
                    "unattached (the BIM opening still records it)",
                    unmapped=True,
                )
                counts.openings_reported += 1
                continue
            target = sides[0]
            adjacent = sides[1] if len(sides) > 1 else None
            model.spaces[target].openings.append(
                SpaceOpening(
                    id=opening_id,
                    tag=tag,
                    category=kind,
                    width_m=width,
                    height_m=height,
                    sill_m=sill,
                    head_m=sill + height,
                    host_facade=info["facade"],
                    host_interval_m=[local, local + width],
                    s_center_m=s_center,
                    area_m2=width * height,
                    provenance=_provenance(proposal_id, opening_id),
                    needs_review=False,
                    adjacent_space_id=adjacent,
                )
            )
            counts.openings_mapped += 1
        counts.walls_mapped += 1

    # --- slabs -------------------------------------------------------------
    for slab in level.get("slabs", []):
        counts.slabs_proposed += 1
        slab_id = str(slab.get("id", ""))
        ring = [[float(p[0]), float(p[1])] for p in slab.get("polygon", [])]
        area = _polygon_area(ring)
        thickness = float(slab.get("thickness_m", 0.0))
        base_offset = float(slab.get("base_offset_m", 0.0))
        cx = sum(p[0] for p in ring) / len(ring) if ring else 0.0
        cy = sum(p[1] for p in ring) / len(ring) if ring else 0.0
        model.bim_elements.append(
            BimElement(
                global_id=slab_id,
                ifc_class="IfcSlab",
                name=slab_id,
                level_id=level_id,
                length_m=_perimeter(ring),
                height_m=thickness,
                thickness_m=thickness,
                area_m2=area,
                volume_m3=area * thickness,
                placement_m=[cx, cy, elevation + base_offset],
                provenance=_provenance(proposal_id, slab_id),
            )
        )
        counts.slabs_mapped += 1


def _largest_share(shares: dict[str, int]) -> str:
    """The space with the largest share of a segment (ties break on the id)."""
    if not shares:
        return ""
    return sorted(shares.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _bordering_spaces(
    a: list,
    b: list,
    s_center: float,
    thickness: float,
    info: dict,
    spaces: list[tuple[str, list]],
) -> list[str]:
    """The spaces bordering a segment at one position, sorted by id."""
    normal = info["normal"]
    found: list[str] = []
    if info["is_exterior"]:
        space_id = _space_at(a, b, s_center, thickness, info["inner_side"], normal, spaces)
        if space_id is not None:
            found.append(space_id)
    else:
        for side in (1, -1):
            space_id = _space_at(a, b, s_center, thickness, side, normal, spaces)
            if space_id is not None and space_id not in found:
                found.append(space_id)
    return sorted(found)


def _wall_thickness(wall: dict, wall_types: dict) -> tuple[float, list[dict]]:
    """The thickness of a wall and its material layers."""
    layers: list[dict] = []
    type_ref = wall.get("type_ref")
    if isinstance(type_ref, str):
        wall_type = wall_types.get(type_ref)
        if isinstance(wall_type, dict):
            for layer in wall_type.get("layers", []):
                if isinstance(layer, dict):
                    layers.append(
                        {
                            "material": str(layer.get("material", "")),
                            "thickness_m": float(layer.get("thickness_m", 0.0)),
                        }
                    )
    if "thickness_m" in wall:
        return float(wall["thickness_m"]), layers
    return sum(layer["thickness_m"] for layer in layers), layers


def _level_bounds(level: dict) -> dict:
    """The plan bounding box of a level's wall points (the facade frame)."""
    points = [p for wall in level.get("walls", []) for p in wall.get("points", [])]
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    if not points:
        return {"minx": 0.0, "maxx": 0.0, "miny": 0.0, "maxy": 0.0}
    return {"minx": min(xs), "maxx": max(xs), "miny": min(ys), "maxy": max(ys)}
