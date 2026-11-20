"""The proposal validator: a pure function from parsed JSON to diagnostics.

``validate_proposal(doc)`` checks a parsed building proposal against the
version 1 contract (``proposal.schema``) and returns the diagnostics an
agent loop needs to repair the document. It is pure: no file, network or
model access, no mutation of the input, and no dependency on the rest of
the repo (stdlib plus shapely, which the repo already depends on, for the
polygon checks).

Every diagnostic carries:

``path``
    a JSON path into the document, e.g. ``levels[0].walls[0].openings[2].offset_m``,
``code``
    a stable machine code (see ``ERROR_CODES`` / ``WARNING_CODES``),
``severity``
    ``"error"`` or ``"warning"``,
``message``
    one sentence with the numbers in it, and
``details``
    machine-usable numbers where they exist.

The validator never repairs, never drops an element and never invents an
id. A document with any error is not mapped at all (``proposal.mapper``
refuses it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .schema import DOCUMENT, Node

#: Every error code the validator can raise in version 1.
ERROR_CODES = (
    "unknown-field",
    "missing-field",
    "wrong-type",
    "unsupported-version",
    "unsupported-units",
    "duplicate-id",
    "duplicate-room-number",
    "bad-polygon",
    "space-overlap",
    "wall-too-short",
    "opening-out-of-range",
    "opening-straddles-vertex",
    "opening-overlap",
    "opening-does-not-fit",
    "unknown-type-ref",
    "layer-sum-mismatch",
)

#: Every warning code the validator can raise in version 1.
WARNING_CODES = (
    "opening-tag-absent",
    "wall-height-defaulted",
)

#: Length tolerance for the geometric comparisons, in metres.
EPS = 1e-9


@dataclass
class Diagnostic:
    """One finding about a proposal document."""

    path: str
    code: str
    severity: str  # "error" | "warning"
    message: str
    details: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Every message is one sentence: it ends with a full stop.
        if self.message and not self.message.endswith("."):
            self.message = f"{self.message}."

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass
class ProposalValidation:
    """The result of :func:`validate_proposal`."""

    ok: bool
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": [d.as_dict() for d in self.errors],
            "warnings": [d.as_dict() for d in self.warnings],
        }


# --- small helpers ---------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return type(value).__name__


def _label(path: str) -> str:
    """How a path reads inside a message."""
    return "the proposal document" if not path else f"'{path}'"


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _fmt(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _clean_ring(points: list) -> Optional[list]:
    """``[[x, y], ...]`` from a list of points, or None when it is not usable."""
    ring: list[list[float]] = []
    for point in points:
        if not isinstance(point, list) or len(point) < 2:
            return None
        if not (_is_number(point[0]) and _is_number(point[1])):
            return None
        ring.append([float(point[0]), float(point[1])])
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    return ring


def _shoelace_area(ring: list) -> float:
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _shapely_polygon(ring: list):
    """A shapely polygon for a ring, or None when shapely cannot build one."""
    try:
        from shapely.geometry import Polygon
    except ImportError:  # pragma: no cover - shapely is a declared dependency
        return None
    try:
        return Polygon(ring)
    except Exception:  # noqa: BLE001 - a malformed ring is reported, not raised
        return None


def _polygon_is_simple(ring: list) -> bool:
    """True when the ring does not cross itself."""
    poly = _shapely_polygon(ring)
    if poly is None:
        return True
    try:
        return bool(poly.is_valid)
    except Exception:  # noqa: BLE001 - never raise out of the validator
        return True


def _polygons_overlap(ring_a: list, ring_b: list) -> bool:
    """True when two rings share interior area (touching is not overlapping)."""
    poly_a = _shapely_polygon(ring_a)
    poly_b = _shapely_polygon(ring_b)
    if poly_a is None or poly_b is None:
        return False
    try:
        return float(poly_a.intersection(poly_b).area) > 1e-9
    except Exception:  # noqa: BLE001 - never raise out of the validator
        return False


# --- generic field checks ---------------------------------------------------


def _check_value(value: Any, node: Node, path: str, errors: list[Diagnostic]) -> None:
    """Type, enum and range checks for one field value."""
    kind = node.type
    if kind == "string":
        if not isinstance(value, str):
            errors.append(
                Diagnostic(
                    path,
                    "wrong-type",
                    "error",
                    f"{_label(path)} must be a string, got {_type_name(value)}",
                )
            )
            return
    elif kind == "number":
        if not _is_number(value):
            errors.append(
                Diagnostic(
                    path,
                    "wrong-type",
                    "error",
                    f"{_label(path)} must be a number, got {_type_name(value)}",
                )
            )
            return
        number = float(value)
        if node.exclusive_min is not None and number <= node.exclusive_min:
            errors.append(
                Diagnostic(
                    path,
                    node.range_code,
                    "error",
                    f"{_label(path)} must be greater than {_fmt(node.exclusive_min)}, "
                    f"got {_fmt(number)}",
                )
            )
            return
        if node.minimum is not None and number < node.minimum:
            errors.append(
                Diagnostic(
                    path,
                    node.range_code,
                    "error",
                    f"{_label(path)} must be at least {_fmt(node.minimum)}, got {_fmt(number)}",
                )
            )
            return
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(
                Diagnostic(
                    path,
                    "wrong-type",
                    "error",
                    f"{_label(path)} must be an integer, got {_type_name(value)}",
                )
            )
            return
    elif kind == "boolean":
        if not isinstance(value, bool):
            errors.append(
                Diagnostic(
                    path,
                    "wrong-type",
                    "error",
                    f"{_label(path)} must be a boolean, got {_type_name(value)}",
                )
            )
            return
    elif kind == "object":
        if not isinstance(value, dict):
            errors.append(
                Diagnostic(
                    path,
                    "wrong-type",
                    "error",
                    f"{_label(path)} must be an object, got {_type_name(value)}",
                )
            )
            return
        if node.free_keys:
            return
        if node.values is not None:
            for key, item in value.items():
                _check_value(item, node.values, _join(path, str(key)), errors)
            return
        _check_object(value, node, path, errors)
        return
    elif kind == "array":
        if not isinstance(value, list):
            errors.append(
                Diagnostic(
                    path,
                    "wrong-type",
                    "error",
                    f"{_label(path)} must be an array, got {_type_name(value)}",
                )
            )
            return
        if node.min_items is not None and len(value) < node.min_items:
            errors.append(
                Diagnostic(
                    path,
                    node.min_items_code,
                    "error",
                    f"{_label(path)} needs at least {node.min_items} items, got {len(value)}",
                )
            )
            return
        for index, item in enumerate(value):
            _check_value(item, node.item, f"{path}[{index}]", errors)
        return
    if node.enum is not None and value not in node.enum:
        errors.append(
            Diagnostic(
                path,
                node.enum_code or "wrong-type",
                "error",
                f"{_label(path)} must be one of {list(node.enum)}, got {value!r}",
            )
        )


def _check_object(value: Any, node: Node, path: str, errors: list[Diagnostic]) -> bool:
    """Field-level checks for one object against its table.

    Returns True when the value is an object the deeper checks may read.
    """
    if not isinstance(value, dict):
        errors.append(
            Diagnostic(
                path,
                "wrong-type",
                "error",
                f"{_label(path)} must be an object, got {_type_name(value)}",
            )
        )
        return False
    known = {f.name: f for f in node.fields}
    for key in value:
        if key in known or node.free_keys or node.values is not None:
            continue
        errors.append(
            Diagnostic(
                _join(path, str(key)),
                "unknown-field",
                "error",
                f"unknown field {key!r} in {_label(path)}; allowed fields are {sorted(known)}",
            )
        )
    for f in node.fields:
        if f.name not in value:
            if f.required:
                errors.append(
                    Diagnostic(
                        _join(path, f.name),
                        "missing-field",
                        "error",
                        f"{_label(_join(path, f.name))} is required",
                    )
                )
            continue
        _check_value(value[f.name], f, _join(path, f.name), errors)
    for group in node.one_of_required:
        if not any(name in value for name in group):
            errors.append(
                Diagnostic(
                    path,
                    "missing-field",
                    "error",
                    f"{_label(path)} needs one of {list(group)}; none of them is present",
                )
            )
    for cond_field, cond_value, required_field in node.conditional_required:
        if value.get(cond_field) == cond_value and required_field not in value:
            errors.append(
                Diagnostic(
                    _join(path, required_field),
                    "missing-field",
                    "error",
                    f"{_label(_join(path, required_field))} is required when "
                    f"{cond_field} is {cond_value!r}",
                )
            )
    return True


# --- element checks ---------------------------------------------------------


def _validate_polygon(points: Any, path: str, errors: list[Diagnostic]) -> Optional[list]:
    """Check one polygon; returns the cleaned ring when it is usable."""
    if not isinstance(points, list):
        return None
    if len(points) < 3:
        return None  # the table check already reported the short array
    ring = _clean_ring(points)
    if ring is None or len(ring) < 3:
        return None  # the table check already reported the bad point
    area = _shoelace_area(ring)
    if area <= 1e-9:
        errors.append(
            Diagnostic(
                path,
                "bad-polygon",
                "error",
                f"the polygon at {_label(path)} encloses no area ({_fmt(area)} m2)",
            )
        )
        return None
    if not _polygon_is_simple(ring):
        errors.append(
            Diagnostic(
                path,
                "bad-polygon",
                "error",
                f"the polygon at {_label(path)} crosses itself",
            )
        )
        return None
    return ring


def _wall_segments(points: Any, closed: bool) -> tuple[list, bool]:
    """The segments of a wall run, and whether the run repeats its first point."""
    if not isinstance(points, list) or len(points) < 2:
        return [], False
    pts: list[list[float]] = []
    for point in points:
        if not isinstance(point, list) or len(point) < 2:
            return [], False
        if not (_is_number(point[0]) and _is_number(point[1])):
            return [], False
        pts.append([float(point[0]), float(point[1])])
    repeats = len(pts) > 2 and pts[0] == pts[-1]
    if repeats:
        closed = False
    segments = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    if closed and len(pts) > 2:
        segments.append((pts[-1], pts[0]))
    return segments, repeats


def _validate_wall(
    wall: dict,
    path: str,
    level_height: Optional[float],
    wall_types: dict,
    errors: list[Diagnostic],
    warnings: list[Diagnostic],
    seen_opening_ids: dict,
) -> None:
    """The wall-specific checks: thickness, type reference, height, openings."""
    wall_id = wall.get("id")
    label = wall_id if isinstance(wall_id, str) else path

    thickness = wall.get("thickness_m")
    type_ref = wall.get("type_ref")
    if isinstance(type_ref, str):
        wall_type = wall_types.get(type_ref)
        if not isinstance(wall_type, dict):
            errors.append(
                Diagnostic(
                    _join(path, "type_ref"),
                    "unknown-type-ref",
                    "error",
                    f"wall {label!r} refers to wall type {type_ref!r}, which the "
                    f"document does not define",
                )
            )
        else:
            layers = wall_type.get("layers")
            total = 0.0
            if isinstance(layers, list):
                for layer in layers:
                    if isinstance(layer, dict) and _is_number(layer.get("thickness_m")):
                        total += float(layer["thickness_m"])
            if _is_number(thickness) and abs(total - float(thickness)) > 1e-6:
                errors.append(
                    Diagnostic(
                        _join(path, "thickness_m"),
                        "layer-sum-mismatch",
                        "error",
                        f"the layers of wall type {type_ref!r} sum to {_fmt(total)} m but "
                        f"wall {label!r} is {_fmt(float(thickness))} m thick",
                        {
                            "wall_type": type_ref,
                            "layer_sum_m": total,
                            "thickness_m": float(thickness),
                        },
                    )
                )

    wall_height = wall.get("height_m")
    if not _is_number(wall_height):
        if level_height is not None:
            warnings.append(
                Diagnostic(
                    _join(path, "height_m"),
                    "wall-height-defaulted",
                    "warning",
                    f"wall {label!r} has no height_m and takes the level height "
                    f"{_fmt(level_height)} m",
                    {"level_height_m": level_height},
                )
            )
        wall_height = level_height

    segments, repeats = _wall_segments(wall.get("points"), bool(wall.get("closed")))
    if repeats:
        errors.append(
            Diagnostic(
                _join(path, "points"),
                "wrong-type",
                "error",
                f"closed wall {label!r} repeats its first point at the end; the closing "
                f"segment is added by the mapper, so the point must not be repeated",
            )
        )
    lengths = [math.dist(a, b) for a, b in segments]
    run_length = sum(lengths)
    starts: list[float] = []
    acc = 0.0
    for length in lengths:
        starts.append(acc)
        acc += length

    openings = wall.get("openings")
    if not isinstance(openings, list):
        return
    placed: list[tuple[str, float, float]] = []
    for index, opening in enumerate(openings):
        opening_path = f"{path}.openings[{index}]"
        if not isinstance(opening, dict):
            continue
        opening_id = opening.get("id")
        if isinstance(opening_id, str):
            if opening_id in seen_opening_ids:
                errors.append(
                    Diagnostic(
                        _join(opening_path, "id"),
                        "duplicate-id",
                        "error",
                        f"opening id {opening_id!r} is used more than once in the document",
                    )
                )
            seen_opening_ids[opening_id] = opening_path

        if not opening.get("tag"):
            warnings.append(
                Diagnostic(
                    _join(opening_path, "tag"),
                    "opening-tag-absent",
                    "warning",
                    f"opening {opening_id!r} carries no tag, so it cannot join to a schedule",
                )
            )

        offset = opening.get("offset_m")
        width = opening.get("width_m")
        height = opening.get("height_m")
        if not (_is_number(offset) and _is_number(width) and _is_number(height)):
            continue
        offset = float(offset)
        width = float(width)
        height = float(height)
        sill = float(opening["sill_m"]) if _is_number(opening.get("sill_m")) else 0.0

        if width > run_length + EPS:
            errors.append(
                Diagnostic(
                    _join(opening_path, "width_m"),
                    "wall-too-short",
                    "error",
                    f"opening {opening_id!r} is {_fmt(width)} m wide but wall {label!r} "
                    f"runs only {_fmt(run_length)} m",
                    {"run_length_m": run_length, "opening_width_m": width},
                )
            )
            continue
        if offset < -EPS or offset + width > run_length + EPS:
            errors.append(
                Diagnostic(
                    _join(opening_path, "offset_m"),
                    "opening-out-of-range",
                    "error",
                    f"opening {opening_id!r} spans {_fmt(offset)} to {_fmt(offset + width)} m "
                    f"along a run of {_fmt(run_length)} m",
                    {
                        "offset_m": offset,
                        "width_m": width,
                        "run_length_m": run_length,
                    },
                )
            )
            continue
        crossed = [s for s in starts[1:] if offset + EPS < s < offset + width - EPS]
        if crossed:
            errors.append(
                Diagnostic(
                    _join(opening_path, "offset_m"),
                    "opening-straddles-vertex",
                    "error",
                    f"opening {opening_id!r} spans {_fmt(offset)} to {_fmt(offset + width)} m "
                    f"along the run, crossing the vertex at {_fmt(crossed[0])} m; an opening "
                    f"must sit inside one segment",
                    {
                        "offset_m": offset,
                        "width_m": width,
                        "vertex_m": crossed[0],
                        "run_length_m": run_length,
                    },
                )
            )
        for other_id, other_start, other_end in placed:
            if offset < other_end - EPS and other_start < offset + width - EPS:
                errors.append(
                    Diagnostic(
                        _join(opening_path, "offset_m"),
                        "opening-overlap",
                        "error",
                        f"opening {opening_id!r} spans {_fmt(offset)} to "
                        f"{_fmt(offset + width)} m and overlaps opening {other_id!r} at "
                        f"{_fmt(other_start)} to {_fmt(other_end)} m",
                        {
                            "offset_m": offset,
                            "width_m": width,
                            "other_opening_id": other_id,
                            "other_interval_m": [other_start, other_end],
                        },
                    )
                )
        placed.append((opening_id if isinstance(opening_id, str) else "", offset, offset + width))

        if _is_number(wall_height) and sill + height > float(wall_height) + EPS:
            max_sill = float(wall_height) - height
            errors.append(
                Diagnostic(
                    _join(opening_path, "sill_m"),
                    "opening-does-not-fit",
                    "error",
                    f"opening {opening_id!r} reaches {_fmt(sill + height)} m above the wall "
                    f"base, above the wall height {_fmt(float(wall_height))} m",
                    {
                        "sill_m": sill,
                        "height_m": height,
                        "wall_height_m": float(wall_height),
                        "fitting_range_m": [0.0, max_sill],
                        "nearest_fitting_offset_m": max(0.0, min(sill, max_sill)),
                    },
                )
            )


def _validate_level(
    level: dict,
    path: str,
    wall_types: dict,
    errors: list[Diagnostic],
    warnings: list[Diagnostic],
    seen_wall_ids: dict,
    seen_opening_ids: dict,
    seen_slab_ids: set,
) -> None:
    """The level-specific checks: walls, spaces and slabs."""
    level_height = level.get("height_m") if _is_number(level.get("height_m")) else None

    walls = level.get("walls")
    if isinstance(walls, list):
        for index, wall in enumerate(walls):
            wall_path = f"{path}.walls[{index}]"
            if not isinstance(wall, dict):
                continue
            wall_id = wall.get("id")
            if isinstance(wall_id, str):
                if wall_id in seen_wall_ids:
                    errors.append(
                        Diagnostic(
                            _join(wall_path, "id"),
                            "duplicate-id",
                            "error",
                            f"wall id {wall_id!r} is used more than once in the document",
                        )
                    )
                seen_wall_ids[wall_id] = wall_path
            _validate_wall(
                wall,
                wall_path,
                level_height,
                wall_types,
                errors,
                warnings,
                seen_opening_ids,
            )

    spaces = level.get("spaces")
    if isinstance(spaces, list):
        numbers: dict[str, str] = {}
        rings: list[tuple[str, list]] = []
        for index, space in enumerate(spaces):
            space_path = f"{path}.spaces[{index}]"
            if not isinstance(space, dict):
                continue
            number = space.get("number")
            if isinstance(number, str):
                if number in numbers:
                    errors.append(
                        Diagnostic(
                            _join(space_path, "number"),
                            "duplicate-room-number",
                            "error",
                            f"room number {number!r} is used twice on level {level.get('id')!r}",
                        )
                    )
                numbers[number] = space_path
            ring = _validate_polygon(space.get("polygon"), _join(space_path, "polygon"), errors)
            if ring is not None:
                rings.append((space_path, ring))
        for i in range(len(rings)):
            for j in range(i + 1, len(rings)):
                if _polygons_overlap(rings[i][1], rings[j][1]):
                    errors.append(
                        Diagnostic(
                            _join(rings[j][0], "polygon"),
                            "space-overlap",
                            "error",
                            f"the space at {rings[j][0]} overlaps the space at {rings[i][0]}",
                            {
                                "space_path": rings[j][0],
                                "other_space_path": rings[i][0],
                            },
                        )
                    )

    slabs = level.get("slabs")
    if isinstance(slabs, list):
        for index, slab in enumerate(slabs):
            slab_path = f"{path}.slabs[{index}]"
            if not isinstance(slab, dict):
                continue
            slab_id = slab.get("id")
            if isinstance(slab_id, str):
                if slab_id in seen_slab_ids:
                    errors.append(
                        Diagnostic(
                            _join(slab_path, "id"),
                            "duplicate-id",
                            "error",
                            f"slab id {slab_id!r} is used more than once in the document",
                        )
                    )
                seen_slab_ids.add(slab_id)
            _validate_polygon(slab.get("polygon"), _join(slab_path, "polygon"), errors)


# --- the validator ----------------------------------------------------------


def validate_proposal(doc: dict) -> ProposalValidation:
    """Check a parsed building proposal and return its diagnostics.

    The function is pure: it reads the document, never writes to it, never
    touches the filesystem and never builds a model. ``ok`` is True when
    there is no error diagnostic; warnings never make a document invalid.
    """
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    if not isinstance(doc, dict):
        errors.append(
            Diagnostic(
                "",
                "wrong-type",
                "error",
                f"the proposal document must be a JSON object, got {_type_name(doc)}",
            )
        )
        return ProposalValidation(ok=False, errors=errors, warnings=warnings)

    _check_object(doc, DOCUMENT, "", errors)

    wall_types = doc.get("wallTypes")
    if not isinstance(wall_types, dict):
        wall_types = {}
    else:
        for type_id, wall_type in wall_types.items():
            type_path = _join("wallTypes", str(type_id))
            if not isinstance(wall_type, dict):
                continue
            layers = wall_type.get("layers")
            if not isinstance(layers, list):
                continue
            total = 0.0
            for layer in layers:
                if isinstance(layer, dict) and _is_number(layer.get("thickness_m")):
                    total += float(layer["thickness_m"])
            if total <= 0.0:
                errors.append(
                    Diagnostic(
                        _join(type_path, "layers"),
                        "layer-sum-mismatch",
                        "error",
                        f"the layers of wall type {type_id!r} sum to {_fmt(total)} m; the "
                        f"total must be positive",
                        {"wall_type": str(type_id), "layer_sum_m": total},
                    )
                )

    seen_level_ids: set[str] = set()
    seen_wall_ids: dict[str, str] = {}
    seen_opening_ids: dict[str, str] = {}
    seen_slab_ids: set[str] = set()

    levels = doc.get("levels")
    if isinstance(levels, list):
        for index, level in enumerate(levels):
            level_path = f"levels[{index}]"
            if not isinstance(level, dict):
                continue
            level_id = level.get("id")
            if isinstance(level_id, str):
                if level_id in seen_level_ids:
                    errors.append(
                        Diagnostic(
                            _join(level_path, "id"),
                            "duplicate-id",
                            "error",
                            f"level id {level_id!r} is used more than once in the document",
                        )
                    )
                seen_level_ids.add(level_id)
            _validate_level(
                level,
                level_path,
                wall_types,
                errors,
                warnings,
                seen_wall_ids,
                seen_opening_ids,
                seen_slab_ids,
            )

    return ProposalValidation(ok=not errors, errors=errors, warnings=warnings)
