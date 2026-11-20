"""Deterministic quantity takeoff -> priced estimate for the canonical model (#1419).

Implements the #1418 spike (Phase 0 inventory/rules/schemas, Phase 1 scratch
implementation, all seven gates) as a package module over the real
``BuildingModel`` dataclass. Pure functions: a model plus a price book plus a
finishes schedule go in, a deterministic estimate structure comes out
(dataclasses with ``to_dict`` / ``to_json``, sorted keys, byte-identical
re-runs). No LLM anywhere in the quantity or pricing path, and no new runtime
dependency.

House invariants (merge-blocking, per #1419):

* **No silent defaults anywhere.** Every quantity names its source (including
  the height source: ``Space.ceiling_height_m`` vs ``Space.height_m`` vs
  ``Level.wall_height_m``, with dataclass-default heights flagged), and every
  missing input produces an ``unpriced`` / ``unquantified`` line in place with
  a machine-readable reason (``no_coverage``, ``no_price_item``,
  ``no_construction``, ...), following the ``constructions.py`` left-out
  pattern.
* Quantities are computed by code from model geometry only; re-pricing with a
  newer price book changes prices, never quantities.
* Every quantity and every priced line carries provenance to the model
  element ids, the rule, and the price-book entry + version that produced it.

Design decisions D1-D6 from #1419 are implemented as written there: roof
reports both the space-polygon union and the gross footprint and prices the
gross (D2); envelope net uses cumulative interval matching with straddle
splits recorded (D3); ``labour_basis`` is a price-book field and an item with
labour hours but no basis is refused at load (D5); partition edge pairing is
general segment pairing, diagonals included (D4); partition basis is an
explicit estimate option, ``faces`` (default) or ``assembly``, and a mixed or
unstated basis is a validation error (D6).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from building_model import BuildingModel, Space
from geometry.geometry_kernel import _shoelace
from geometry.geometry_simplify import footprint_multipart_from_regions, ring_perimeter

EPS = 1e-9
COLLINEAR_TOL_M = 0.001  # Phase 0 s.2: same supporting line within 1 mm
MIN_OVERLAP_M = 0.05  # Phase 0 s.2: minimum pairing overlap
MAX_OFFSET_M = 0.30  # Phase 0 s.2: inner-face island offset ceiling
DRIFT_FLAG_PCT = 1.0  # stored-vs-recomputed floor area drift worth flagging
UNMATCHED_FLAG_PCT = 5.0  # Phase 0 s.2: level failure flag for unmatched edges

__all__ = [
    "Estimate",
    "EstimateLine",
    "EstimateValidationError",
    "FinishesError",
    "FinishesSchedule",
    "FinishRow",
    "PriceBook",
    "PriceBookError",
    "PriceBookItem",
    "TakeoffQuantities",
    "build_estimate",
    "compute_quantities",
    "finishes_from_dict",
    "load_finishes_schedule",
    "load_price_book",
    "price_book_from_dict",
    "validate_estimate_dict",
]


def _r6(x: float) -> float:
    return round(float(x), 6)


def _r2(x: float) -> float:
    return round(float(x), 2)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PriceBookError(ValueError):
    """A price book document was refused at load (see loader rules)."""


class FinishesError(ValueError):
    """A finishes schedule document was refused at load (see loader rules)."""


class EstimateValidationError(ValueError):
    """An estimate structure failed validation (e.g. mixed partition basis)."""


# ---------------------------------------------------------------------------
# Price book (Phase 0 section 4, hardened per #1419 D5)
# ---------------------------------------------------------------------------

_PRICE_BOOK_HEADER_KEYS = frozenset(
    {"price_book_version", "currency", "region", "source", "source_date", "kind"}
)
_PRICE_BOOK_ITEM_KEYS = frozenset(
    {
        "item_id",
        "description",
        "category",
        "unit",
        "material_unit_price",
        "labour_hours_per_unit",
        "labour_rate_per_hour",
        "labour_basis",
        "waste_pct",
        "spare_pct",
        "coverage_m2_per_l",
        "coats",
        "container_l",
        "applies_to",
        "source",
        "source_date",
    }
)
_APPLIES_TO_KEYS = frozenset({"finish_id", "construction_id", "category", "tag", "kind"})
_PRICE_UNITS = frozenset({"m2", "m", "count", "l"})
LABOUR_BASES = frozenset({"priced_unit", "quantity_unit"})


@dataclass(frozen=True)
class PriceBookItem:
    """One priced item. ``labour_basis`` (D5) says which quantity the labour
    hours multiply: the priced quantity (``priced_unit``, e.g. per purchasable
    litre) or the raw measured quantity (``quantity_unit``, e.g. per m2)."""

    item_id: str
    category: str
    unit: str
    material_unit_price: float
    applies_to: dict
    source: str
    source_date: str
    description: str = ""
    labour_hours_per_unit: Optional[float] = None
    labour_rate_per_hour: Optional[float] = None
    labour_basis: Optional[str] = None
    waste_pct: Optional[float] = None
    spare_pct: Optional[float] = None
    coverage_m2_per_l: Optional[float] = None
    coats: Optional[float] = None
    container_l: Optional[float] = None


@dataclass(frozen=True)
class PriceBook:
    """A versioned price book. Currency and region are per-book, stated once
    in the header, never per-row defaults."""

    price_book_version: str
    currency: str
    region: str
    source: str
    source_date: str
    items: tuple[PriceBookItem, ...]
    kind: str = ""

    def item_by_id(self, item_id: str) -> Optional[PriceBookItem]:
        for item in self.items:
            if item.item_id == item_id:
                return item
        return None

    def find_by_applies_to(self, key: str, value: str) -> Optional[PriceBookItem]:
        for item in self.items:
            if item.applies_to.get(key) == value:
                return item
        return None


def _refuse_unknown(keys: set, allowed: frozenset, where: str, exc: type[ValueError]) -> None:
    unknown = sorted(keys - allowed)
    if unknown:
        raise exc(f"{where}: unknown keys refused: {', '.join(unknown)}")


def price_book_from_dict(doc: dict) -> PriceBook:
    """Parse and validate a price book document. Refuses (raises
    ``PriceBookError``) on: unknown keys anywhere, a header or item with no
    source / source_date, a duplicate item id, an unknown unit, and (D5) an
    item carrying labour hours with no ``labour_basis``."""
    if not isinstance(doc, dict):
        raise PriceBookError("price book document must be a JSON object")
    _refuse_unknown(set(doc), frozenset({"header", "items"}), "price book", PriceBookError)
    header = doc.get("header")
    if not isinstance(header, dict):
        raise PriceBookError("price book header missing or not an object")
    _refuse_unknown(set(header), _PRICE_BOOK_HEADER_KEYS, "price book header", PriceBookError)
    for key in ("price_book_version", "currency", "region", "source", "source_date"):
        if not header.get(key):
            raise PriceBookError(f"price book header missing {key} (no source, no load)")
    raw_items = doc.get("items")
    if not isinstance(raw_items, list):
        raise PriceBookError("price book items missing or not a list")
    items: list[PriceBookItem] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise PriceBookError("price book item must be an object")
        _refuse_unknown(set(raw), _PRICE_BOOK_ITEM_KEYS, "price book item", PriceBookError)
        item_id = raw.get("item_id")
        if not item_id:
            raise PriceBookError("price book item missing item_id")
        if item_id in seen:
            raise PriceBookError(f"duplicate price book item_id: {item_id}")
        seen.add(item_id)
        if not raw.get("source") or not raw.get("source_date"):
            raise PriceBookError(f"price item {item_id} missing source/source_date (refused)")
        if raw.get("unit") not in _PRICE_UNITS:
            raise PriceBookError(
                f"price item {item_id}: unit must be one of {sorted(_PRICE_UNITS)}"
            )
        if raw.get("material_unit_price") is None:
            raise PriceBookError(f"price item {item_id} missing material_unit_price")
        applies_to = raw.get("applies_to")
        if not isinstance(applies_to, dict) or not applies_to:
            raise PriceBookError(f"price item {item_id} missing applies_to")
        _refuse_unknown(
            set(applies_to), _APPLIES_TO_KEYS, f"item {item_id} applies_to", PriceBookError
        )
        if raw.get("labour_hours_per_unit") is not None:
            if raw.get("labour_basis") not in LABOUR_BASES:
                raise PriceBookError(
                    f"price item {item_id} carries labour hours but no labour_basis "
                    f"(must be one of {sorted(LABOUR_BASES)}); refused per D5"
                )
        elif raw.get("labour_basis") is not None and raw.get("labour_basis") not in LABOUR_BASES:
            raise PriceBookError(
                f"price item {item_id}: unknown labour_basis {raw['labour_basis']}"
            )
        items.append(
            PriceBookItem(
                item_id=item_id,
                category=str(raw.get("category", "")),
                unit=str(raw["unit"]),
                material_unit_price=float(raw["material_unit_price"]),
                applies_to=dict(applies_to),
                source=str(raw["source"]),
                source_date=str(raw["source_date"]),
                description=str(raw.get("description", "")),
                labour_hours_per_unit=raw.get("labour_hours_per_unit"),
                labour_rate_per_hour=raw.get("labour_rate_per_hour"),
                labour_basis=raw.get("labour_basis"),
                waste_pct=raw.get("waste_pct"),
                spare_pct=raw.get("spare_pct"),
                coverage_m2_per_l=raw.get("coverage_m2_per_l"),
                coats=raw.get("coats"),
                container_l=raw.get("container_l"),
            )
        )
    return PriceBook(
        price_book_version=str(header["price_book_version"]),
        currency=str(header["currency"]),
        region=str(header["region"]),
        source=str(header["source"]),
        source_date=str(header["source_date"]),
        items=tuple(items),
        kind=str(header.get("kind", "")),
    )


def load_price_book(path: str | Path) -> PriceBook:
    """Load a price book JSON file, refusing invalid documents."""
    return price_book_from_dict(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------------------
# Finishes schedule (Phase 0 section 4)
# ---------------------------------------------------------------------------

_FINISHES_HEADER_KEYS = frozenset({"finishes_version", "source", "source_date"})
_FINISH_ROW_KEYS = frozenset({"space_id", "surface", "finish_id"})
_SURFACES = frozenset({"floor", "wall", "ceiling"})


@dataclass(frozen=True)
class FinishRow:
    space_id: str
    surface: str  # "floor" | "wall" | "ceiling"
    finish_id: str


@dataclass(frozen=True)
class FinishesSchedule:
    """Versioned estimate-side input keyed by space id. A space/surface with
    no row is unassigned: its areas still appear as quantities, unpriced."""

    finishes_version: str
    source: str
    source_date: str
    rows: tuple[FinishRow, ...]

    def finish_for(self, space_id: str, surface: str) -> Optional[str]:
        for row in self.rows:
            if row.space_id == space_id and row.surface == surface:
                return row.finish_id
        return None


def finishes_from_dict(doc: dict) -> FinishesSchedule:
    """Parse and validate a finishes schedule. Refuses unknown keys, a header
    with no source / source_date, an unknown surface, and a duplicate
    (space, surface) assignment."""
    if not isinstance(doc, dict):
        raise FinishesError("finishes document must be a JSON object")
    _refuse_unknown(set(doc), frozenset({"header", "rows"}), "finishes", FinishesError)
    header = doc.get("header")
    if not isinstance(header, dict):
        raise FinishesError("finishes header missing or not an object")
    _refuse_unknown(set(header), _FINISHES_HEADER_KEYS, "finishes header", FinishesError)
    for key in ("finishes_version", "source", "source_date"):
        if not header.get(key):
            raise FinishesError(f"finishes header missing {key} (no source, no load)")
    raw_rows = doc.get("rows")
    if not isinstance(raw_rows, list):
        raise FinishesError("finishes rows missing or not a list")
    rows: list[FinishRow] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise FinishesError("finishes row must be an object")
        _refuse_unknown(set(raw), _FINISH_ROW_KEYS, "finishes row", FinishesError)
        space_id, surface, finish_id = raw.get("space_id"), raw.get("surface"), raw.get("finish_id")
        if not space_id or not finish_id:
            raise FinishesError("finishes row missing space_id or finish_id")
        if surface not in _SURFACES:
            raise FinishesError(f"finishes row for {space_id}: unknown surface {surface!r}")
        if (space_id, surface) in seen:
            raise FinishesError(f"duplicate finishes assignment for {space_id}/{surface}")
        seen.add((space_id, surface))
        rows.append(
            FinishRow(space_id=str(space_id), surface=str(surface), finish_id=str(finish_id))
        )
    return FinishesSchedule(
        finishes_version=str(header["finishes_version"]),
        source=str(header["source"]),
        source_date=str(header["source_date"]),
        rows=tuple(rows),
    )


def load_finishes_schedule(path: str | Path) -> FinishesSchedule:
    """Load a finishes schedule JSON file, refusing invalid documents."""
    return finishes_from_dict(json.loads(Path(path).read_text()))


# ---------------------------------------------------------------------------
# Geometry helpers: general straight-segment pairing (D4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SegFrame:
    """Canonical frame for one straight segment: unit direction ``u`` (sign
    canonicalised), unit normal ``n``, signed line offset ``d = n . p``,
    interval ``[t0, t1]`` along ``u``, and whether canonicalisation flipped
    the segment's own direction (two facing edges traverse a shared boundary
    in opposite directions, so exactly one of them flips)."""

    ux: float
    uy: float
    d: float
    t0: float
    t1: float
    flipped: bool

    @property
    def length(self) -> float:
        return self.t1 - self.t0


def _seg_frame(p: list, q: list) -> Optional[_SegFrame]:
    vx, vy = float(q[0]) - float(p[0]), float(q[1]) - float(p[1])
    length = math.hypot(vx, vy)
    if length <= EPS:
        return None
    ux, uy = vx / length, vy / length
    flipped = False
    if ux < -EPS or (abs(ux) <= EPS and uy < 0):
        ux, uy, flipped = -ux, -uy, True
    nx, ny = -uy, ux
    d = nx * float(p[0]) + ny * float(p[1])
    tp = ux * float(p[0]) + uy * float(p[1])
    tq = ux * float(q[0]) + uy * float(q[1])
    return _SegFrame(ux, uy, d, min(tp, tq), max(tp, tq), flipped)


def _parallel(a: _SegFrame, b: _SegFrame) -> bool:
    return abs(a.ux * b.uy - a.uy * b.ux) < 1e-7 and (a.ux * b.ux + a.uy * b.uy) > 0


def _subtract_intervals(free: list, a: float, b: float) -> list:
    out = []
    for x, y in free:
        if b <= x or a >= y:
            out.append((x, y))
            continue
        if a > x:
            out.append((x, a))
        if b < y:
            out.append((b, y))
    return [(x, y) for (x, y) in out if y - x > EPS]


def _space_height(space: Space, levels: dict) -> tuple[Optional[float], str, list]:
    """Phase 0 s.1 height hierarchy, with the source recorded.

    ``ceiling_height_m`` -> ``height_m`` -> ``Level.wall_height_m``. A level
    height equal to the dataclass default (3.0 m) is flagged: in a serialized
    model it cannot be distinguished from an unset default, and the estimate
    must not hide that."""
    if space.ceiling_height_m is not None:
        return float(space.ceiling_height_m), "space.ceiling_height_m", []
    if space.height_m is not None:
        flags = []
        lvl = levels.get(space.level_id)
        if lvl is not None and abs(float(lvl.wall_height_m) - 3.0) < EPS:
            flags.append("level_wall_height_equals_dataclass_default_3.0")
        return float(space.height_m), "space.height_m", flags
    lvl = levels.get(space.level_id)
    if lvl is not None:
        flags = ["height_from_level_wall_height_m"]
        if abs(float(lvl.wall_height_m) - 3.0) < EPS:
            flags.append("level_wall_height_indistinguishable_from_dataclass_default")
        return float(lvl.wall_height_m), "level.wall_height_m", flags
    return None, "missing", ["no_height_source"]


def _opening_area(opening) -> Optional[float]:
    if opening.area_m2 is not None:
        return float(opening.area_m2)
    if opening.width_m is not None and opening.height_m is not None:
        return float(opening.width_m) * float(opening.height_m)
    return None


# ---------------------------------------------------------------------------
# Quantities (Phase 0 section 1, rows 1-11)
# ---------------------------------------------------------------------------


@dataclass
class OpeningQuantity:
    opening_id: str
    space_id: str
    adjacent_space_id: Optional[str]
    category: str
    tag: str
    width_m: Optional[float]
    height_m: Optional[float]
    sill_m: Optional[float]
    host_facade: str
    host_interval_m: Optional[list]
    area_m2: Optional[float]
    needs_review: bool
    unquantified_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "opening_id": self.opening_id,
            "space_id": self.space_id,
            "adjacent_space_id": self.adjacent_space_id,
            "category": self.category,
            "tag": self.tag,
            "width_m": self.width_m,
            "height_m": self.height_m,
            "sill_m": self.sill_m,
            "host_facade": self.host_facade,
            "host_interval_m": self.host_interval_m,
            "area_m2": self.area_m2,
            "needs_review": self.needs_review,
            "unquantified_reason": self.unquantified_reason,
            "provenance": {"element_ids": [self.opening_id], "method": "takeoff_estimate.opening"},
        }


@dataclass
class SpaceQuantity:
    space_id: str
    level_id: str
    floor_area_stored_m2: Optional[float]
    floor_area_recomputed_m2: float
    floor_area_drift_pct: Optional[float]
    ceiling_area_m2: float
    height_m: Optional[float]
    height_source: str
    height_flags: list
    perimeter_m: float
    wall_gross_m2: Optional[float]
    wall_net_m2: Optional[float]
    wall_net_partial: bool
    trim_length_m: float
    volume_stored_m3: Optional[float]
    volume_derived_m3: Optional[float]
    flags: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "space_id": self.space_id,
            "level_id": self.level_id,
            "floor_area_stored_m2": self.floor_area_stored_m2,
            "floor_area_recomputed_m2": self.floor_area_recomputed_m2,
            "floor_area_drift_pct": self.floor_area_drift_pct,
            "ceiling_area_m2": self.ceiling_area_m2,
            "height_m": self.height_m,
            "height_source": self.height_source,
            "height_flags": self.height_flags,
            "perimeter_m": self.perimeter_m,
            "wall_gross_m2": self.wall_gross_m2,
            "wall_net_m2": self.wall_net_m2,
            "wall_net_partial": self.wall_net_partial,
            "trim_length_m": self.trim_length_m,
            "volume_stored_m3": self.volume_stored_m3,
            "volume_derived_m3": self.volume_derived_m3,
            "flags": self.flags,
            "provenance": {"element_ids": [self.space_id], "method": "takeoff_estimate.space"},
        }


@dataclass
class EnvelopeSegmentQuantity:
    segment_id: str
    facade: str
    space_id: str
    construction_id: str
    interval_m: Optional[list]  # cumulative interval along the facade (D3)
    length_m: Optional[float]
    height_m: Optional[float]
    gross_m2: Optional[float]
    deductions: list  # [{opening_id, overlap_m, area_m2}] straddle splits (D3)
    net_m2: Optional[float]
    unquantified_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "facade": self.facade,
            "space_id": self.space_id,
            "construction_id": self.construction_id,
            "interval_m": self.interval_m,
            "length_m": self.length_m,
            "height_m": self.height_m,
            "gross_m2": self.gross_m2,
            "deductions": self.deductions,
            "net_m2": self.net_m2,
            "unquantified_reason": self.unquantified_reason,
            "provenance": {
                "element_ids": [self.segment_id] + [d["opening_id"] for d in self.deductions],
                "method": "takeoff_estimate.envelope",
            },
        }


@dataclass
class PartitionQuantity:
    """One interior partition between two spaces on one level (Phase 0 s.2).
    ``area_m2`` is the assembly area (length x min height); the per-space face
    areas are what the per-space paint basis already covers (D6)."""

    space_a: str
    space_b: str
    level_id: str
    length_m: float
    area_m2: Optional[float]
    height_m: Optional[float]
    pairing: list  # ["exact"] / ["offset"] / both
    face_area_by_space: dict

    def to_dict(self) -> dict:
        return {
            "space_a": self.space_a,
            "space_b": self.space_b,
            "level_id": self.level_id,
            "length_m": self.length_m,
            "area_m2": self.area_m2,
            "height_m": self.height_m,
            "pairing": self.pairing,
            "face_area_by_space": self.face_area_by_space,
            "provenance": {
                "element_ids": [self.space_a, self.space_b],
                "method": "takeoff_estimate.partition_pairing",
            },
        }


@dataclass
class PartitionLevelStats:
    level_id: str
    interior_edge_total_m: float
    unmatched_m: float
    unmatched_pct: float
    exceeds_unmatched_flag: bool

    def to_dict(self) -> dict:
        return {
            "level_id": self.level_id,
            "interior_edge_total_m": self.interior_edge_total_m,
            "unmatched_m": self.unmatched_m,
            "unmatched_pct": self.unmatched_pct,
            "exceeds_unmatched_flag": self.exceeds_unmatched_flag,
        }


@dataclass
class RoofQuantity:
    """D2: both areas are reported; priced lines use the gross."""

    basis: str  # "roof_planes" | "flat_roof"
    area_union_m2: Optional[float]
    area_gross_m2: Optional[float]
    unquantified_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "basis": self.basis,
            "area_union_m2": self.area_union_m2,
            "area_gross_m2": self.area_gross_m2,
            "priced_basis": "area_gross_m2",
            "unquantified_reason": self.unquantified_reason,
            "provenance": {"element_ids": [], "method": "takeoff_estimate.roof"},
        }


@dataclass
class TakeoffQuantities:
    spaces: dict  # space_id -> SpaceQuantity
    envelope_segments: list  # [EnvelopeSegmentQuantity]
    envelope_by_facade: dict  # facade -> {gross_m2, net_m2, unquantified_segment_ids}
    envelope_gross_total_m2: float
    envelope_net_total_m2: float
    envelope_unplaced_openings: list  # [{opening_id, facade, reason}]
    partitions: list  # [PartitionQuantity]
    partition_levels: dict  # level_id -> PartitionLevelStats
    openings: list  # [OpeningQuantity]
    opening_rollup: list  # [{category, tag, count, area_m2}]
    roof: RoofQuantity
    total_floor_area_m2: float
    fixture_counts: dict  # row 11: present counts, or "absent" markers

    def to_dict(self) -> dict:
        return {
            "spaces": {sid: q.to_dict() for sid, q in sorted(self.spaces.items())},
            "envelope_segments": [s.to_dict() for s in self.envelope_segments],
            "envelope_by_facade": self.envelope_by_facade,
            "envelope_gross_total_m2": self.envelope_gross_total_m2,
            "envelope_net_total_m2": self.envelope_net_total_m2,
            "envelope_unplaced_openings": self.envelope_unplaced_openings,
            "partitions": [p.to_dict() for p in self.partitions],
            "partition_levels": {
                lid: s.to_dict() for lid, s in sorted(self.partition_levels.items())
            },
            "openings": [o.to_dict() for o in self.openings],
            "opening_rollup": self.opening_rollup,
            "roof": self.roof.to_dict(),
            "total_floor_area_m2": self.total_floor_area_m2,
            "fixture_counts": self.fixture_counts,
        }


def _compute_partitions(model: BuildingModel, levels: dict) -> tuple[list, dict]:
    """Phase 0 section 2 edge pairing, generalised to arbitrary straight
    segments (D4). Exact (collinear) pairing first-class alongside offset
    pairing for inner-face islands; longest overlap first; each edge portion
    pairs at most once; unmatched remainder reported per level."""
    space_items = sorted(model.spaces.items())
    space_by_id = dict(space_items)

    env_frames = []
    for seg in model.envelope:
        if seg.from_m and seg.to_m:
            fr = _seg_frame(seg.from_m, seg.to_m)
            if fr is not None:
                env_frames.append(fr)

    def is_envelope_adjacent(fr: _SegFrame) -> bool:
        for ef in env_frames:
            if _parallel(fr, ef) and abs(fr.d - ef.d) <= MAX_OFFSET_M + EPS:
                if min(fr.t1, ef.t1) - max(fr.t0, ef.t0) >= MIN_OVERLAP_M:
                    return True
        return False

    edges = []  # {space_id, level_id, frame, free[(t0, t1)]}
    for sid, sp in space_items:
        poly = sp.polygon_m
        for i in range(len(poly)):
            fr = _seg_frame(poly[i], poly[(i + 1) % len(poly)])
            if fr is None or fr.length <= EPS:
                continue
            if is_envelope_adjacent(fr):
                continue
            edges.append(
                {"space_id": sid, "level_id": sp.level_id, "frame": fr, "free": [(fr.t0, fr.t1)]}
            )

    candidates = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            a, b = edges[i], edges[j]
            if a["space_id"] == b["space_id"] or a["level_id"] != b["level_id"]:
                continue
            fa, fb = a["frame"], b["frame"]
            if not _parallel(fa, fb):
                continue
            offset = abs(fa.d - fb.d)
            if offset <= COLLINEAR_TOL_M:
                kind = "exact"
            elif offset <= MAX_OFFSET_M and fa.flipped != fb.flipped:
                kind = "offset"  # facing edges traverse the boundary oppositely
            else:
                continue
            overlap = min(fa.t1, fb.t1) - max(fa.t0, fb.t0)
            if overlap >= MIN_OVERLAP_M:
                key = tuple(sorted((a["space_id"], b["space_id"])))
                candidates.append((-overlap, key, max(fa.t0, fb.t0), i, j, kind))
    candidates.sort()

    pair_len: dict = {}
    for _neg_ov, _key, _t, i, j, kind in candidates:
        a, b = edges[i], edges[j]
        span = (max(a["frame"].t0, b["frame"].t0), min(a["frame"].t1, b["frame"].t1))
        best = None
        for x, y in a["free"]:
            lo, hi = max(x, span[0]), min(y, span[1])
            if hi - lo < MIN_OVERLAP_M:
                continue
            for x2, y2 in b["free"]:
                lo2, hi2 = max(lo, x2), min(hi, y2)
                if hi2 - lo2 >= MIN_OVERLAP_M and (best is None or hi2 - lo2 > best[1] - best[0]):
                    best = (lo2, hi2)
        if best is None:
            continue
        a["free"] = _subtract_intervals(a["free"], best[0], best[1])
        b["free"] = _subtract_intervals(b["free"], best[0], best[1])
        key = tuple(sorted((a["space_id"], b["space_id"]))) + (a["level_id"],)
        rec = pair_len.setdefault(key, [0.0, set()])
        rec[0] += best[1] - best[0]
        rec[1].add(kind)

    partitions: list[PartitionQuantity] = []
    for (sa, sb, lid), (length, kinds) in sorted(pair_len.items()):
        ha, _, _ = _space_height(space_by_id[sa], levels)
        hb, _, _ = _space_height(space_by_id[sb], levels)
        height = min(ha, hb) if ha is not None and hb is not None else None
        face = {}
        for sid, h in ((sa, ha), (sb, hb)):
            face[sid] = _r6(length * h) if h is not None else None
        partitions.append(
            PartitionQuantity(
                space_a=sa,
                space_b=sb,
                level_id=lid,
                length_m=_r6(length),
                area_m2=_r6(length * height) if height is not None else None,
                height_m=height,
                pairing=sorted(kinds),
                face_area_by_space=face,
            )
        )

    per_level: dict[str, PartitionLevelStats] = {}
    raw: dict[str, list] = {}
    # every level with spaces reports stats, even with no interior edges at
    # all (a zeroed entry is a statement; a missing entry would be a gap)
    for _sid, sp in space_items:
        raw.setdefault(sp.level_id, [0.0, 0.0])
    for e in edges:
        st = raw.setdefault(e["level_id"], [0.0, 0.0])
        st[0] += e["frame"].length
        st[1] += sum(y - x for x, y in e["free"])
    for lid, (total, unmatched) in sorted(raw.items()):
        pct = _r6(100.0 * unmatched / total) if total > 0 else 0.0
        per_level[lid] = PartitionLevelStats(
            level_id=lid,
            interior_edge_total_m=_r6(total),
            unmatched_m=_r6(unmatched),
            unmatched_pct=pct,
            exceeds_unmatched_flag=pct > UNMATCHED_FLAG_PCT,
        )
    return partitions, per_level


def _envelope_quantities(
    model: BuildingModel, openings: list
) -> tuple[list, dict, float, float, list]:
    """Envelope gross/net per segment with D3 cumulative interval matching:
    segments on a facade are ordered along the facade axis, an opening
    deducts from the segment(s) its host interval overlaps, split pro-rata
    when it straddles a segment boundary, and the split is recorded."""
    by_facade: dict[str, list] = {}
    for seg in model.envelope:
        by_facade.setdefault(seg.facade, []).append(seg)

    intervals: dict[str, list] = {}
    for facade, segs in by_facade.items():
        best = None
        for s in segs:
            if s.from_m and s.to_m:
                fr = _seg_frame(s.from_m, s.to_m)
                if fr is not None and (best is None or fr.length > best.length):
                    best = fr
        if best is None:
            continue
        ux, uy = best.ux, best.uy
        projections = []
        for s in segs:
            if s.from_m and s.to_m:
                projections += [
                    ux * float(s.from_m[0]) + uy * float(s.from_m[1]),
                    ux * float(s.to_m[0]) + uy * float(s.to_m[1]),
                ]
        origin = min(projections)
        for s in segs:
            if s.from_m and s.to_m:
                a = ux * float(s.from_m[0]) + uy * float(s.from_m[1]) - origin
                b = ux * float(s.to_m[0]) + uy * float(s.to_m[1]) - origin
                intervals[s.id] = [_r6(min(a, b)), _r6(max(a, b))]

    deductions: dict[str, list] = {s.id: [] for s in model.envelope}
    unplaced: list[dict] = []
    for o in openings:
        if not o.host_facade or o.host_facade == "roof" or o.area_m2 is None:
            continue
        segs = by_facade.get(o.host_facade, [])
        if o.host_interval_m is None:
            unplaced.append(
                {"opening_id": o.opening_id, "facade": o.host_facade, "reason": "no_host_interval"}
            )
            continue
        a, b = float(o.host_interval_m[0]), float(o.host_interval_m[1])
        width = b - a
        if width <= EPS:
            unplaced.append(
                {
                    "opening_id": o.opening_id,
                    "facade": o.host_facade,
                    "reason": "zero_width_interval",
                }
            )
            continue
        placed = False
        for s in sorted(segs, key=lambda s: (intervals.get(s.id, [math.inf])[0], s.id)):
            iv = intervals.get(s.id)
            if iv is None:
                continue
            overlap = min(b, iv[1]) - max(a, iv[0])
            if overlap > EPS:
                deductions[s.id].append(
                    {
                        "opening_id": o.opening_id,
                        "overlap_m": _r6(overlap),
                        "area_m2": _r6(o.area_m2 * overlap / width),
                    }
                )
                placed = True
        if not placed:
            unplaced.append(
                {
                    "opening_id": o.opening_id,
                    "facade": o.host_facade,
                    "reason": "interval_outside_segments",
                }
            )

    seg_quantities: list[EnvelopeSegmentQuantity] = []
    by_facade_totals: dict[str, dict] = {}
    gross_total = 0.0
    net_total = 0.0
    for facade in sorted(by_facade):
        segs = sorted(by_facade[facade], key=lambda s: (intervals.get(s.id, [math.inf])[0], s.id))
        totals = {"gross_m2": 0.0, "net_m2": 0.0, "unquantified_segment_ids": []}
        for s in segs:
            gross = None
            reason = None
            seg_len = s.length_m
            if seg_len is None and s.from_m and s.to_m:
                fr = _seg_frame(s.from_m, s.to_m)
                seg_len = fr.length if fr is not None else None
            if s.area_m2 is not None:
                gross = float(s.area_m2)
            elif seg_len is not None and s.height_m is not None:
                gross = float(seg_len) * float(s.height_m)
            else:
                reason = "no_area_or_dimensions"
            ded = deductions[s.id]
            net = gross - sum(d["area_m2"] for d in ded) if gross is not None else None
            seg_quantities.append(
                EnvelopeSegmentQuantity(
                    segment_id=s.id,
                    facade=s.facade,
                    space_id=s.space_id,
                    construction_id=s.construction_id,
                    interval_m=intervals.get(s.id),
                    length_m=_r6(seg_len) if seg_len is not None else None,
                    height_m=s.height_m,
                    gross_m2=_r6(gross) if gross is not None else None,
                    deductions=ded,
                    net_m2=_r6(net) if net is not None else None,
                    unquantified_reason=reason,
                )
            )
            if gross is None:
                totals["unquantified_segment_ids"].append(s.id)
            else:
                totals["gross_m2"] += gross
                totals["net_m2"] += net
                gross_total += gross
                net_total += net
        totals["gross_m2"] = _r6(totals["gross_m2"])
        totals["net_m2"] = _r6(totals["net_m2"])
        by_facade_totals[facade] = totals
    return seg_quantities, by_facade_totals, _r6(gross_total), _r6(net_total), unplaced


def _gross_footprint_m2(model: BuildingModel) -> tuple[Optional[float], Optional[str]]:
    """Gross footprint (wall band included, D2): the area enclosed by the
    envelope segments' own ring, chained endpoint to endpoint per level.
    Returns (None, reason) when the ring cannot be closed; never falls back
    to the union silently."""
    if not model.envelope:
        return None, "no_envelope_segments"
    level_of_space = {sid: sp.level_id for sid, sp in model.spaces.items()}
    by_level: dict[str, list] = {}
    for seg in model.envelope:
        lid = level_of_space.get(seg.space_id)
        if lid is None:
            if len(model.levels) == 1:
                lid = model.levels[0].id
            else:
                return None, "envelope_segment_without_level"
        by_level.setdefault(lid, []).append(seg)
    total = 0.0
    for lid, segs in sorted(by_level.items()):
        adjacency: dict[tuple, list] = {}
        for seg in segs:
            if not seg.from_m or not seg.to_m:
                return None, "envelope_segment_without_endpoints"
            a = (round(float(seg.from_m[0]), 6), round(float(seg.from_m[1]), 6))
            b = (round(float(seg.to_m[0]), 6), round(float(seg.to_m[1]), 6))
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)
        unused = {(a, b) for a, bs in adjacency.items() for b in bs}
        while unused:
            start = next(iter(unused))[0]
            loop = [start]
            node = start
            while True:
                nxt = None
                for cand in adjacency.get(node, []):
                    if (node, cand) in unused:
                        nxt = cand
                        break
                if nxt is None:
                    return None, "footprint_ring_not_closed"
                unused.discard((node, nxt))
                unused.discard((nxt, node))
                node = nxt
                if node == start:
                    break
                loop.append(node)
            if len(loop) >= 3:
                total += abs(_shoelace(loop))
    return _r6(total), None


def _roof_quantity(model: BuildingModel, space_items: list) -> RoofQuantity:
    if model.roof_planes:
        gross = 0.0
        reason = None
        plan_polys = []
        for plane in model.roof_planes:
            if plane.area_m2 is not None:
                gross += float(plane.area_m2)
            elif len(plane.vertices_m) >= 3:
                # true sloped area via the Newell formula on 3D vertices
                nx = ny = nz = 0.0
                verts = plane.vertices_m
                for i in range(len(verts)):
                    x1, y1, z1 = (float(v) for v in verts[i])
                    x2, y2, z2 = (float(v) for v in verts[(i + 1) % len(verts)])
                    nx += (y1 - y2) * (z1 + z2)
                    ny += (z1 - z2) * (x1 + x2)
                    nz += (x1 - x2) * (y1 + y2)
                gross += 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
            else:
                reason = "roof_plane_without_area_or_vertices"
            if len(plane.vertices_m) >= 3:
                plan_polys.append([[float(v[0]), float(v[1])] for v in plane.vertices_m])
        parts = footprint_multipart_from_regions(plan_polys) if plan_polys else []
        union = _r6(sum(p.footprint_area for p in parts)) if parts else None
        if reason is not None:
            return RoofQuantity("roof_planes", union, None, reason)
        return RoofQuantity("roof_planes", union, _r6(gross), None)
    polys = [sp.polygon_m for _, sp in space_items if len(sp.polygon_m) >= 3]
    parts = footprint_multipart_from_regions(polys) if polys else []
    union = _r6(sum(p.footprint_area for p in parts)) if parts else None
    gross, reason = _gross_footprint_m2(model)
    return RoofQuantity("flat_roof", union, gross, reason)


def compute_quantities(model: BuildingModel) -> TakeoffQuantities:
    """Quantity rollup over the canonical model (Phase 0 section 1, rows
    1-11). Pure: the model is read, never mutated."""
    levels = {lvl.id: lvl for lvl in model.levels}
    space_items = sorted(model.spaces.items())

    openings: list[OpeningQuantity] = []
    q_spaces: dict[str, SpaceQuantity] = {}
    for sid, sp in space_items:
        poly = [[float(x), float(y)] for x, y in sp.polygon_m]
        recomputed = abs(_shoelace(poly)) if len(poly) >= 3 else 0.0
        stored = float(sp.area_m2) if sp.area_m2 is not None else None
        drift = None
        flags: list[str] = []
        if stored:
            drift = _r6(100.0 * abs(stored - recomputed) / stored)
            if drift > DRIFT_FLAG_PCT:
                flags.append("floor_area_drift_over_1pct")
        height, h_source, h_flags = _space_height(sp, levels)
        perim = ring_perimeter(poly) if len(poly) >= 3 else 0.0
        wall_gross = perim * height if height is not None else None

        wall_deduction = 0.0
        trim_deduction = 0.0
        partial = False
        for o in sp.openings:
            area = _opening_area(o)
            rec = OpeningQuantity(
                opening_id=o.id,
                space_id=sid,
                adjacent_space_id=o.adjacent_space_id,
                category=o.category,
                tag=o.tag,
                width_m=o.width_m,
                height_m=o.height_m,
                sill_m=o.sill_m,
                host_facade=o.host_facade,
                host_interval_m=list(o.host_interval_m) if o.host_interval_m else None,
                area_m2=_r6(area) if area is not None else None,
                needs_review=bool(o.needs_review),
                unquantified_reason="no_area_or_dimensions" if area is None else None,
            )
            openings.append(rec)
            if area is None:
                partial = True
            elif o.category != "skylight":
                wall_deduction += area
            if o.sill_m == 0 and o.width_m is not None:
                trim_deduction += float(o.width_m)
        far_deduction = 0.0
        far_trim = 0.0
        for other_sid, other in space_items:
            if other_sid == sid:
                continue
            for o in other.openings:
                if o.adjacent_space_id == sid:
                    area = _opening_area(o)
                    if area is None:
                        partial = True
                    elif o.category != "skylight":
                        far_deduction += area
                    if o.sill_m == 0 and o.width_m is not None:
                        far_trim += float(o.width_m)
        wall_net = None
        if wall_gross is not None:
            wall_net = wall_gross - wall_deduction - far_deduction
        q_spaces[sid] = SpaceQuantity(
            space_id=sid,
            level_id=sp.level_id,
            floor_area_stored_m2=_r6(stored) if stored is not None else None,
            floor_area_recomputed_m2=_r6(recomputed),
            floor_area_drift_pct=drift,
            ceiling_area_m2=_r6(recomputed),
            height_m=height,
            height_source=h_source,
            height_flags=h_flags,
            perimeter_m=_r6(perim),
            wall_gross_m2=_r6(wall_gross) if wall_gross is not None else None,
            wall_net_m2=_r6(wall_net) if wall_net is not None else None,
            wall_net_partial=partial,
            trim_length_m=_r6(perim - trim_deduction - far_trim),
            volume_stored_m3=_r6(sp.volume_m3) if sp.volume_m3 is not None else None,
            volume_derived_m3=_r6(recomputed * height) if height is not None else None,
            flags=flags,
        )

    seg_q, by_facade, gross_total, net_total, unplaced = _envelope_quantities(model, openings)
    partitions, partition_levels = _compute_partitions(model, levels)
    roof = _roof_quantity(model, space_items)

    rollup: dict[str, dict] = {}
    for rec in openings:
        key = f"{rec.category}|{rec.tag}"
        r = rollup.setdefault(
            key, {"category": rec.category, "tag": rec.tag, "count": 0, "area_m2": 0.0}
        )
        r["count"] += 1
        if rec.area_m2 is not None:
            r["area_m2"] += rec.area_m2
    opening_rollup = []
    for key in sorted(rollup):
        r = rollup[key]
        r["area_m2"] = _r6(r["area_m2"])
        opening_rollup.append(r)

    fixtures_by_space = {
        sid: len(sp.lighting.fixtures) for sid, sp in space_items if sp.lighting.fixtures
    }
    fixture_counts = {
        "lighting_fixtures_total": sum(fixtures_by_space.values()),
        "lighting_fixtures_by_space": fixtures_by_space,
        "equipment_total": len(model.equipment),
    }

    return TakeoffQuantities(
        spaces=q_spaces,
        envelope_segments=seg_q,
        envelope_by_facade=by_facade,
        envelope_gross_total_m2=gross_total,
        envelope_net_total_m2=net_total,
        envelope_unplaced_openings=unplaced,
        partitions=partitions,
        partition_levels=partition_levels,
        openings=openings,
        opening_rollup=opening_rollup,
        roof=roof,
        total_floor_area_m2=_r6(sum(q.floor_area_recomputed_m2 for q in q_spaces.values())),
        fixture_counts=fixture_counts,
    )


# ---------------------------------------------------------------------------
# Estimate (Phase 0 section 3 rules; D5 labour basis; D6 partition basis)
# ---------------------------------------------------------------------------

PARTITION_BASES = frozenset({"faces", "assembly"})


@dataclass
class LineProvenance:
    element_ids: list
    rule: str
    price_item_id: Optional[str]
    price_book_version: Optional[str]

    def to_dict(self) -> dict:
        return {
            "element_ids": self.element_ids,
            "rule": self.rule,
            "price_item_id": self.price_item_id,
            "price_book_version": self.price_book_version,
        }


@dataclass
class EstimateLine:
    line_id: str
    kind: str
    category: str
    status: str  # "priced" | "unpriced" | "unquantified"
    quantity: Optional[float]
    unit: str
    rule: str
    provenance: LineProvenance
    partition_basis: str
    reason: Optional[str] = None
    material_cost: Optional[float] = None
    labour_hours: Optional[float] = None
    labour_cost: Optional[float] = None
    labour_basis: Optional[str] = None
    labour_quantity: Optional[float] = None
    labour_flag: Optional[str] = None
    line_total: Optional[float] = None
    basis: Optional[str] = None
    quantity_detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "line_id": self.line_id,
            "kind": self.kind,
            "category": self.category,
            "status": self.status,
            "quantity": self.quantity,
            "unit": self.unit,
            "rule": self.rule,
            "reason": self.reason,
            "material_cost": self.material_cost,
            "labour_hours": self.labour_hours,
            "labour_cost": self.labour_cost,
            "labour_basis": self.labour_basis,
            "labour_quantity": self.labour_quantity,
            "labour_flag": self.labour_flag,
            "line_total": self.line_total,
            "basis": self.basis,
            "partition_basis": self.partition_basis,
            "quantity_detail": self.quantity_detail,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class Estimate:
    model_name: str
    price_book_version: str
    finishes_version: str
    currency: str
    partition_basis: str
    quantities: TakeoffQuantities
    lines: list  # [EstimateLine]
    subtotals_by_category: dict
    total: float
    total_label: str  # "all_lines_priced" | "priced_lines_only"
    unpriced_line_ids: list

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "price_book_version": self.price_book_version,
            "finishes_version": self.finishes_version,
            "currency": self.currency,
            "partition_basis": self.partition_basis,
            "quantities": self.quantities.to_dict(),
            "lines": [ln.to_dict() for ln in self.lines],
            "subtotals_by_category": self.subtotals_by_category,
            "total": self.total,
            "total_label": self.total_label,
            "unpriced_line_ids": self.unpriced_line_ids,
        }

    def to_json(self) -> str:
        """Deterministic serialization: sorted keys, byte-identical re-runs."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"


