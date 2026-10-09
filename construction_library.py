"""Cited construction library for envelope U-values (#747).

Drawings rarely state U-values, so a wall-type tag such as ``W-1 8" CMU`` or a
roof note such as ``rigid insulation above deck`` is resolved to the ASHRAE
90.1-2019 Table 5.5 maximum assembly value for that construction class and
climate zone (``construction_library_data.py``, generated from the same
openstudio-standards tag as the space-use defaults, #685).

Rules, same as the DOE prototype defaults:

* A stated value (drawing, schedule, IFC ThermalTransmittance) is never
  overwritten; only constructions whose ``u_value_w_m2k`` is unset are filled.
* Nothing is guessed. A construction resolves only when its tag or
  description names exactly one construction class; no match, two classes, or
  no climate zone leaves it unset and reported.
* Every filled value carries Provenance method ``construction_default`` whose
  note names the source, edition, table, climate zone, row and the words that
  matched. The value is the code maximum, not the drawn assembly, so the
  confidence stays below the review threshold's comfortable range.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from building_model import Provenance
from construction_library_data import (
    CONSTRUCTION_LIBRARY,
    EDITION,
    SOURCE,
    SOURCE_VERSION,
)
from construction_library_prm_data import PRM_LIBRARY
from construction_library_prm_data import SOURCE as PRM_SOURCE
from construction_library_prm_data import SOURCE_VERSION as PRM_SOURCE_VERSION

METHOD = "construction_default"
CONFIDENCE = 0.6  # a code-maximum baseline, not the assembly on the drawings
CATEGORIES = ("Nonresidential", "Residential", "Semiheated")

# Order matters: framing governs over cladding ("brick veneer on steel studs"
# is a steel-framed wall), so framing classes are tested before Mass. Two
# framing classes in one description is ambiguous and resolves to nothing.
_WALL_FRAMING: List[Tuple[str, re.Pattern]] = [
    (
        "Metal Building",
        re.compile(r"metal\s+building|pre-?engineered|insulated\s+metal\s+panel|\bimp\b"),
    ),
    (
        "SteelFramed",
        re.compile(r"(steel|metal|mtl\.?|light[- ]gau?ge|cold[- ]formed)\s+(stud|framing|framed)"),
    ),
    ("WoodFramed", re.compile(r"wood\s+(stud|framing|framed)|\b2\s*x\s*[468]\s+stud")),
]
_WALL_MASS = re.compile(
    r"\bcmu\b|concrete|masonry|\bbrick\b|\bblock\b|tilt[- ]?up|precast|\bmass\s+wall"
)
_ROOF: List[Tuple[str, re.Pattern]] = [
    (
        "IEAD",
        re.compile(
            r"\biead\b|(insulation|insul\.?)\s+(entirely\s+)?above\s+(the\s+)?deck"
            r"|rigid\s+(roof\s+)?insulation\s+(on|over)\s+(metal|steel|concrete)\s+deck"
        ),
    ),
    ("Metal Building", re.compile(r"metal\s+building|standing\s+seam")),
    ("Attic and Other", re.compile(r"\battic\b|wood\s+(joist|truss)")),
]


def climate_zone_number(cz: str) -> str:
    """'5A' -> '5', '7' -> '7'. Table 5.5 is split by zone number only."""
    m = re.fullmatch(r"\s*(?:cz\s*)?([0-8])\s*([abc])?\s*", (cz or "").lower())
    if not m:
        raise ValueError(f"not an ASHRAE climate zone: {cz!r} (expected 0-8 with A/B/C)")
    return m.group(1)


def classify(text: str, surface: str) -> Tuple[Optional[str], str]:
    """Construction class named by ``text`` for ``surface``, plus the reason.

    Returns ``(None, reason)`` when nothing or more than one class matches.
    """
    t = (text or "").lower()
    if surface == "ExteriorWall":
        hits = [(c, m.group(0)) for c, rx in _WALL_FRAMING if (m := rx.search(t))]
        if len(hits) > 1:
            return None, "ambiguous: " + ", ".join(f"{c} ({w!r})" for c, w in hits)
        if hits:
            return hits[0][0], f"matched {hits[0][1]!r}"
        m = _WALL_MASS.search(t)
        if m:
            return "Mass", f"matched {m.group(0)!r}"
        return None, "no wall construction class named"
    if surface == "ExteriorRoof":
        hits = [(c, m.group(0)) for c, rx in _ROOF if (m := rx.search(t))]
        if len(hits) > 1:
            return None, "ambiguous: " + ", ".join(f"{c} ({w!r})" for c, w in hits)
        if hits:
            return hits[0][0], f"matched {hits[0][1]!r}"
        return None, "no roof construction class named"
    return None, f"surface {surface} not resolved by this library yet"


@dataclass
class LibraryRow:
    category: str
    climate_zone: str
    surface: str
    construction_type: str
    source_construction: str
    u_ip: Optional[float]
    u_si: Optional[float]
    f_ip: Optional[float] = None  # slab F-factor, Btu/h-ft-F (GroundContactFloor rows)
    shgc: Optional[float] = None  # glazing rows only
    min_vt_shgc: Optional[float] = None  # vertical glazing rows only

    @property
    def table(self) -> str:
        return f"Table 5.5-{self.climate_zone}"


def lookup(
    surface: str, construction_type: str, climate_zone: str, category: str = "Nonresidential"
) -> Optional[LibraryRow]:
    cz = climate_zone_number(climate_zone)
    row = CONSTRUCTION_LIBRARY.get((category, cz, surface, construction_type))
    if row is None:
        return None
    return LibraryRow(
        category,
        cz,
        surface,
        construction_type,
        row["source_construction"],
        row["u_ip"],
        row["u_si"],
        row.get("f_ip"),
        row.get("shgc"),
        row.get("min_vt_shgc"),
    )


@dataclass
class LibrarySummary:
    climate_zone: str = ""
    category: str = ""
    resolved: Dict[str, dict] = field(default_factory=dict)  # construction id -> row info
    kept: List[str] = field(default_factory=list)  # ids with a stated U, untouched
    unmatched: Dict[str, str] = field(default_factory=dict)  # id -> reason
    not_run: str = ""  # why the library did not run at all
    # Appendix G baseline defaults (#768): construction id -> row info, for
    # walls/roof the drawings name no assembly for at all
    defaulted: Dict[str, dict] = field(default_factory=dict)
    # Appendix G baseline envelope (#781): Table G3.4 (PRM 2019) values for
    # the baseline model; recorded only, the exported proposed model is not
    # changed by it
    baseline: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source": f"{SOURCE} ({SOURCE_VERSION})",
            "climate_zone": self.climate_zone,
            "building_category": self.category,
            "not_run": self.not_run,
            "resolved": self.resolved,
            "kept_stated_values": sorted(self.kept),
            "unmatched": self.unmatched,
            "defaulted": self.defaulted,
            "baseline": self.baseline,
        }


def _prov(row: LibraryRow, cid: str, why: str, given_cz: str) -> Provenance:
    return Provenance(
        sheet_id="",
        revision=0,
        method=METHOD,
        confidence=CONFIDENCE,
        note=(
            f"{SOURCE}, {EDITION} {row.table} ({SOURCE_VERSION}); {row.category} "
            f"{row.surface} {row.construction_type}, climate zone {given_cz}; "
            f"U {row.u_ip} Btu/h-ft2-F; {cid}: {why}; code maximum, not the drawn assembly"
        ),
    )


def apply_construction_library(
    model, climate_zone: str = "", category: str = "Nonresidential"
) -> LibrarySummary:
    """Fill unset construction U-values from Table 5.5; never overwrite.

    With no climate zone the library does not run (a zone is never assumed)
    and every unset construction is reported. Space wall U-values are rolled
    up again afterwards so the exports see the filled values.
    """
    from constructions import apply_wall_u_rollup

    if category not in CATEGORIES:
        raise ValueError(f"building category must be one of {CATEGORIES}, got {category!r}")
    s = LibrarySummary(climate_zone=climate_zone or "", category=category)
    if climate_zone:
        climate_zone_number(climate_zone)  # validate early
        model.climate_zone = climate_zone
        model.building_category = category
    else:
        s.not_run = "no climate zone given"
    roof_id = getattr(model, "roof_construction_id", "") or ""
    slab_id = getattr(model, "slab_construction_id", "") or ""
    for cid, c in sorted((getattr(model, "constructions", None) or {}).items()):
        if c.u_value_w_m2k is not None and c.u_value_w_m2k > 0:
            s.kept.append(cid)
            continue
        if cid == slab_id:
            s.unmatched[cid] = "slab: Table 5.5 gives an F-factor, not resolved yet"
            continue
        if not climate_zone:
            s.unmatched[cid] = "no climate zone given"
            continue
        surface = "ExteriorRoof" if cid == roof_id else "ExteriorWall"
        ctype, why = classify(f"{cid} {c.name}", surface)
        if ctype is None:
            s.unmatched[cid] = why
            continue
        row = lookup(surface, ctype, climate_zone, category)
        if row is None or row.u_si is None:
            s.unmatched[cid] = f"no Table 5.5 U-value for {category} {surface} {ctype}"
            continue
        c.u_value_w_m2k = row.u_si
        c.provenance = _prov(row, cid, why, climate_zone)
        s.resolved[cid] = {
            "surface": surface,
            "construction_type": ctype,
            "table": row.table,
            "u_ip": row.u_ip,
            "u_si": row.u_si,
            "why": why,
        }
    if climate_zone:
        _apply_baseline_defaults(model, s, climate_zone, category)
        s.baseline = baseline_envelope(model, climate_zone, category)
    if s.resolved or s.defaulted:
        apply_wall_u_rollup(model)
    return s


# Unlabeled envelope (#781, Alex's order: drawings first, Table 5.5 for
# anything the drawings leave unlabeled, Table G3.4 for the Appendix G
# baseline). Walls with no construction and an unset roof or slab get the
# Table 5.5 code maximum. Table 5.5 needs a construction class and the
# drawings name none, so the class is the one Appendix G uses for its
# baseline (G3.1-5(b): steel-framed walls, insulation entirely above deck).
DEFAULT_WALL_ID = "t55-wall"
DEFAULT_ROOF_ID = "t55-roof"
DEFAULT_SLAB_ID = "t55-slab"
_BASELINE = {
    DEFAULT_WALL_ID: (
        "ExteriorWall",
        "SteelFramed",
        "Exterior wall, Table 5.5 code maximum (unlabeled)",
    ),
    DEFAULT_ROOF_ID: ("ExteriorRoof", "IEAD", "Roof, Table 5.5 code maximum (unlabeled)"),
}
_UNLABELED_WHY = "no assembly stated on the drawings; Table 5.5 code maximum, class {ctype}"


def _apply_baseline_defaults(model, s: "LibrarySummary", climate_zone: str, category: str):
    """Default walls with no construction and an unset roof to Table 5.5 (#768, #781).

    A segment that already points at a construction (resolved or not) and a
    stated roof are left alone. An unset slab gets the unheated-slab F-factor
    as an effective U (``_default_slab``).
    """
    from building_model import Construction

    walls = [w for w in model.envelope if not (getattr(w, "construction_id", "") or "")]
    targets = []
    if walls:
        targets.append(DEFAULT_WALL_ID)
    if not (getattr(model, "roof_construction_id", "") or ""):
        targets.append(DEFAULT_ROOF_ID)
    for cid in targets:
        surface, ctype, name = _BASELINE[cid]
        row = lookup(surface, ctype, climate_zone, category)
        if row is None or row.u_si is None:
            s.unmatched[cid] = f"no Table 5.5 U-value for {category} {surface} {ctype}"
            continue
        why = _UNLABELED_WHY.format(ctype=ctype)
        model.constructions[cid] = Construction(
            id=cid, name=name, u_value_w_m2k=row.u_si, provenance=_prov(row, cid, why, climate_zone)
        )
        info = {
            "surface": surface,
            "construction_type": ctype,
            "table": row.table,
            "u_ip": row.u_ip,
            "u_si": row.u_si,
            "why": why,
        }
        if cid == DEFAULT_WALL_ID:
            for w in walls:
                w.construction_id = cid
            info["segments"] = sorted(w.id for w in walls)
        else:
            model.roof_construction_id = cid
        s.defaulted[cid] = info
    if not (getattr(model, "slab_construction_id", "") or ""):
        _default_slab(model, s, climate_zone, category)
    _default_openings(model, s, climate_zone, category)


DEFAULT_WINDOW_ID = "t55-window"
DEFAULT_DOOR_ID = "t55-door"
DEFAULT_SKYLIGHT_ID = "t55-skylight"
# category -> (construction id, Table 5.5 surface, class, name, why the class).
# Drawings rarely say whether a window opens or whether a door is glazed, so
# each gets one class and says which: fixed windows (the more common class in
# nonresidential glazing), opaque swinging doors, and curbed glass skylights
# (all three skylight classes carry the same values in 90.1-2019).
# Table 5.5 sets no VT for skylights. The 90.1 PRM 2019 data in
# openstudio-standards v0.8.6 (ashrae_90_1_prm_2019.construction_properties.json)
# gives every glazing row VT = round(1.10 x SHGC, 2): all 132 ExteriorWindow,
# 132 GlassDoor and 66 Skylight rows. Skylights use that ratio rather than
# openstudio-standards' name-parsing fallback of VT 0.81, which is a placeholder
# (VT/SHGC 2.0 against a 0.40 SHGC) and not tied to any table (#784).
SKYLIGHT_VT_SHGC = 1.10
SKYLIGHT_VT_SHGC_SOURCE = (
    "VT/SHGC 1.10 as in openstudio-standards v0.8.6 "
    "ashrae_90_1_prm_2019.construction_properties.json skylight rows"
)


_OPENING_DEFAULTS = {
    "window": (
        DEFAULT_WINDOW_ID,
        "ExteriorWindow",
        "Fixed",
        "Window, Table 5.5 code maximum (unlabeled)",
        "fixed vs operable not stated; fixed class used",
    ),
    "door": (
        DEFAULT_DOOR_ID,
        "ExteriorDoor",
        "Swinging",
        "Exterior door, Table 5.5 code maximum (unlabeled)",
        "door type not stated; opaque swinging class used",
    ),
    "skylight": (
        DEFAULT_SKYLIGHT_ID,
        "Skylight",
        "Glass with Curb",
        "Skylight, Table 5.5 code maximum (unlabeled)",
        "skylight type not stated; all Table 5.5 skylight classes share these values",
    ),
}


def _default_openings(model, s: "LibrarySummary", climate_zone: str, category: str):
    """Give exterior windows, doors and skylights with no assembly Table 5.5 values (#747).

    Interior openings (an ``adjacent_space_id``) and openings that already
    point at a construction are left alone. Windows and skylights carry the
    row's SHGC as well as its U.
    """
    from building_model import Construction

    by_cat: Dict[str, list] = {}
    for sp in (model.spaces or {}).values():
        for op in getattr(sp, "openings", None) or []:
            if getattr(op, "adjacent_space_id", None) or (getattr(op, "construction_id", "") or ""):
                continue
            if op.category in _OPENING_DEFAULTS:
                by_cat.setdefault(op.category, []).append(op)
    for cat, ops in sorted(by_cat.items()):
        cid, surface, ctype, name, why_class = _OPENING_DEFAULTS[cat]
        row = lookup(surface, ctype, climate_zone, category)
        if row is None or row.u_si is None:
            s.unmatched[cid] = f"no Table 5.5 U-value for {category} {surface} {ctype}"
            continue
        why = f"no assembly stated on the drawings; Table 5.5 code maximum, {why_class}"
        # Table 5.5 caps SHGC and sets a minimum VT/SHGC for vertical glazing;
        # VT is taken at that minimum. Skylight rows carry no VT figure, so
        # skylights take the same 1.10 ratio the 90.1 PRM 2019 data applies to
        # every glazing row, skylights included (#784).
        vt = None
        if row.shgc is not None and row.min_vt_shgc is not None:
            vt = round(row.shgc * row.min_vt_shgc, 4)
            why += f"; VT {vt} = minimum VT/SHGC {row.min_vt_shgc} x SHGC {row.shgc}"
        elif row.shgc is not None and surface == "Skylight":
            vt = round(row.shgc * SKYLIGHT_VT_SHGC, 4)
            why += f"; VT {vt} = {SKYLIGHT_VT_SHGC} x SHGC {row.shgc} ({SKYLIGHT_VT_SHGC_SOURCE})"
        model.constructions[cid] = Construction(
            id=cid,
            name=name,
            u_value_w_m2k=row.u_si,
            provenance=_prov(row, cid, why, climate_zone),
            shgc=row.shgc,
            vt=vt,
        )
        for op in ops:
            op.construction_id = cid
        s.defaulted[cid] = {
            "surface": surface,
            "construction_type": ctype,
            "table": row.table,
            "u_ip": row.u_ip,
            "u_si": row.u_si,
            "shgc": row.shgc,
            "vt": vt,
            "why": why,
            "openings": sorted(op.id for op in ops),
        }


# 1 Btu/h-ft-F = 1.730735 W/m-K (F-factor, heat loss per length of exposed slab edge)
F_IP_TO_SI = 1.730735


# Slab parts smaller than this are drawing noise (slivers between rooms), not a
# wing on grade; they are listed in the report, not counted.
MIN_SLAB_PART_M2 = 1.0
# A hole in the ground footprint is an open-air courtyard when exterior walls
# run along at least this share of its edge (#747). Otherwise it is an
# unmodelled room or shaft: still slab, and its edge is not exposed.
COURTYARD_WALL_SHARE = 0.5
# exterior wall centreline within this distance of the hole edge (m), and
# within this angle of it (degrees), counts as running along it
_COURTYARD_TOL_M = 0.6
_COURTYARD_ANGLE_DEG = 10.0
# a courtyard is open to the sky: spaces on higher levels may cover at most
# this share of it (balconies, a bridge); more means floor above a missing room
COURTYARD_MAX_COVERED_ABOVE = 0.10


def _slab_level_walls(model, level_ids: set) -> list:
    """Exterior wall centrelines ``[(p0, p1)]`` on the slab's level.

    IFC and drawing-set envelope ids are level-prefixed (``<level>-EW1``,
    ``<level>-E1``); when any wall carries a known level prefix, only walls on
    ``level_ids`` count, so an upper-floor wall over a ground-floor hole edge
    is not evidence. Unprefixed envelopes (a single-level model) use every
    wall.
    """
    known = {lv.id for lv in (getattr(model, "levels", None) or [])}
    rows = [
        w
        for w in (getattr(model, "envelope", None) or [])
        if len(w.from_m or []) >= 2 and len(w.to_m or []) >= 2 and w.from_m[:2] != w.to_m[:2]
    ]

    def level_of(w):
        for lid in known:
            if str(w.id).startswith(f"{lid}-"):
                return lid
        return None

    if any(level_of(w) for w in rows):
        rows = [w for w in rows if level_of(w) in level_ids]
    return [(tuple(w.from_m[:2]), tuple(w.to_m[:2])) for w in rows]


def _wall_cover(ring, walls) -> float:
    """Length of ``ring`` (a closed shapely ring) that exterior walls run along.

    A wall runs along an edge when its centreline is within
    ``_COURTYARD_TOL_M`` of the edge and within ``_COURTYARD_ANGLE_DEG`` of
    parallel; only the stretch of the edge the wall projects onto counts, and
    overlapping walls are counted once.
    """
    import math

    from shapely.geometry import LineString, Point

    total = 0.0
    coords = list(ring.coords)
    for a, b in zip(coords, coords[1:]):
        edge = LineString([a, b])
        if edge.length <= 0:
            continue
        ea = math.atan2(b[1] - a[1], b[0] - a[0])
        spans = []
        for p0, p1 in walls:
            wa = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
            d = abs((wa - ea + math.pi / 2) % math.pi - math.pi / 2)
            if math.degrees(d) > _COURTYARD_ANGLE_DEG:
                continue
            if LineString([p0, p1]).distance(edge) > _COURTYARD_TOL_M:
                continue
            s0, s1 = sorted((edge.project(Point(p0)), edge.project(Point(p1))))
            if s1 > s0:
                spans.append((s0, s1))
        spans.sort()
        cur0 = cur1 = None
        for s0, s1 in spans:
            if cur1 is None or s0 > cur1:
                if cur1 is not None:
                    total += cur1 - cur0
                cur0, cur1 = s0, s1
            else:
                cur1 = max(cur1, s1)
        if cur1 is not None:
            total += cur1 - cur0
    return total


def _slab_geometry(model, lowest: bool = True):
    """Slab on grade geometry of the ground (or, ``lowest=False``, top) level.

    Returns ``(area_m2, exposed_perimeter_m, level_id, detail)`` or None when
    the model has no space polygons. The slab is the union of the level's
    space polygons, every part of it (#747): a separate wing is slab on grade
    too, so its area and its whole edge count; parts under
    ``MIN_SLAB_PART_M2`` are slivers and are only listed. A hole in the union
    is an open-air courtyard when exterior walls on this level run along at
    least ``COURTYARD_WALL_SHARE`` of its edge and spaces on higher levels
    cover at most ``COURTYARD_MAX_COVERED_ABOVE`` of it (open to the sky):
    its edge is exposed and its area is not slab. Any other hole (an
    unmodelled room or shaft) stays slab and its edge is not exposed, which is
    what the exported footprint (holes dropped) already assumes. ``detail``
    lists wings, courtyards and holes for the report.
    """
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    spaces = [sp for sp in (model.spaces or {}).values() if len(sp.polygon_m or []) >= 3]
    if not spaces:
        return None
    elev = {lv.id: lv.elevation_z_m for lv in (getattr(model, "levels", None) or [])}
    pick = min if lowest else max
    low = pick(elev.get(sp.level_id, 0.0) for sp in spaces)
    ground = [sp for sp in spaces if abs(elev.get(sp.level_id, 0.0) - low) < 1e-6]
    polys = []
    for sp in ground:
        g = Polygon([tuple(v[:2]) for v in sp.polygon_m])
        if not g.is_valid:
            g = g.buffer(0)
        if not g.is_empty and g.area > 0:
            polys.append(g)
    if not polys:
        return None
    merged = unary_union(polys)
    parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    parts = [g for g in parts if g.geom_type == "Polygon" and g.area > 0]
    walls = _slab_level_walls(model, {sp.level_id for sp in ground})
    # spaces on higher levels: a courtyard is open to the sky, so a hole with
    # floor above it is a missing room, not a courtyard
    above = []
    for sp in spaces:
        if elev.get(sp.level_id, 0.0) > low + 1e-6:
            g = Polygon([tuple(v[:2]) for v in sp.polygon_m])
            if not g.is_valid:
                g = g.buffer(0)
            if not g.is_empty and g.area > 0:
                above.append(g)
    above_union = unary_union(above) if above else None
    detail = {"wings": 0, "slivers": [], "courtyards": [], "holes_not_exposed": []}
    area = perim = 0.0
    for g in sorted(parts, key=lambda q: -q.area):
        if g.area < MIN_SLAB_PART_M2:
            detail["slivers"].append(round(g.area, 4))
            continue
        detail["wings"] += 1
        area += Polygon(g.exterior).area
        perim += g.exterior.length
        for hole in g.interiors:
            h_area = Polygon(hole).area
            h_len = hole.length
            cover = _wall_cover(hole, walls) if walls else 0.0
            covered_above = 0.0
            if above_union is not None and h_area > 0:
                covered_above = Polygon(hole).intersection(above_union).area / h_area
            rec = {
                "area_m2": round(h_area, 4),
                "perimeter_m": round(h_len, 4),
                "wall_cover_m": round(cover, 4),
            }
            if covered_above > 0:
                rec["covered_above_share"] = round(covered_above, 4)
            if (
                h_len > 0
                and cover / h_len >= COURTYARD_WALL_SHARE
                and covered_above <= COURTYARD_MAX_COVERED_ABOVE
            ):
                area -= h_area
                perim += h_len
                detail["courtyards"].append(rec)
            else:
                detail["holes_not_exposed"].append(rec)
    if area <= 0 or perim <= 0:
        return None
    return area, perim, ground[0].level_id, detail


# 90.1-2019 Section 3.2, heated slab: "slab-on-grade floor construction in
# which the heating elements, hydronic tubing, or hot-air distribution system
# is in contact with, or placed within or under, the slab." On a drawing set
# that shows up as a radiant floor / in-slab heating row on a mechanical
# schedule. Snow-melt and sidewalk heating are outdoors, and radiant ceiling
# panels heat the room, not the slab, so neither counts.
HEATED_SLAB_RX = re.compile(
    r"\bRADIANT\s+(FLOOR|SLAB)|\bIN[- ]?(FLOOR|SLAB)\s+(RADIANT|HEAT)"
    r"|\bHEATED\s+(SLAB|FLOOR)|\b(UNDER)?FLOOR\s+HEATING\b|\bSLAB\s+HEATING\b"
)
NOT_HEATED_SLAB_RX = re.compile(r"\bSNOW\s*-?\s*MELT|\bSIDEWALK|\bDRIVEWAY|\bCEILING")
# a radiant slab that only cools is not a heated slab
_COOLING_RX = re.compile(r"\bCOOLING\b")
_HEAT_RX = re.compile(r"\bHEAT")


def heated_slab_evidence(equipment) -> str:
    """Scheduled equipment that heats the slab -> one line naming it, or "".

    ``equipment`` is the mechanical-schedule record list from a drawing-set
    run (``tag``, ``description``, ``schedule``, ``sheet``, ``values``). A
    row counts when its description, schedule title or any cell names
    radiant floor / in-slab heating and nothing on it says snow melt,
    sidewalk, driveway or ceiling. A row that says cooling and never heat
    is a radiant cooling slab, not a heated one.
    """
    hits = []
    for e in equipment or []:
        text = " ".join(
            [str(e.get("description") or ""), str(e.get("schedule") or "")]
            + [str(v) for v in (e.get("values") or {}).values()]
        ).upper()
        if not HEATED_SLAB_RX.search(text) or NOT_HEATED_SLAB_RX.search(text):
            continue
        if _COOLING_RX.search(text) and not _HEAT_RX.search(text):
            continue
        where = f" on {e['sheet']}" if e.get("sheet") else ""
        hits.append(f"{e.get('tag', '?')}{where}")
    if not hits:
        return ""
    return "radiant floor / in-slab heating scheduled: " + ", ".join(sorted(set(hits)))


def _default_slab(model, s: "LibrarySummary", climate_zone: str, category: str):
    """Unset slab -> Table 5.5 slab F-factor as an effective U (#747, #781).

    The slab is Unheated unless ``model.slab_heated_by`` says what heats it,
    then the Table 5.5 Heated row is used. Heating anywhere in the slab makes
    the whole slab Heated here (the footprint is one slab), which can only
    raise the loss; the drawing set run sends that to review.

    gbXML carries a U-value, not
    an F-factor, so the slab gets the U that loses the same heat:
    U = F x exposed perimeter / slab area (F-factor definition, heat loss per
    unit length of exposed edge per degree indoor-outdoor). Perimeter and area
    are the whole ground slab: every wing, open-air courtyard edges included
    (``_slab_geometry``).
    """
    from building_model import Construction

    cid = DEFAULT_SLAB_ID
    heated_by = (getattr(model, "slab_heated_by", "") or "").strip()
    ctype = "Heated" if heated_by else "Unheated"
    label = ctype.lower()
    row = lookup("GroundContactFloor", ctype, climate_zone, category)
    if row is None or row.f_ip is None:
        s.unmatched[cid] = f"no Table 5.5 F-factor for {category} {label} slab"
        return
    geo = _slab_geometry(model)
    if geo is None:
        s.unmatched[cid] = "slab: no ground-floor space polygons for perimeter and area"
        return
    area, perim, level_id, detail = geo
    f_si = row.f_ip * F_IP_TO_SI
    u = round(f_si * perim / area, 6)
    why = (
        "no slab assembly stated on the drawings; "
        + (f"heated slab ({heated_by}); " if heated_by else "")
        + f"Table 5.5 {label} slab F-factor "
        f"{row.f_ip} Btu/h-ft-F = {f_si:.4f} W/m-K; effective U = F x exposed perimeter "
        f"{perim:.2f} m / slab area {area:.2f} m2 (ground slab of level {level_id}: "
        f"{detail['wings']} wing(s), {len(detail['courtyards'])} courtyard(s))"
    )
    prov = Provenance(
        sheet_id="",
        revision=0,
        method=METHOD,
        confidence=CONFIDENCE,
        note=(
            f"{SOURCE}, {EDITION} {row.table} ({SOURCE_VERSION}); {row.category} "
            f"GroundContactFloor {ctype}, climate zone {climate_zone}; {cid}: {why}; "
            "code maximum, not the drawn assembly"
        ),
    )
    model.constructions[cid] = Construction(
        id=cid,
        name=(
            "Heated slab on grade, Table 5.5 code maximum (unlabeled)"
            if heated_by
            else "Slab on grade, Table 5.5 code maximum (unlabeled)"
        ),
        u_value_w_m2k=u,
        provenance=prov,
    )
    model.slab_construction_id = cid
    s.defaulted[cid] = {
        "surface": "GroundContactFloor",
        "construction_type": ctype,
        "table": row.table,
        "f_ip": row.f_ip,
        "f_si": round(f_si, 6),
        "exposed_perimeter_m": round(perim, 4),
        "area_m2": round(area, 4),
        "u_si": u,
        "why": why,
        "wings": detail["wings"],
        "courtyards": detail["courtyards"],
        "holes_not_exposed": detail["holes_not_exposed"],
        "slivers_m2": detail["slivers"],
    }
    if heated_by:
        s.defaulted[cid]["heated_by"] = heated_by


# Appendix G baseline envelope (#781): 90.1-2019 Table G3.4 (PRM 2019).
# Name -> (surface, construction type) as the PRM table keys them.
_PRM_SURFACES = (
    ("wall", "ExteriorWall", "SteelFramed"),
    ("roof", "ExteriorRoof", "IEAD"),
    ("slab", "GroundContactFloor", "Unheated"),
    ("exterior_floor", "ExteriorFloor", "SteelFramed"),
    ("door_swinging", "ExteriorDoor", "Swinging"),
    ("door_nonswinging", "ExteriorDoor", "NonSwinging"),
    ("vertical_glazing", "ExteriorWindow", "Any Vertical Glazing"),
    ("glass_door", "GlassDoor", "Any Vertical Glazing"),
    ("skylight", "Skylight", "Any Skylight"),
)


def prm_rows(surface: str, construction_type: str, climate_zone: str, category: str):
    """(rows, zone key) from Table G3.4; zone 3 is split 3A/3B/3C there."""
    z = (climate_zone or "").strip().upper().removeprefix("CZ").strip()
    num = climate_zone_number(climate_zone)
    for zk in (z, num):
        rows = PRM_LIBRARY.get((category, zk, surface, construction_type))
        if rows:
            return rows, zk
    return None, None


def _pick_band(rows: list, pct: Optional[float]):
    """(row, note) for a percent of surface; above the top band uses the top
    band (G3.1-5(c) caps the baseline window-to-wall ratio at 40%)."""
    if len(rows) == 1 and rows[0]["pct_min"] is None:
        return rows[0], ""
    if pct is None:
        return None, "percent of surface unknown; band not chosen"
    for r in rows:
        hi = 100.0 if r["pct_max"] is None else r["pct_max"]
        if pct <= hi:
            return r, ""
    return rows[-1], f"{pct:.1f}% is above the top band; top band used (G3.1-5(c) cap)"


def _glazing_ratios(model):
    """(window-to-wall %, skylight-to-roof %) from the model, None if unknown."""
    win = sky = 0.0
    for sp in (model.spaces or {}).values():
        for o in sp.openings or []:
            if getattr(o, "adjacent_space_id", None):
                continue
            a = o.area_m2 if o.area_m2 else (o.width_m or 0.0) * (o.height_m or 0.0)
            if o.category == "window":
                win += a
            elif o.category == "skylight":
                sky += a
    wall = 0.0
    for w in model.envelope or []:
        if w.area_m2:
            wall += w.area_m2
        elif w.length_m and w.height_m:
            wall += w.length_m * w.height_m
    wwr = round(100.0 * win / wall, 2) if wall > 0 else None
    top = _slab_geometry(model, lowest=False)
    srr = round(100.0 * sky / top[0], 2) if top else None
    return wwr, srr


def baseline_envelope(model, climate_zone: str, category: str) -> dict:
    """Appendix G baseline envelope values for this model (Table G3.4).

    Recorded for the baseline model; it does not change the proposed model.
    Glazing picks its band from the model's own window-to-wall and
    skylight-to-roof ratios. The slab also gets its effective U on the
    model's ground slab (F x exposed perimeter / area, ``_slab_geometry``).
    """
    wwr, srr = _glazing_ratios(model)
    out: dict = {
        "source": f"{PRM_SOURCE} ({PRM_SOURCE_VERSION})",
        "climate_zone": climate_zone,
        "building_category": category,
        "window_to_wall_pct": wwr,
        "skylight_to_roof_pct": srr,
        "surfaces": {},
        "unmatched": {},
    }
    for name, surface, ctype in _PRM_SURFACES:
        rows, zk = prm_rows(surface, ctype, climate_zone, category)
        if not rows:
            out["unmatched"][name] = f"no Table G3.4 row for {category} {surface} {ctype}"
            continue
        pct = srr if surface == "Skylight" else wwr
        row, note = _pick_band(rows, pct)
        if row is None:
            out["unmatched"][name] = note
            continue
        info = {
            "surface": surface,
            "construction_type": ctype,
            "table_zone": zk,
            "source_construction": row["source_construction"],
            "u_ip": row["u_ip"],
            "u_si": row["u_si"],
            "f_ip": row["f_ip"],
            "shgc": row["shgc"],
            "vt": row["vt"],
            "band_pct": [row["pct_min"], row["pct_max"]],
        }
        if note:
            info["note"] = note
        if name == "slab" and row["f_ip"] is not None:
            geo = _slab_geometry(model)
            if geo is not None:
                area, perim, _, _ = geo
                info["u_si_effective"] = round(row["f_ip"] * F_IP_TO_SI * perim / area, 6)
        out["surfaces"][name] = info
    return out
