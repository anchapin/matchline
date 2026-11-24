"""Deterministic quantity takeoff -> priced estimate over the canonical model.

Matchline #1419. This module is the package promotion of the Phase 1 scratch
implementation (report #1418: ``_reference_scratch/takeoff_estimate.py``),
hardened per the issue's design decisions D1-D6.

What it does
------------
1. **Quantity takeoff** over the canonical ``BuildingModel`` (spaces, envelope
   segments, openings, levels, roof planes): floor areas (stored *and*
   recomputed from the polygon, with the drift reported), ceiling areas,
   gross/net wall areas, trim lengths, interior partition lengths/areas, and
   the opening roll-up.
2. **Pricing** of those quantities against a *price book* plus a *finishes
   schedule*, one estimate line per priced element, each line carrying its
   provenance (element ids, pricing rule, price item, price book version).

Hard rules (merge-blocking invariants)
--------------------------------------
* **No silent defaults.** Nothing is ever guessed. A missing input produces an
  ``unpriced`` line (priced quantity, no money) or an ``unquantified``
  quantity (``None``) with a machine-readable reason, e.g. ``no_price_item``,
  ``no_coverage``, ``no_height_source``, ``no_construction``.
* **Deterministic.** The same inputs always produce byte-identical output:
  every float is rounded at a fixed precision, every collection is sorted
  before it is emitted, and no wall-clock, no RNG, no network.
* **Fixture prices only.** The price books shipped with this repo are
  labelled ``FIXTURE`` in their headers and carry invented numbers. They are
  test data, **not** market data; no real-world cost accuracy is claimed.
* **No material layers.** Wall build-ups, finishes build-ups and other
  material-layer quantities are out of scope.

Design decisions D1-D6 (settled for the package; see
``docs/takeoff-estimate-overview.md``)
----------------------------------------
* **D1 - the Phase 1 fixtures are vendored in-repo** under
  ``tests/fixtures/takeoff_estimate/`` (models, price books, finishes
  schedules and the expected estimates), so the gates run against checked-in
  data instead of a scratch directory outside the repo.
* **D2 - the roof basis is explicit, never silent.** The roof quantity is
  always reported with the basis that produced it
  (``roof_planes_stored_area`` or ``flat_roof_union_of_space_polygons``),
  the caller may pin the basis explicitly, and a basis that cannot be
  quantified yields ``area_m2: None`` with a reason instead of a guess. The
  inner-face union excludes the wall-thickness band; that limitation is
  stated in the output, not hidden.
* **D3 - envelope opening deductions use cumulative interval matching.**
  Openings carry ``host_interval_m`` along their facade; the intervals are
  projected onto the facade axis (origin and direction are derived from the
  facade's reference segment and recorded), so an opening that straddles two
  facade segments deducts its area from both, proportionally. The legacy
  "same facade + same space" fallback is used only when the interval is
  missing, and it is recorded as such.
* **D4 - diagonal edges are reported, not dropped.** An interior edge that
  the pairing rule cannot handle (diagonal) is listed with its length and a
  reason and counts towards the level's unmatched-edge budget, so it can
  never disappear silently.
* **D5 - the labour basis is part of the price-book schema.** A price item
  may declare ``labour_basis`` (``unit`` or ``m2``); when it is absent the
  legacy per-unit fields are used and the line records that the basis was
  inferred. Labour is never applied to a quantity it does not belong to.
* **D6 - partitions are reported, and never priced, by this release.** The
  per-space face lines already cover the partition faces; pricing both
  would double-count. The estimate states the partition pricing basis
  explicitly and lists any partition price item in the book as not priced
  (with the reason) rather than quietly ignoring it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from building_model import BuildingModel

EPS = 1e-9
COLLINEAR_TOL_M = 0.001  # same supporting line within 1 mm counts as collinear
MIN_OVERLAP_M = 0.05  # minimum pairing overlap
MAX_OFFSET_M = 0.30  # inner-face island offset ceiling

#: Roof quantity bases the caller may pin (D2).
ROOF_BASES = ("auto", "roof_planes", "space_union")
#: Labour bases a price item may declare (D5).
LABOUR_BASES = ("unit", "m2")
#: Price-item categories this module knows how to price.
ITEM_CATEGORIES = (
    "paint",
    "flooring",
    "tile",
    "opening",
    "trim",
    "wall_assembly",
    "partition",
)
#: Units a price item may be sold in.
ITEM_UNITS = ("m2", "m", "l", "count")
#: Surfaces a finishes row may assign a finish to.
FINISH_SURFACES = ("wall", "ceiling", "floor")

DESIGN_DECISIONS = {
    "D1": "Phase 1 fixtures vendored under tests/fixtures/takeoff_estimate/.",
    "D2": "Roof basis explicit: roof_planes_stored_area or flat_roof_union_of_space_polygons.",
    "D3": "Envelope deductions use cumulative interval matching along the facade axis.",
    "D4": "Unsupported (diagonal) interior edges are reported with a reason, never dropped.",
    "D5": "Price items declare a labour basis (unit or m2); the basis is recorded on every line.",
    "D6": "Partitions are reported as quantities and are not priced (per-space faces cover them).",
}


# --------------------------------------------------------------------- errors
class TakeoffError(ValueError):
    """Base error for this module.

    Every error carries a machine-readable ``code`` so callers (and tests)
    can branch on the reason instead of parsing prose.
    """

    code = "takeoff_error"

    def __init__(self, message: str, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.code = code or type(self).code
        self.details = details or {}


class ModelError(TakeoffError):
    """The canonical model could not be read or is not a BuildingModel."""

    code = "model_invalid"


class PriceBookError(TakeoffError):
    """The price book is missing something it must state."""

    code = "price_book_invalid"


class FinishesError(TakeoffError):
    """The finishes schedule is missing something it must state."""

    code = "finishes_invalid"


# ------------------------------------------------------------------------- io
def _read_json(path: str | Path, err_cls: type[TakeoffError]) -> Any:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise err_cls(f"cannot read {p}: {exc}", code="unreadable_file") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise err_cls(f"{p} is not valid JSON: {exc}", code="invalid_json") from exc


def model_to_dict(model: BuildingModel | dict) -> dict:
    """Return the canonical model as a plain dict.

    Accepts a ``BuildingModel`` (the canonical dataclass) or an already
    serialised model (the ``{"model": {...}}`` document shape, or the inner
    model dict itself). No defaulting happens here: a payload that is not a
    BuildingModel document is refused.
    """
    if isinstance(model, BuildingModel):
        return model.to_dict()["model"]
    if not isinstance(model, dict):
        raise ModelError(
            f"model must be a BuildingModel or a dict, got {type(model).__name__}",
            code="not_a_building_model",
        )
    if "model" in model:
        inner = model["model"]
        if not isinstance(inner, dict):
            raise ModelError("model document has no 'model' object", code="not_a_building_model")
        return inner
    if "spaces" not in model:
        raise ModelError("model document has no 'spaces'", code="not_a_building_model")
    return model


def load_model(path: str | Path) -> BuildingModel:
    """Load a canonical BuildingModel from a JSON file.

    Refuses (never guesses) a file that is not a BuildingModel document.
    """
    doc = _read_json(path, ModelError)
    if not isinstance(doc, dict) or "model" not in doc:
        raise ModelError(
            f"{path} is not a BuildingModel document (no 'model' key)",
            code="not_a_building_model",
        )
    try:
        return BuildingModel.from_dict(doc)
    except Exception as exc:  # pragma: no cover - depends on the payload
        raise ModelError(
            f"{path} is not a readable BuildingModel: {exc}", code="model_read_failed"
        ) from exc


def load_price_book(path: str | Path) -> dict:
    """Load and validate a price book. Refuses a book that is not complete."""
    book = _read_json(path, PriceBookError)
    validate_price_book(book)
    return book


def load_finishes(path: str | Path, book: dict | None = None) -> dict:
    """Load and validate a finishes schedule.

    When a price book is given, the finishes are also checked against it
    (every ``finish_id`` must exist as a price item).
    """
    finishes = _read_json(path, FinishesError)
    validate_finishes(finishes, book=book)
    return finishes


def validate_price_book(book: dict) -> None:
    """Raise ``PriceBookError`` unless the book states everything it needs.

    A price book that cannot be priced from is refused at load time; the
    estimate never falls back to a default price.
    """
    if not isinstance(book, dict):
        raise PriceBookError("price book must be an object", code="not_an_object")
    header = book.get("header")
    if not isinstance(header, dict):
        raise PriceBookError("price book has no header object", code="missing_header")
    for key in ("price_book_version", "currency", "region", "source", "source_date", "kind"):
        if not header.get(key):
            raise PriceBookError(
                f"price book header missing {key}",
                code="missing_header_field",
                details={"field": key},
            )
    items = book.get("items")
    if not isinstance(items, list) or not items:
        raise PriceBookError("price book has no items", code="missing_items")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise PriceBookError("price item is not an object", code="item_not_an_object")
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise PriceBookError("price item has no item_id", code="item_missing_id")
        if item_id in seen:
            raise PriceBookError(
                f"duplicate price item {item_id}",
                code="duplicate_item_id",
                details={"item_id": item_id},
            )
        seen.add(item_id)
        if not item.get("source") or not item.get("source_date"):
            raise PriceBookError(
                f"price item {item_id} missing source/source_date",
                code="item_missing_source",
                details={"item_id": item_id},
            )
        price = item.get("material_unit_price")
        if price is None:
            raise PriceBookError(
                f"price item {item_id} has no material_unit_price",
                code="item_missing_material_price",
                details={"item_id": item_id},
            )
        if float(price) < 0:
            raise PriceBookError(
                f"price item {item_id} has a negative material_unit_price",
                code="item_negative_material_price",
                details={"item_id": item_id},
            )
        category = item.get("category")
        if category not in ITEM_CATEGORIES:
            raise PriceBookError(
                f"price item {item_id} has unknown category {category!r}",
                code="item_unknown_category",
                details={"item_id": item_id, "category": category},
            )
        unit = item.get("unit")
        if unit not in ITEM_UNITS:
            raise PriceBookError(
                f"price item {item_id} has unknown unit {unit!r}",
                code="item_unknown_unit",
                details={"item_id": item_id, "unit": unit},
            )
        basis = item.get("labour_basis")
        if basis is not None:
            if basis not in LABOUR_BASES:
                raise PriceBookError(
                    f"price item {item_id} has unknown labour_basis {basis!r}",
                    code="item_unknown_labour_basis",
                    details={"item_id": item_id, "labour_basis": basis},
                )
            if basis == "m2" and item.get("labour_hours_per_m2") is None:
                raise PriceBookError(
                    f"price item {item_id} declares labour_basis m2 but no labour_hours_per_m2",
                    code="item_missing_labour_rate",
                    details={"item_id": item_id},
                )


def validate_finishes(finishes: dict, book: dict | None = None) -> None:
    """Raise ``FinishesError`` unless the finishes schedule is complete."""
    if not isinstance(finishes, dict):
        raise FinishesError("finishes must be an object", code="not_an_object")
    header = finishes.get("header")
    if not isinstance(header, dict):
        raise FinishesError("finishes have no header object", code="missing_header")
    for key in ("finishes_version", "source", "source_date"):
        if not header.get(key):
            raise FinishesError(
                f"finishes header missing {key}",
                code="missing_header_field",
                details={"field": key},
            )
    rows = finishes.get("rows")
    if not isinstance(rows, list):
        raise FinishesError("finishes have no rows list", code="missing_rows")
    known_finishes: set[str] | None = None
    if book is not None:
        known_finishes = {it.get("item_id") for it in book.get("items", [])}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FinishesError("finishes row is not an object", code="row_not_an_object")
        for key in ("space_id", "surface", "finish_id"):
            if not row.get(key):
                raise FinishesError(
                    f"finishes row missing {key}", code="row_missing_field", details={"field": key}
                )
        if row["surface"] not in FINISH_SURFACES:
            raise FinishesError(
                f"finishes row has unknown surface {row['surface']!r}",
                code="row_unknown_surface",
                details={"surface": row["surface"]},
            )
        key = (row["space_id"], row["surface"])
        if key in seen:
            raise FinishesError(
                f"duplicate finishes row for {key[0]} {key[1]}",
                code="row_duplicate_assignment",
                details={"space_id": key[0], "surface": key[1]},
            )
        seen.add(key)
        if known_finishes is not None and row["finish_id"] not in known_finishes:
            raise FinishesError(
                f"finishes row names finish {row['finish_id']!r}, "
                "which the price book does not price",
                code="row_unknown_finish_id",
                details={"finish_id": row["finish_id"]},
            )


# ------------------------------------------------------------------- geometry
def r6(x: float) -> float:
    """Round to 6 decimals (quantities)."""
    return round(float(x), 6)


def r2(x: float) -> float:
    """Round to 2 decimals (money)."""
    return round(float(x), 2)


def shoelace(poly: list) -> float:
    """Absolute area of a closed polygon (list of [x, y])."""
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def polygon_edges(poly: list) -> list:
    """Directed edges [(p, q), ...] closing the ring."""
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def edge_len(e: tuple) -> float:
    (x1, y1), (x2, y2) = e
    return math.hypot(x2 - x1, y2 - y1)


def perimeter(poly: list) -> float:
    """Closed perimeter of a polygon."""
    return sum(edge_len(e) for e in polygon_edges(poly))


def union_area_axis_aligned(polys: list) -> float | None:
    """Exact union area via coordinate compression.

    Exact only when every edge is axis-aligned; returns ``None`` otherwise
    (the caller reports the quantity as unquantified with a reason).
    """
    for poly in polys:
        for (x1, y1), (x2, y2) in polygon_edges(poly):
            if abs(x2 - x1) > EPS and abs(y2 - y1) > EPS:
                return None
    xs = sorted({p[0] for poly in polys for p in poly})
    ys = sorted({p[1] for poly in polys for p in poly})

    def inside(x: float, y: float, poly: list) -> bool:
        # ray casting, point strictly inside test for cell centres
        c = False
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            if (y1 > y) != (y2 > y):
                xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if x < xin:
                    c = not c
        return c

    total = 0.0
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            cx = (xs[i] + xs[i + 1]) / 2.0
            cy = (ys[j] + ys[j + 1]) / 2.0
            if any(inside(cx, cy, poly) for poly in polys):
                total += (xs[i + 1] - xs[i]) * (ys[j + 1] - ys[j])
    return total


# --------------------------------------------------------------------- model
def space_items(model: dict) -> list:
    """Spaces as a sorted list of ``(space_id, space_dict)`` pairs."""
    spaces = model.get("spaces") or {}
    if isinstance(spaces, dict):
        return sorted(spaces.items())
    return sorted(((s["id"], s) for s in spaces))


def space_height(space: dict, levels: dict) -> tuple:
    """Space height and where it came from.

    Hierarchy: ``ceiling_height_m`` -> ``height_m`` -> ``Level.wall_height_m``.
    A level value equal to the dataclass default (3.0 m) is flagged: on its
    own it cannot be told apart from a default. ``(None, "missing", ...)``
    means the space has no height source at all - never a default.
    """
    if space.get("ceiling_height_m") is not None:
        return float(space["ceiling_height_m"]), "space.ceiling_height_m", []
    if space.get("height_m") is not None:
        flags: list = []
        lvl = levels.get(space.get("level_id"), {})
        if lvl.get("wall_height_m") is not None and abs(float(lvl["wall_height_m"]) - 3.0) < EPS:
            flags.append("level_wall_height_equals_dataclass_default_3.0")
        return float(space["height_m"]), "space.height_m", flags
    lvl = levels.get(space.get("level_id"), {})
    if lvl.get("wall_height_m") is not None:
        return float(lvl["wall_height_m"]), "level.wall_height_m", [
            "height_from_level_wall_height_m_indistinguishable_from_dataclass_default"
        ]
    return None, "missing", ["no_height_source"]


def opening_area(opening: dict) -> float | None:
    """Opening area from the stated area, else from its dimensions."""
    if opening.get("area_m2") is not None:
        return float(opening["area_m2"])
    if opening.get("width_m") is not None and opening.get("height_m") is not None:
        return float(opening["width_m"]) * float(opening["height_m"])
    return None


# -------------------------------------------------------- interior partitions
def _edge_frame(edge: tuple) -> tuple:
    """Frame of an edge: ``(angle_key, perp_offset, t0, t1, direction_sign)``.

    ``angle_key``: 0 = horizontal, 1 = vertical, ``None`` = diagonal
    (unsupported for pairing; reported unmatched, never dropped - D4).
    """
    (x1, y1), (x2, y2) = edge
    if abs(y2 - y1) <= EPS and abs(x2 - x1) > EPS:
        return 0, y1, min(x1, x2), max(x1, x2), 1 if x2 > x1 else -1
    if abs(x2 - x1) <= EPS and abs(y2 - y1) > EPS:
        return 1, x1, min(y1, y2), max(y1, y2), 1 if y2 > y1 else -1
    return None, None, None, None, None


def _subtract_intervals(free: list, a: float, b: float) -> list:
    out = []
    for (x, y) in free:
        if b <= x or a >= y:
            out.append((x, y))
            continue
        if a > x:
            out.append((x, a))
        if b < y:
            out.append((b, y))
    return [(x, y) for (x, y) in out if y - x > EPS]


def compute_partitions(model: dict, levels: dict) -> tuple:
    """Interior partition lengths/areas (Phase 0 section 2).

    Returns ``(partitions, per_level_stats, unmatched_edges)``.

    * ``partitions``: ``[{"space_a", "space_b", "level_id", "length_m",
      "area_m2", "kind", "pairing"}]`` - one entry per space pair.
    * ``per_level_stats``: interior edge total, unmatched length and the
      unmatched percentage per level (the G6 budget).
    * ``unmatched_edges``: every interior edge the pairing rule could not use,
      with the reason (D4).
    """
    items = space_items(model)
    envelope = model.get("envelope") or []
    env_frames = []
    for seg in envelope:
        fr = _edge_frame((seg["from_m"], seg["to_m"]))
        if fr[0] is not None:
            env_frames.append(fr)

    def is_envelope_adjacent(fr: tuple) -> bool:
        ang, off, t0, t1, _ = fr
        for (ea, eo, et0, et1, _) in env_frames:
            if ea == ang and abs(eo - off) <= MAX_OFFSET_M + EPS:
                if min(t1, et1) - max(t0, et0) >= MIN_OVERLAP_M:
                    return True
        return False

    edges = []
    for sid, sp in items:
        for e in polygon_edges(sp["polygon_m"]):
            fr = _edge_frame(e)
            if fr[0] is None:
                edges.append(
                    {
                        "space_id": sid,
                        "level_id": sp.get("level_id", ""),
                        "frame": None,
                        "free": [],
                        "diag_len": edge_len(e),
                        "reason": "diagonal_edge_not_pairable",
                    }
                )
                continue
            if is_envelope_adjacent(fr):
                continue
            edges.append(
                {
                    "space_id": sid,
                    "level_id": sp.get("level_id", ""),
                    "frame": fr,
                    "free": [(fr[2], fr[3])],
                    "diag_len": edge_len(e),
                    "reason": None,
                }
            )

    cands = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            a, b = edges[i], edges[j]
            if a["space_id"] == b["space_id"] or a["level_id"] != b["level_id"]:
                continue
            if a["frame"] is None or b["frame"] is None:
                continue
            aa, ao, at0, at1, adir = a["frame"]
            ba, bo, bt0, bt1, bdir = b["frame"]
            if aa != ba:
                continue
            off = abs(ao - bo)
            if off <= COLLINEAR_TOL_M:
                kind = "exact"
            elif off <= MAX_OFFSET_M and adir * bdir < 0:
                kind = "offset"  # must face each other (opposite directions)
            else:
                continue
            ov = min(at1, bt1) - max(at0, bt0)
            if ov >= MIN_OVERLAP_M:
                cands.append((ov, i, j, kind))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))

    pair_len: dict = {}
    for ov, i, j, kind in cands:
        a, b = edges[i], edges[j]
        span = (max(a["frame"][2], b["frame"][2]), min(a["frame"][3], b["frame"][3]))
        for (x, y) in list(a["free"]):
            lo, hi = max(x, span[0]), min(y, span[1])
            if hi - lo < MIN_OVERLAP_M:
                continue
            ok = any(
                max(x2, lo) == lo and min(y2, hi) >= hi - EPS
                or (max(x2, lo) < min(y2, hi) and min(y2, hi) - max(x2, lo) >= MIN_OVERLAP_M)
                for (x2, y2) in b["free"]
            )
            if not ok:
                continue
            best = None
            for (x2, y2) in b["free"]:
                lo2, hi2 = max(lo, x2), min(hi, y2)
                if hi2 - lo2 >= MIN_OVERLAP_M and (best is None or hi2 - lo2 > best[1] - best[0]):
                    best = (lo2, hi2)
            if best is None:
                continue
            lo2, hi2 = best
            a["free"] = _subtract_intervals(a["free"], lo2, hi2)
            b["free"] = _subtract_intervals(b["free"], lo2, hi2)
            key = tuple(sorted((a["space_id"], b["space_id"]))) + (a["level_id"],)
            rec = pair_len.setdefault(key, [0.0, set()])
            rec[0] += hi2 - lo2
            rec[1].add(kind)

    space_by_id = dict(items)
    partitions = []
    for (sa, sb, lid), (length, kinds) in sorted(pair_len.items()):
        ha, _, _ = space_height(space_by_id[sa], levels)
        hb, _, _ = space_height(space_by_id[sb], levels)
        h = min(ha, hb) if ha is not None and hb is not None else None
        partitions.append(
            {
                "space_a": sa,
                "space_b": sb,
                "level_id": lid,
                "length_m": r6(length),
                "area_m2": r6(length * h) if h is not None else None,
                "height_m": h,
                "pairing": sorted(kinds),
                # D6: reported, not priced - the per-space face lines already
                # cover the partition faces.
                "pricing_status": "not_priced",
                "not_priced_reason": "per_space_face_basis_covers_partition_faces",
            }
        )

    per_level: dict = {}
    unmatched_edges = []
    for e in edges:
        st = per_level.setdefault(
            e["level_id"],
            {"interior_edge_total_m": 0.0, "unmatched_m": 0.0, "unmatched_reasons": {}},
        )
        if e["frame"] is None:
            st["interior_edge_total_m"] += e["diag_len"]
            st["unmatched_m"] += e["diag_len"]
            st["unmatched_reasons"]["diagonal_edge_not_pairable"] = (
                st["unmatched_reasons"].get("diagonal_edge_not_pairable", 0.0) + e["diag_len"]
            )
            unmatched_edges.append(
                {
                    "level_id": e["level_id"],
                    "space_id": e["space_id"],
                    "length_m": r6(e["diag_len"]),
                    "reason": "diagonal_edge_not_pairable",
                }
            )
        else:
            st["interior_edge_total_m"] += e["frame"][3] - e["frame"][2]
            st["unmatched_m"] += sum(y - x for (x, y) in e["free"])
    for lid, st in per_level.items():
        tot = st["interior_edge_total_m"]
        st["interior_edge_total_m"] = r6(tot)
        st["unmatched_m"] = r6(st["unmatched_m"])
        st["unmatched_pct"] = r6(100.0 * st["unmatched_m"] / tot) if tot > 0 else 0.0
        st["unmatched_reasons"] = {k: r6(v) for k, v in sorted(st["unmatched_reasons"].items())}
    unmatched_edges.sort(key=lambda u: (u["level_id"], u["space_id"], u["length_m"]))
    return partitions, per_level, unmatched_edges


# --------------------------------------------------------- envelope matching
def _facade_axis(segments: list) -> tuple | None:
    """Origin and unit direction of a facade axis (D3).

    The reference segment is the one with the smallest id; its ``from_m`` is
    the facade origin and its direction is the axis. Recorded in the output
    so the interval frame is never implicit.
    """
    ref = min(segments, key=lambda s: str(s.get("id", "")))
    fx, fy = ref["from_m"]
    tx, ty = ref["to_m"]
    dx, dy = tx - fx, ty - fy
    n = math.hypot(dx, dy)
    if n <= EPS:
        return None
    return (fx, fy), (dx / n, dy / n)


def _project(point: list, origin: tuple, direction: tuple) -> float:
    return (point[0] - origin[0]) * direction[0] + (point[1] - origin[1]) * direction[1]


def match_openings_to_segments(model: dict, openings: list) -> tuple:
    """Match openings to envelope segments along the facade axis (D3).

    Returns ``(hosting, warnings)`` where ``hosting`` maps a segment id to a
    list of ``{"opening_id", "area_m2", "fraction", "basis"}`` deductions and
    ``warnings`` lists the machine-readable notes (interval fallbacks,
    space-id mismatches, intervals that no segment covers).
    """
    segments = model.get("envelope") or []
    by_facade: dict = {}
    for seg in segments:
        by_facade.setdefault(seg.get("facade", ""), []).append(seg)

    hosting: dict = {}
    warnings: list = []

    for facade in sorted(by_facade):
        segs = sorted(by_facade[facade], key=lambda s: str(s.get("id", "")))
        axis = _facade_axis(segs)
        if axis is None:
            warnings.append(
                {
                    "code": "facade_axis_undefined",
                    "facade": facade,
                    "detail": "degenerate reference segment",
                }
            )
            continue
        origin, direction = axis
        spans = []
        for seg in segs:
            s0 = _project(seg["from_m"], origin, direction)
            s1 = _project(seg["to_m"], origin, direction)
            if s1 < s0:
                s0, s1 = s1, s0
            spans.append((seg, s0, s1))
        for op in openings:
            if op.get("host_facade") != facade:
                continue
            interval = op.get("host_interval_m")
            if not interval or len(interval) < 2:
                # No interval stated: fall back to the legacy rule (same
                # facade + same space) and say so.
                candidates = [s for (s, _, _) in spans if s.get("space_id") == op.get("space_id")]
                if not candidates:
                    continue
                target = candidates[0]
                hosting.setdefault(target.get("id", ""), []).append(
                    {
                        "opening_id": op["opening_id"],
                        "area_m2": op.get("area_m2") or 0.0,
                        "fraction": 1.0,
                        "basis": "facade_space_id_fallback",
                    }
                )
                if len(candidates) > 1:
                    warnings.append(
                        {
                            "code": "opening_matched_to_first_matching_segment",
                            "opening_id": op["opening_id"],
                            "facade": facade,
                            "segment_ids": [s.get("id", "") for s in candidates],
                        }
                    )
                continue
            a, b = float(interval[0]), float(interval[1])
            if b < a:
                a, b = b, a
            span_len = b - a
            matched = []
            for (seg, s0, s1) in spans:
                ov = min(b, s1) - max(a, s0)
                if ov <= EPS:
                    continue
                fraction = ov / span_len if span_len > EPS else 1.0
                matched.append((seg, ov, fraction))
            if not matched:
                warnings.append(
                    {
                        "code": "opening_interval_matches_no_segment",
                        "opening_id": op["opening_id"],
                        "facade": facade,
                    }
                )
                continue
            covered = sum(ov for (_, ov, _) in matched)
            if span_len > EPS and covered < span_len - EPS:
                warnings.append(
                    {
                        "code": "opening_interval_partly_uncovered",
                        "opening_id": op["opening_id"],
                        "facade": facade,
                        "uncovered_m": r6(span_len - covered),
                    }
                )
            for (seg, ov, fraction) in matched:
                hosting.setdefault(seg.get("id", ""), []).append(
                    {
                        "opening_id": op["opening_id"],
                        "area_m2": op.get("area_m2") or 0.0,
                        "fraction": r6(fraction),
                        "basis": "facade_interval",
                        "overlap_m": r6(ov),
                    }
                )
                if seg.get("space_id") != op.get("space_id"):
                    warnings.append(
                        {
                            "code": "opening_interval_space_id_mismatch",
                            "opening_id": op["opening_id"],
                            "segment_id": seg.get("id", ""),
                            "segment_space_id": seg.get("space_id", ""),
                            "opening_space_id": op.get("space_id", ""),
                        }
                    )
    for seg_id in hosting:
        hosting[seg_id].sort(key=lambda d: d["opening_id"])
    return hosting, warnings


# ----------------------------------------------------------------- quantities
def compute_quantities(model: BuildingModel | dict, roof_basis: str = "auto") -> dict:
    """Compute every takeoff quantity for the canonical model.

    ``roof_basis`` (D2): ``"auto"`` (stored roof planes when they carry an
    area, else the union of the space polygons), ``"roof_planes"`` (stored
    roof planes only; unquantified with a reason when there are none) or
    ``"space_union"`` (the union of the space polygons only).
    """
    if roof_basis not in ROOF_BASES:
        raise ValueError(f"unknown roof_basis {roof_basis!r}; expected one of {ROOF_BASES}")
    m = model_to_dict(model)
    levels = {l["id"]: l for l in (m.get("levels") or [])}
    items = space_items(m)
    space_by_id = dict(items)

    q_spaces: dict = {}
    all_openings: list = []
    for sid, sp in items:
        poly = sp["polygon_m"]
        stored = sp.get("area_m2")
        recomputed = shoelace(poly)
        drift_pct = None
        if stored:
            drift_pct = r6(100.0 * abs(float(stored) - recomputed) / float(stored))
        height, h_source, h_flags = space_height(sp, levels)
        perim = perimeter(poly)
        wall_gross = perim * height if height is not None else None

        ops = []
        wall_deduction = 0.0
        trim_deduction = 0.0
        for o in sp.get("openings") or []:
            area = opening_area(o)
            rec = {
                "opening_id": o["id"],
                "space_id": sid,
                "adjacent_space_id": o.get("adjacent_space_id"),
                "category": o.get("category", ""),
                "tag": o.get("tag", ""),
                "width_m": o.get("width_m"),
                "height_m": o.get("height_m"),
                "sill_m": o.get("sill_m"),
                "host_facade": o.get("host_facade", ""),
                "host_interval_m": o.get("host_interval_m"),
                "area_m2": r6(area) if area is not None else None,
                "needs_review": bool(o.get("needs_review", False)),
            }
            ops.append(rec)
            all_openings.append(rec)
            if area is None:
                rec["unquantified_reason"] = "no_area_or_dimensions"
            if o.get("category") != "skylight" and area is not None:
                wall_deduction += area
            if o.get("sill_m") == 0 and o.get("width_m") is not None:
                trim_deduction += float(o["width_m"])
        # far-side deductions: interior openings stored on the neighbour
        far_deduction = 0.0
        far_trim = 0.0
        for other_sid, other in items:
            if other_sid == sid:
                continue
            for o in other.get("openings") or []:
                if o.get("adjacent_space_id") == sid:
                    area = opening_area(o)
                    if o.get("category") != "skylight" and area is not None:
                        far_deduction += area
                    if o.get("sill_m") == 0 and o.get("width_m") is not None:
                        far_trim += float(o["width_m"])
        wall_net = (wall_gross - wall_deduction - far_deduction) if wall_gross is not None else None
        q = {
            "level_id": sp.get("level_id", ""),
            "floor_area_stored_m2": r6(stored) if stored is not None else None,
            "floor_area_recomputed_m2": r6(recomputed),
            "floor_area_drift_pct": drift_pct,
            "ceiling_area_m2": r6(recomputed),
            "height_m": height,
            "height_source": h_source,
            "height_flags": h_flags,
            "perimeter_m": r6(perim),
            "wall_gross_m2": r6(wall_gross) if wall_gross is not None else None,
            "wall_net_m2": r6(wall_net) if wall_net is not None else None,
            "trim_length_m": r6(perim - trim_deduction - far_trim),
            "volume_stored_m3": r6(sp["volume_m3"]) if sp.get("volume_m3") is not None else None,
            "openings": ops,
        }
        if wall_gross is None:
            q["wall_gross_unquantified_reason"] = "no_height_source"
            q["wall_net_unquantified_reason"] = "no_height_source"
        q_spaces[sid] = q

    # envelope by facade and by segment (D3 interval matching)
    hosting, warnings = match_openings_to_segments(m, all_openings)
    env_segments = []
    env_by_facade: dict = {}
    for seg in m.get("envelope") or []:
        gross = seg.get("area_m2")
        if gross is None and seg.get("length_m") is not None and seg.get("height_m") is not None:
            gross = float(seg["length_m"]) * float(seg["height_m"])
        deductions = hosting.get(seg.get("id", ""), [])
        ded = 0.0
        hosted = []
        for d in deductions:
            ded += float(d["area_m2"]) * float(d["fraction"])
            hosted.append(d["opening_id"])
        net = gross - ded if gross is not None else None
        rec = {
            "segment_id": seg.get("id", ""),
            "facade": seg.get("facade", ""),
            "space_id": seg.get("space_id", ""),
            "construction_id": seg.get("construction_id", ""),
            "length_m": seg.get("length_m"),
            "height_m": seg.get("height_m"),
            "gross_m2": r6(gross) if gross is not None else None,
            "net_m2": r6(net) if net is not None else None,
            "hosted_opening_ids": hosted,
            "opening_deductions": deductions,
        }
        if gross is None:
            rec["unquantified_reason"] = "no_area_or_length_and_height"
        env_segments.append(rec)
        f = env_by_facade.setdefault(seg.get("facade", ""), {"gross_m2": 0.0, "net_m2": 0.0})
        if gross is not None:
            f["gross_m2"] += gross
            f["net_m2"] += net
    for f in env_by_facade.values():
        f["gross_m2"] = r6(f["gross_m2"])
        f["net_m2"] = r6(f["net_m2"])

    partitions, partition_levels, unmatched_edges = compute_partitions(m, levels)

    # roof: stored planes, else the flat roof as the union of the level's
    # space polygons. The basis is always stated (D2).
    roof_planes = m.get("roof_planes") or []
    plane_area = None
    if roof_planes:
        areas = [float(p["area_m2"]) for p in roof_planes if p.get("area_m2") is not None]
        plane_area = r6(sum(areas)) if len(areas) == len(roof_planes) else None
    polys = [sp["polygon_m"] for _, sp in items]
    union_area = union_area_axis_aligned(polys)
    if roof_basis == "roof_planes":
        roof = {
            "basis": "roof_planes_stored_area",
            "area_m2": plane_area,
            "basis_note": "stored roof plane areas (explicit basis).",
        }
        if plane_area is None:
            roof["unquantified_reason"] = "no_roof_plane_areas"
    elif roof_basis == "space_union":
        roof = {
            "basis": "flat_roof_union_of_space_polygons",
            "area_m2": r6(union_area) if union_area is not None else None,
            "basis_note": (
                "inner-face union of the space polygons (explicit basis); the "
                "wall-thickness band is not covered."
            ),
        }
        if union_area is None:
            roof["unquantified_reason"] = "non_axis_aligned_polygon"
    else:  # auto
        if plane_area is not None:
            roof = {
                "basis": "roof_planes_stored_area",
                "area_m2": plane_area,
                "basis_note": "stored roof plane areas.",
            }
        else:
            roof = {
                "basis": "flat_roof_union_of_space_polygons",
                "area_m2": r6(union_area) if union_area is not None else None,
                "basis_note": (
                    "inner-face union of the space polygons; the wall-thickness "
                    "band is not covered. Pin roof_basis to make the choice explicit."
                ),
            }
            if union_area is None:
                roof["unquantified_reason"] = "non_axis_aligned_polygon"

    # opening rollup by category + tag
    rollup: dict = {}
    for rec in all_openings:
        key = f"{rec['category']}|{rec['tag']}"
        r = rollup.setdefault(
            key, {"category": rec["category"], "tag": rec["tag"], "count": 0, "area_m2": 0.0}
        )
        r["count"] += 1
        if rec["area_m2"] is not None:
            r["area_m2"] += rec["area_m2"]
    for r in rollup.values():
        r["area_m2"] = r6(r["area_m2"])

    return {
        "spaces": q_spaces,
        "envelope_segments": env_segments,
        "envelope_by_facade": env_by_facade,
        "envelope_gross_total_m2": r6(
            sum(s["gross_m2"] for s in env_segments if s["gross_m2"] is not None)
        ),
        "envelope_net_total_m2": r6(
            sum(s["net_m2"] for s in env_segments if s["net_m2"] is not None)
        ),
        "partitions": partitions,
        "partition_levels": partition_levels,
        "unmatched_edges": unmatched_edges,
        "openings": all_openings,
        "opening_rollup": [rollup[k] for k in sorted(rollup)],
        "roof": roof,
        "roof_basis_requested": roof_basis,
        "warnings": warnings,
        "total_floor_area_m2": r6(sum(q["floor_area_recomputed_m2"] for q in q_spaces.values())),
    }


# -------------------------------------------------------------------- pricing
def _labour(item: dict, quantity: float, area_m2: float | None) -> dict:
    """Labour for a priced line (D5).

    ``labour_basis`` ``"unit"`` (default, legacy per-unit fields) or
    ``"m2"`` (per square metre of the line's area basis). The basis and
    whether it was stated or inferred are always reported.
    """
    basis = item.get("labour_basis")
    stated = basis is not None
    if not stated:
        basis = "unit"
    rate = item.get("labour_rate_per_hour")
    if basis == "m2":
        per_m2 = item.get("labour_hours_per_m2")
        if per_m2 is None:
            return {
                "hours": None,
                "cost": None,
                "flag": "no_labour_rate",
                "basis": basis,
                "stated": stated,
            }
        if area_m2 is None:
            return {
                "hours": None,
                "cost": None,
                "flag": "no_area_basis_for_labour",
                "basis": basis,
                "stated": stated,
            }
        hours = float(area_m2) * float(per_m2)
    else:
        per_unit = item.get("labour_hours_per_unit")
        if per_unit is None:
            return {
                "hours": None,
                "cost": None,
                "flag": "no_labour_rate",
                "basis": basis,
                "stated": stated,
            }
        hours = float(quantity) * float(per_unit)
    if rate is None:
        return {
            "hours": None,
            "cost": None,
            "flag": "no_labour_rate",
            "basis": basis,
            "stated": stated,
        }
    return {
        "hours": r6(hours),
        "cost": r2(hours * float(rate)),
        "flag": None,
        "basis": basis,
        "stated": stated,
    }


def _priced_line(
    line_id: str,
    kind: str,
    category: str,
    element_ids: list,
    quantity: float,
    unit: str,
    rule: str,
    item: dict,
    book_version: str,
    basis: str | None = None,
    area_m2: float | None = None,
) -> dict:
    mat = r2(float(quantity) * float(item["material_unit_price"]))
    lab = _labour(item, quantity, area_m2)
    line = {
        "line_id": line_id,
        "kind": kind,
        "category": category,
        "status": "priced",
        "quantity": r6(quantity),
        "unit": unit,
        "rule": rule,
        "material_cost": mat,
        "labour_hours": lab["hours"],
        "labour_cost": lab["cost"],
        "labour_basis": lab["basis"],
        "labour_basis_stated": lab["stated"],
        "line_total": r2(mat + (lab["cost"] or 0.0)),
        "provenance": {
            "element_ids": element_ids,
            "rule": rule,
            "price_item_id": item["item_id"],
            "price_book_version": book_version,
        },
    }
    if lab["flag"]:
        line["labour_flag"] = lab["flag"]
    if basis:
        line["basis"] = basis
    return line


def _unpriced_line(
    line_id: str,
    kind: str,
    category: str,
    element_ids: list,
    quantity: float | None,
    unit: str,
    rule: str,
    reason: str,
    basis: str | None = None,
) -> dict:
    line = {
        "line_id": line_id,
        "kind": kind,
        "category": category,
        "status": "unpriced",
        "quantity": r6(quantity) if quantity is not None else None,
        "unit": unit,
        "rule": rule,
        "reason": reason,
        "material_cost": None,
        "labour_hours": None,
        "labour_cost": None,
        "line_total": None,
        "provenance": {
            "element_ids": element_ids,
            "rule": rule,
            "price_item_id": None,
            "price_book_version": None,
        },
    }
    if basis:
        line["basis"] = basis
    return line


def _trim_item(items: dict) -> dict | None:
    """The book's trim item (by id, or by a trim category item)."""
    if "trim_baseboard" in items:
        return items["trim_baseboard"]
    for item_id in sorted(items):
        if items[item_id].get("category") == "trim":
            return items[item_id]
    return None


def build_estimate(
    case_id: str,
    model: BuildingModel | dict,
    quantities: dict,
    book: dict,
    finishes: dict,
) -> dict:
    """Price the quantities into an estimate.

    Every line is either ``priced`` (it carries money and its provenance) or
    ``unpriced`` (it carries a machine-readable ``reason``). No line is ever
    dropped for want of a price.
    """
    validate_price_book(book)
    validate_finishes(finishes)
    m = model_to_dict(model)
    known_spaces = set(dict(space_items(m)))
    for row in finishes["rows"]:
        if row["space_id"] not in known_spaces:
            raise FinishesError(
                f"finishes row names space {row['space_id']!r}, which the model does not contain",
                code="row_unknown_space_id",
                details={"space_id": row["space_id"]},
            )
    version = book["header"]["price_book_version"]
    items = {it["item_id"]: it for it in book["items"]}
    fin = {(r["space_id"], r["surface"]): r["finish_id"] for r in finishes["rows"]}
    lines: list = []

    def lid(*parts) -> str:
        return ":".join([case_id] + [str(p) for p in parts])

    # --- finish-driven lines: paint (wall/ceiling), flooring/tile (floor)
    for sid in sorted(quantities["spaces"]):
        q = quantities["spaces"][sid]
        for surface in ("wall", "ceiling", "floor"):
            finish_id = fin.get((sid, surface))
            if finish_id is None:
                continue
            item = items.get(finish_id)
            if surface in ("wall", "ceiling"):
                area = q["wall_net_m2"] if surface == "wall" else q["ceiling_area_m2"]
                if item is None:
                    lines.append(
                        _unpriced_line(
                            lid("paint", sid, surface),
                            "paint",
                            "paint",
                            [sid],
                            area,
                            "m2",
                            "paint_volume",
                            "no_price_item",
                        )
                    )
                    continue
                if item.get("coverage_m2_per_l") is None:
                    lines.append(
                        _unpriced_line(
                            lid("paint", sid, surface),
                            "paint",
                            "paint",
                            [sid],
                            area,
                            "m2",
                            "paint_volume",
                            "no_coverage",
                        )
                    )
                    continue
                if item.get("coats") is None:
                    lines.append(
                        _unpriced_line(
                            lid("paint", sid, surface),
                            "paint",
                            "paint",
                            [sid],
                            area,
                            "m2",
                            "paint_volume",
                            "no_coats",
                        )
                    )
                    continue
                if area is None:
                    lines.append(
                        _unpriced_line(
                            lid("paint", sid, surface),
                            "paint",
                            "paint",
                            [sid],
                            None,
                            "m2",
                            "paint_volume",
                            "no_area_basis",
                        )
                    )
                    continue
                litres = area * float(item["coats"]) / float(item["coverage_m2_per_l"])
                if item.get("container_l") is None:
                    ln = _unpriced_line(
                        lid("paint", sid, surface),
                        "paint",
                        "paint",
                        [sid],
                        litres,
                        "l",
                        "paint_purchase",
                        "no_container_size",
                    )
                    ln["quantity_basis_m2"] = r6(area)
                    lines.append(ln)
                    continue
                buy = (
                    math.ceil(litres / float(item["container_l"]) - EPS)
                    * float(item["container_l"])
                )
                ln = _priced_line(
                    lid("paint", sid, surface),
                    "paint",
                    "paint",
                    [sid],
                    buy,
                    "l",
                    "paint_purchase",
                    item,
                    version,
                    basis="per_space_face",
                    area_m2=area,
                )
                ln["quantity_detail"] = {
                    "net_area_m2": r6(area),
                    "litres_exact": r6(litres),
                    "container_l": item["container_l"],
                }
                lines.append(ln)
            else:  # floor
                area = q["floor_area_recomputed_m2"]
                item_cat = (item or {}).get("category", "")
                rule = "tile_purchase" if item_cat == "tile" else "flooring_purchase"
                kind = rule.split("_")[0]
                if item is None:
                    lines.append(
                        _unpriced_line(
                            lid(kind, sid, "floor"),
                            kind,
                            kind,
                            [sid],
                            area,
                            "m2",
                            rule,
                            "no_price_item",
                        )
                    )
                    continue
                if item.get("waste_pct") is None:
                    lines.append(
                        _unpriced_line(
                            lid(kind, sid, "floor"),
                            kind,
                            kind,
                            [sid],
                            area,
                            "m2",
                            rule,
                            "no_waste_pct",
                        )
                    )
                    continue
                buy = area * (1.0 + float(item["waste_pct"]) / 100.0)
                ln = _priced_line(
                    lid(kind, sid, "floor"),
                    kind,
                    kind,
                    [sid],
                    buy,
                    "m2",
                    rule,
                    item,
                    version,
                    basis="finished_floor_area_plus_waste",
                    area_m2=area,
                )
                ln["quantity_detail"] = {
                    "finished_area_m2": r6(area),
                    "waste_pct": item["waste_pct"],
                }
                lines.append(ln)
                if item.get("spare_pct") is not None:
                    spare_qty = area * float(item["spare_pct"]) / 100.0
                    sl = _priced_line(
                        lid("spare", sid, "floor"),
                        "spare_stock",
                        "spare",
                        [sid],
                        spare_qty,
                        "m2",
                        "spare_stock",
                        item,
                        version,
                        basis="finished_area_x_spare_pct_material_only",
                        area_m2=area,
                    )
                    sl["labour_hours"], sl["labour_cost"] = 0.0, 0.0
                    sl["line_total"] = sl["material_cost"]
                    lines.append(sl)

    # --- opening unit lines (one per opening element)
    opening_items = [it for it in book["items"] if it.get("category") == "opening"]
    for rec in quantities["openings"]:
        match = None
        for it in opening_items:
            at = it.get("applies_to", {})
            if at.get("category") != rec["category"]:
                continue
            if "tag" in at and at["tag"] != rec["tag"]:
                continue
            match = it
            break
        el = [rec["opening_id"]]
        if match is None:
            lines.append(
                _unpriced_line(
                    lid("opening", rec["opening_id"]),
                    "opening_unit",
                    "opening",
                    el,
                    1,
                    "count",
                    "opening_unit",
                    "no_price_item",
                )
            )
        else:
            ln = _priced_line(
                lid("opening", rec["opening_id"]),
                "opening_unit",
                "opening",
                el,
                1,
                "count",
                "opening_unit",
                match,
                version,
            )
            ln["opening_detail"] = {
                "space_id": rec["space_id"],
                "category": rec["category"],
                "tag": rec["tag"],
                "host_facade": rec["host_facade"],
                "adjacent_space_id": rec["adjacent_space_id"],
            }
            lines.append(ln)

    # --- trim lines per space
    trim_item = _trim_item(items)
    for sid in sorted(quantities["spaces"]):
        q = quantities["spaces"][sid]
        if trim_item is None:
            lines.append(
                _unpriced_line(
                    lid("trim", sid),
                    "trim_length",
                    "trim",
                    [sid],
                    q["trim_length_m"],
                    "m",
                    "trim_length",
                    "no_price_item",
                )
            )
        else:
            lines.append(
                _priced_line(
                    lid("trim", sid),
                    "trim_length",
                    "trim",
                    [sid],
                    q["trim_length_m"],
                    "m",
                    "trim_length",
                    trim_item,
                    version,
                )
            )

    # --- envelope wall assembly, grouped by construction_id (envelope basis,
    #     never the per-space face basis used for paint)
    by_constr: dict = {}
    for seg in quantities["envelope_segments"]:
        g = by_constr.setdefault(seg["construction_id"], {"net": 0.0, "ids": []})
        if seg["net_m2"] is not None:
            g["net"] += seg["net_m2"]
        g["ids"].append(seg["segment_id"])
    asm_items = [it for it in book["items"] if it.get("category") == "wall_assembly"]
    for cid in sorted(by_constr):
        g = by_constr[cid]
        key = cid if cid else "none"
        if not cid:
            lines.append(
                _unpriced_line(
                    lid("wall_assembly", key),
                    "wall_assembly",
                    "wall_assembly",
                    g["ids"],
                    g["net"],
                    "m2",
                    "wall_assembly",
                    "no_construction",
                    basis="envelope_net",
                )
            )
            continue
        match = next(
            (it for it in asm_items if it.get("applies_to", {}).get("construction_id") == cid),
            None,
        )
        if match is None:
            lines.append(
                _unpriced_line(
                    lid("wall_assembly", key),
                    "wall_assembly",
                    "wall_assembly",
                    g["ids"],
                    g["net"],
                    "m2",
                    "wall_assembly",
                    "no_price_item",
                    basis="envelope_net",
                )
            )
        else:
            lines.append(
                _priced_line(
                    lid("wall_assembly", key),
                    "wall_assembly",
                    "wall_assembly",
                    g["ids"],
                    g["net"],
                    "m2",
                    "wall_assembly",
                    match,
                    version,
                    basis="envelope_net",
                    area_m2=g["net"],
                )
            )

    lines.sort(key=lambda l: l["line_id"])
    subtotals: dict = {}
    total = 0.0
    for ln in lines:
        if ln["status"] == "priced":
            subtotals[ln["category"]] = r2(subtotals.get(ln["category"], 0.0) + ln["line_total"])
            total += ln["line_total"]
    unpriced = [ln["line_id"] for ln in lines if ln["status"] != "priced"]
    partitions = quantities.get("partitions") or []
    partition_items = sorted(
        it["item_id"] for it in book["items"] if it.get("category") == "partition"
    )
    estimate = {
        "case_id": case_id,
        "model_name": m.get("name", ""),
        "price_book_version": version,
        "price_book_kind": book["header"].get("kind", ""),
        "finishes_version": finishes["header"]["finishes_version"],
        "currency": book["header"]["currency"],
        "quantities": quantities,
        "lines": lines,
        "subtotals_by_category": {k: subtotals[k] for k in sorted(subtotals)},
        "total": r2(total),
        "total_label": "priced_lines_only" if unpriced else "all_lines_priced",
        "unpriced_line_ids": unpriced,
        # D6: the partition pricing basis is stated, never implied.
        "partition_pricing_basis": {
            "status": "not_priced",
            "reason": "per_space_face_basis_covers_partition_faces",
            "partition_count": len(partitions),
            "partition_area_m2": r6(
                sum(p["area_m2"] for p in partitions if p.get("area_m2") is not None)
            ),
        },
        "not_priced_items": (
            [
                {
                    "price_item_id": item_id,
                    "reason": "partition_pricing_not_enabled_in_this_release",
                }
                for item_id in partition_items
            ]
            if partition_items
            else []
        ),
        "warnings": list(quantities.get("warnings") or []),
    }
    return estimate


def estimate_from_files(
    case_id: str,
    model_path: str | Path,
    book_path: str | Path,
    finishes_path: str | Path,
    roof_basis: str = "auto",
) -> dict:
    """Load the three inputs and build the estimate in one call."""
    model = load_model(model_path)
    book = load_price_book(book_path)
    finishes = load_finishes(finishes_path, book=book)
    quantities = compute_quantities(model, roof_basis=roof_basis)
    return build_estimate(case_id, model, quantities, book, finishes)


def serialize(estimate: dict) -> str:
    """Deterministic JSON serialisation of an estimate (stable key order)."""
    return json.dumps(estimate, sort_keys=True, indent=2) + "\n"