def validate_estimate_dict(d: dict) -> None:
    """Validate a serialized estimate, in particular the D6 partition-basis
    discipline: the basis must be stated, every line must carry the same
    basis, and partition assembly lines may exist only under the assembly
    basis. Raises ``EstimateValidationError``."""
    basis = d.get("partition_basis")
    if basis not in PARTITION_BASES:
        raise EstimateValidationError(
            f"estimate partition_basis must be one of {sorted(PARTITION_BASES)}, got {basis!r}"
        )
    for ln in d.get("lines", []):
        if ln.get("partition_basis") != basis:
            raise EstimateValidationError(
                f"line {ln.get('line_id')}: partition_basis {ln.get('partition_basis')!r} "
                f"!= estimate basis {basis!r} (mixed or unstated basis)"
            )
        if basis == "faces" and ln.get("kind") == "partition_assembly":
            raise EstimateValidationError(
                f"line {ln.get('line_id')}: partition assembly line under the faces basis "
                "(both bases in one estimate)"
            )


def _labour(item: PriceBookItem, priced_qty: float, raw_qty: float) -> tuple:
    """Phase 0 labour rule with the D5 basis. Returns
    (hours, cost, basis, labour_quantity, flag). Labour is never folded in at
    an assumed rate: missing hours or rate leaves the line material-only with
    the ``no_labour_rate`` flag."""
    if item.labour_hours_per_unit is None or item.labour_rate_per_hour is None:
        return None, None, item.labour_basis, None, "no_labour_rate"
    basis = item.labour_basis  # loader guarantees a basis when hours exist
    qty = priced_qty if basis == "priced_unit" else raw_qty
    hours = qty * float(item.labour_hours_per_unit)
    return _r6(hours), _r2(hours * float(item.labour_rate_per_hour)), basis, _r6(qty), None


def _priced_line(
    line_id,
    kind,
    category,
    element_ids,
    quantity,
    raw_quantity,
    unit,
    rule,
    item: PriceBookItem,
    book_version: str,
    partition_basis: str,
    basis=None,
) -> EstimateLine:
    mat = _r2(quantity * float(item.material_unit_price))
    hours, lab_cost, lab_basis, lab_qty, flag = _labour(item, quantity, raw_quantity)
    return EstimateLine(
        line_id=line_id,
        kind=kind,
        category=category,
        status="priced",
        quantity=_r6(quantity),
        unit=unit,
        rule=rule,
        provenance=LineProvenance(list(element_ids), rule, item.item_id, book_version),
        partition_basis=partition_basis,
        material_cost=mat,
        labour_hours=hours,
        labour_cost=lab_cost,
        labour_basis=lab_basis,
        labour_quantity=lab_qty,
        labour_flag=flag,
        line_total=_r2(mat + (lab_cost or 0.0)),
        basis=basis,
    )


def _unpriced_line(
    line_id,
    kind,
    category,
    element_ids,
    quantity,
    unit,
    rule,
    reason,
    partition_basis: str,
    basis=None,
    status="unpriced",
) -> EstimateLine:
    return EstimateLine(
        line_id=line_id,
        kind=kind,
        category=category,
        status=status,
        quantity=_r6(quantity) if quantity is not None else None,
        unit=unit,
        rule=rule,
        provenance=LineProvenance(list(element_ids), rule, None, None),
        partition_basis=partition_basis,
        reason=reason,
        basis=basis,
    )


def _partition_face_exclusions(quantities: TakeoffQuantities) -> dict:
    """Per-space partition face area to exclude from paint under the D6
    assembly basis: each partition's face area for the space, less the areas
    of the openings hosted on that partition (the hole is in both faces)."""
    openings_by_pair: dict[frozenset, float] = {}
    for o in quantities.openings:
        if o.adjacent_space_id:
            key = frozenset((o.space_id, o.adjacent_space_id))
            if o.area_m2 is not None:
                openings_by_pair[key] = openings_by_pair.get(key, 0.0) + o.area_m2
    exclusions: dict[str, float] = {}
    for p in quantities.partitions:
        hole = openings_by_pair.get(frozenset((p.space_a, p.space_b)), 0.0)
        for sid in (p.space_a, p.space_b):
            face = p.face_area_by_space.get(sid)
            if face is not None:
                exclusions[sid] = exclusions.get(sid, 0.0) + max(0.0, face - hole)
    return {sid: _r6(v) for sid, v in exclusions.items()}


def build_estimate(
    model: BuildingModel,
    book: PriceBook,
    finishes: FinishesSchedule,
    *,
    partition_basis: str = "faces",
) -> Estimate:
    """Build the priced estimate. ``partition_basis`` is the D6 option:
    ``"faces"`` (default) prices partition faces inside the per-space paint
    lines and reports partitions as reconciliation quantities only;
    ``"assembly"`` prices partition assemblies as lines and excludes the
    corresponding faces from the per-space paint quantities, recording the
    exclusion on each affected line. Any other value (including an unstated
    basis) is a validation error."""
    if partition_basis not in PARTITION_BASES:
        raise EstimateValidationError(
            f"partition_basis must be one of {sorted(PARTITION_BASES)}, got {partition_basis!r}"
        )
    quantities = compute_quantities(model)
    version = book.price_book_version
    lines: list[EstimateLine] = []
    exclusions = _partition_face_exclusions(quantities) if partition_basis == "assembly" else {}

    # --- finish-driven lines: paint (wall/ceiling), flooring/tile (floor)
    for sid in sorted(quantities.spaces):
        q = quantities.spaces[sid]
        for surface in ("wall", "ceiling", "floor"):
            finish_id = finishes.finish_for(sid, surface)
            if surface in ("wall", "ceiling"):
                area = q.wall_net_m2 if surface == "wall" else q.ceiling_area_m2
                excluded = exclusions.get(sid, 0.0) if surface == "wall" else 0.0
                if finish_id is None:
                    lines.append(
                        _unpriced_line(
                            f"paint:{sid}:{surface}",
                            "paint",
                            "paint",
                            [sid],
                            area,
                            "m2",
                            "paint_volume",
                            "no_finish_assigned",
                            partition_basis,
                        )
                    )
                    continue
                item = book.find_by_applies_to("finish_id", finish_id)
                if item is None:
                    lines.append(
                        _unpriced_line(
                            f"paint:{sid}:{surface}",
                            "paint",
                            "paint",
                            [sid],
                            area,
                            "m2",
                            "paint_volume",
                            "no_price_item",
                            partition_basis,
                        )
                    )
                    continue
                if area is None:
                    lines.append(
                        _unpriced_line(
                            f"paint:{sid}:{surface}",
                            "paint",
                            "paint",
                            [sid],
                            None,
                            "m2",
                            "paint_volume",
                            "no_height_source",
                            partition_basis,
                            status="unquantified",
                        )
                    )
                    continue
                paint_area = area - excluded
                if item.coverage_m2_per_l is None:
                    ln = _unpriced_line(
                        f"paint:{sid}:{surface}",
                        "paint",
                        "paint",
                        [sid],
                        paint_area,
                        "m2",
                        "paint_volume",
                        "no_coverage",
                        partition_basis,
                    )
                    lines.append(ln)
                    continue
                if item.coats is None:
                    lines.append(
                        _unpriced_line(
                            f"paint:{sid}:{surface}",
                            "paint",
                            "paint",
                            [sid],
                            paint_area,
                            "m2",
                            "paint_volume",
                            "no_coats",
                            partition_basis,
                        )
                    )
                    continue
                litres = paint_area * float(item.coats) / float(item.coverage_m2_per_l)
                if item.container_l is None:
                    ln = _unpriced_line(
                        f"paint:{sid}:{surface}",
                        "paint",
                        "paint",
                        [sid],
                        litres,
                        "l",
                        "paint_purchase",
                        "no_container_size",
                        partition_basis,
                    )
                    ln.quantity_detail = {"net_area_m2": _r6(paint_area)}
                    lines.append(ln)
                    continue
                buy = math.ceil(litres / float(item.container_l) - EPS) * float(item.container_l)
                ln = _priced_line(
                    f"paint:{sid}:{surface}",
                    "paint",
                    "paint",
                    [sid],
                    buy,
                    paint_area,
                    "l",
                    "paint_purchase",
                    item,
                    version,
                    partition_basis,
                    basis="per_space_face",
                )
                ln.quantity_detail = {
                    "net_area_m2": _r6(paint_area),
                    "litres_exact": _r6(litres),
                    "container_l": item.container_l,
                }
                if excluded:
                    ln.quantity_detail["partition_face_excluded_m2"] = _r6(excluded)
                lines.append(ln)
            else:  # floor
                area = q.floor_area_recomputed_m2
                if finish_id is None:
                    lines.append(
                        _unpriced_line(
                            f"flooring:{sid}:floor",
                            "flooring",
                            "flooring",
                            [sid],
                            area,
                            "m2",
                            "flooring_purchase",
                            "no_finish_assigned",
                            partition_basis,
                        )
                    )
                    continue
                item = book.find_by_applies_to("finish_id", finish_id)
                rule = (
                    "tile_purchase"
                    if (item is not None and item.category == "tile")
                    else "flooring_purchase"
                )
                kind = "tile" if rule == "tile_purchase" else "flooring"
                if item is None:
                    lines.append(
                        _unpriced_line(
                            f"flooring:{sid}:floor",
                            kind,
                            kind,
                            [sid],
                            area,
                            "m2",
                            rule,
                            "no_price_item",
                            partition_basis,
                        )
                    )
                    continue
                if item.waste_pct is None:
                    lines.append(
                        _unpriced_line(
                            f"{kind}:{sid}:floor",
                            kind,
                            kind,
                            [sid],
                            area,
                            "m2",
                            rule,
                            "no_waste_pct",
                            partition_basis,
                        )
                    )
                    continue
                buy = area * (1.0 + float(item.waste_pct) / 100.0)
                ln = _priced_line(
                    f"{kind}:{sid}:floor",
                    kind,
                    kind,
                    [sid],
                    buy,
                    area,
                    "m2",
                    rule,
                    item,
                    version,
                    partition_basis,
                    basis="finished_floor_area_plus_waste",
                )
                ln.quantity_detail = {"finished_area_m2": _r6(area), "waste_pct": item.waste_pct}
                lines.append(ln)
                if item.spare_pct is not None:
                    spare_qty = area * float(item.spare_pct) / 100.0
                    sl = _priced_line(
                        f"spare:{sid}:floor",
                        "spare_stock",
                        "spare",
                        [sid],
                        spare_qty,
                        area,
                        "m2",
                        "spare_stock",
                        item,
                        version,
                        partition_basis,
                        basis="finished_area_x_spare_pct_material_only",
                    )
                    # spare stock is a material line by rule (Phase 0 s.3)
                    sl.labour_hours, sl.labour_cost = 0.0, 0.0
                    sl.labour_quantity = 0.0
                    sl.line_total = sl.material_cost
                    lines.append(sl)

    # --- opening unit lines (one per opening element)
    opening_items = [it for it in book.items if it.category == "opening"]
    for rec in quantities.openings:
        match = None
        for it in opening_items:
            at = it.applies_to
            if at.get("category") != rec.category:
                continue
            if "tag" in at and at["tag"] != rec.tag:
                continue
            match = it
            break
        if match is None:
            lines.append(
                _unpriced_line(
                    f"opening:{rec.opening_id}",
                    "opening_unit",
                    "opening",
                    [rec.opening_id],
                    1,
                    "count",
                    "opening_unit",
                    "no_price_item",
                    partition_basis,
                )
            )
        else:
            ln = _priced_line(
                f"opening:{rec.opening_id}",
                "opening_unit",
                "opening",
                [rec.opening_id],
                1,
                1,
                "count",
                "opening_unit",
                match,
                version,
                partition_basis,
            )
            ln.quantity_detail = {
                "space_id": rec.space_id,
                "category": rec.category,
                "tag": rec.tag,
                "host_facade": rec.host_facade,
                "adjacent_space_id": rec.adjacent_space_id,
            }
            lines.append(ln)

    # --- trim lines per space
    trim_item = next((it for it in book.items if it.category == "trim"), None)
    for sid in sorted(quantities.spaces):
        q = quantities.spaces[sid]
        if trim_item is None:
            lines.append(
                _unpriced_line(
                    f"trim:{sid}",
                    "trim_length",
                    "trim",
                    [sid],
                    q.trim_length_m,
                    "m",
                    "trim_length",
                    "no_price_item",
                    partition_basis,
                )
            )
        else:
            lines.append(
                _priced_line(
                    f"trim:{sid}",
                    "trim_length",
                    "trim",
                    [sid],
                    q.trim_length_m,
                    q.trim_length_m,
                    "m",
                    "trim_length",
                    trim_item,
                    version,
                    partition_basis,
                )
            )

    # --- envelope wall assembly, grouped by construction_id (envelope basis,
    #     never the per-space face basis used for paint)
    by_constr: dict[str, dict] = {}
    for seg in quantities.envelope_segments:
        g = by_constr.setdefault(seg.construction_id, {"net": 0.0, "ids": [], "unquantified": []})
        if seg.net_m2 is not None:
            g["net"] += seg.net_m2
        else:
            g["unquantified"].append(seg.segment_id)
        g["ids"].append(seg.segment_id)
    asm_items = [it for it in book.items if it.category == "wall_assembly"]
    for cid in sorted(by_constr):
        g = by_constr[cid]
        key = cid if cid else "none"
        # net is the sum over quantified segments; None only when every
        # segment in the group is unquantified. A partial sum is used but the
        # unquantified segments are named on the line, never dropped silently.
        net = None if len(g["unquantified"]) == len(g["ids"]) else _r6(g["net"])
        if not cid:
            ln = _unpriced_line(
                f"wall_assembly:{key}",
                "wall_assembly",
                "wall_assembly",
                g["ids"],
                net,
                "m2",
                "wall_assembly",
                "no_construction",
                partition_basis,
                basis="envelope_net",
            )
            if g["unquantified"]:
                ln.quantity_detail = {"unquantified_segment_ids": g["unquantified"]}
            lines.append(ln)
            continue
        match = next((it for it in asm_items if it.applies_to.get("construction_id") == cid), None)
        if match is None:
            lines.append(
                _unpriced_line(
                    f"wall_assembly:{key}",
                    "wall_assembly",
                    "wall_assembly",
                    g["ids"],
                    net,
                    "m2",
                    "wall_assembly",
                    "no_price_item",
                    partition_basis,
                    basis="envelope_net",
                )
            )
        elif net is None:
            lines.append(
                _unpriced_line(
                    f"wall_assembly:{key}",
                    "wall_assembly",
                    "wall_assembly",
                    g["ids"],
                    None,
                    "m2",
                    "wall_assembly",
                    "segment_unquantified",
                    partition_basis,
                    basis="envelope_net",
                    status="unquantified",
                )
            )
        else:
            ln = _priced_line(
                f"wall_assembly:{key}",
                "wall_assembly",
                "wall_assembly",
                g["ids"],
                net,
                net,
                "m2",
                "wall_assembly",
                match,
                version,
                partition_basis,
                basis="envelope_net",
            )
            if g["unquantified"]:
                ln.quantity_detail = {"unquantified_segment_ids": g["unquantified"]}
            lines.append(ln)

    # --- partition assembly lines (D6 assembly basis only)
    if partition_basis == "assembly":
        part_item = next(
            (
                it
                for it in book.items
                if it.applies_to.get("kind") == "partition" or it.category == "partition_assembly"
            ),
            None,
        )
        for p in quantities.partitions:
            el = [p.space_a, p.space_b]
            lid = f"partition:{p.space_a}:{p.space_b}"
            if p.area_m2 is None:
                lines.append(
                    _unpriced_line(
                        lid,
                        "partition_assembly",
                        "partition",
                        el,
                        None,
                        "m2",
                        "partition_assembly",
                        "no_height_source",
                        partition_basis,
                        basis="partition_assembly",
                        status="unquantified",
                    )
                )
            elif part_item is None:
                lines.append(
                    _unpriced_line(
                        lid,
                        "partition_assembly",
                        "partition",
                        el,
                        p.area_m2,
                        "m2",
                        "partition_assembly",
                        "no_price_item",
                        partition_basis,
                        basis="partition_assembly",
                    )
                )
            else:
                ln = _priced_line(
                    lid,
                    "partition_assembly",
                    "partition",
                    el,
                    p.area_m2,
                    p.area_m2,
                    "m2",
                    "partition_assembly",
                    part_item,
                    version,
                    partition_basis,
                    basis="partition_assembly",
                )
                ln.quantity_detail = {"length_m": p.length_m, "height_m": p.height_m}
                lines.append(ln)

    # --- roof line (D2: priced on the gross area)
    roof = quantities.roof
    roof_item = next(
        (it for it in book.items if it.applies_to.get("kind") == "roof" or it.category == "roof"),
        None,
    )
    roof_elements = (
        [p.id for p in model.roof_planes]
        if model.roof_planes
        else [s.segment_id for s in quantities.envelope_segments]
    )
    if roof.area_gross_m2 is None:
        ln = _unpriced_line(
            "roof",
            "roof_area",
            "roof",
            roof_elements,
            None,
            "m2",
            "roof_area",
            roof.unquantified_reason or "roof_unquantified",
            partition_basis,
            basis="area_gross_m2",
            status="unquantified",
        )
        ln.quantity_detail = {"area_union_m2": roof.area_union_m2}
        lines.append(ln)
    elif roof_item is None:
        ln = _unpriced_line(
            "roof",
            "roof_area",
            "roof",
            roof_elements,
            roof.area_gross_m2,
            "m2",
            "roof_area",
            "no_price_item",
            partition_basis,
            basis="area_gross_m2",
        )
        ln.quantity_detail = {
            "area_union_m2": roof.area_union_m2,
            "area_gross_m2": roof.area_gross_m2,
        }
        lines.append(ln)
    else:
        ln = _priced_line(
            "roof",
            "roof_area",
            "roof",
            roof_elements,
            roof.area_gross_m2,
            roof.area_gross_m2,
            "m2",
            "roof_area",
            roof_item,
            version,
            partition_basis,
            basis="area_gross_m2",
        )
        ln.quantity_detail = {
            "area_union_m2": roof.area_union_m2,
            "area_gross_m2": roof.area_gross_m2,
        }
        lines.append(ln)

    lines.sort(key=lambda ln: ln.line_id)
    subtotals: dict[str, float] = {}
    total = 0.0
    for ln in lines:
        if ln.status == "priced":
            subtotals[ln.category] = _r2(subtotals.get(ln.category, 0.0) + (ln.line_total or 0.0))
            total += ln.line_total or 0.0
    unpriced = [ln.line_id for ln in lines if ln.status != "priced"]
    estimate = Estimate(
        model_name=model.name,
        price_book_version=version,
        finishes_version=finishes.finishes_version,
        currency=book.currency,
        partition_basis=partition_basis,
        quantities=quantities,
        lines=lines,
        subtotals_by_category={k: subtotals[k] for k in sorted(subtotals)},
        total=_r2(total),
        total_label="priced_lines_only" if unpriced else "all_lines_priced",
        unpriced_line_ids=unpriced,
    )
    validate_estimate_dict(estimate.to_dict())
    return estimate
