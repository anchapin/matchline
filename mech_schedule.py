"""Mechanical schedule parsing and system-type classification (#974).

Parses a real mechanical schedule into typed rows, classifies a system type
per row with provenance, routes low-confidence rows to the review queue
(never silently accepted), and emits first-class ``MechSystem`` entities
linked to rooms. The follow-on MEP slice consumes ``MechSystem`` for
air-loop building (#746: each scheduled AHU/RTU becomes one gbXML AirLoop).

Source dataset: buildingSMART's Medical-Dental Clinic design workbook
(``2013-10-09-Clinic-Design-v14-WORKING-01.xlsx``, COBie 2.4 Spreadsheet
format), from https://github.com/buildingsmart-community/
Community-Sample-Test-Files, licensed CC BY 4.0 with attribution
``BSI (2020) "Medical-Dental Test Files," buildingSMART International``.
The clinic drawing set carries no schedule PDFs; the COBie workbook is the
project's real schedule deliverable. ``Component`` rows carry the equipment
(tag, type, service area), ``Type`` rows the category and description
(capacities as stated there), ``Space`` rows the room list. The workbook
path is passed in; like every dataset it is never committed.

Parsing rules carried from the ledger decisions:
- capacities are recorded as stated (raw string plus parsed value and
  unit), never converted or guessed; a range such as ``633-703 kW`` keeps
  the raw string with ``value=None`` because the schedule states an
  interval, not a number;
- classification is multi-signal. The workbook's OmniClass category column
  is unreliable (``Duplex Receptacle`` sits under a ``23-`` HVAC
  category), so a row's system type comes from agreement between the tag
  pattern parsed out of the component name and the type-name/description
  text; conflicting evidence is low confidence;
- components with no positive air-system evidence (no known tag prefix,
  no type or description text hit; receptacles, doors, sprinklers, ...)
  are excluded from the schedule and counted in the result stats, not
  parsed as rows;
- every low-confidence row gets a ``ReviewItem`` (kind
  ``"mech_schedule"``) and keeps ``system_type=None`` until confirmed;
- stated values resolve to slots (``MechValue``: cooling/heating
  capacity, airflow with min/max, water flow, head, power) with cell
  provenance and confidence; an ambiguous family/unit combination is
  never mapped, it becomes a review item (kind
  ``"mech_schedule_value"``);
- zone-load-impacting inputs (the airflow slots) are recorded inputs
  only: nothing here sizes equipment, and any downstream HVAC sizing
  consumes zone loads first.

Typical use::

    result = parse_cobie_workbook("/path/clinic-design.xlsx")
    matched = link_to_model(result.systems, model)  # rooms on the model
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from building_model import ReviewItem

SCHEMA = "matchline.mech_schedule/1"

# Tag prefixes parsed out of component names, mapped to system types.
# Evidence: the clinic workbook's component-name families ("AC Unit Type 1
# AC-1", "ACCU-1", "AHU-1", "Fan- EF1-1").
TAG_PREFIX_TYPES: Dict[str, str] = {
    "AC": "fan_coil",
    "ACCU": "condensing_unit",
    "ACC": "condensing_unit",
    "AHU": "ahu",
    "RTU": "rtu",
    "FCU": "fan_coil",
    "EF": "exhaust_fan",
    "ERV": "erv",
    "VAV": "vav_box",
    "HP": "heat_pump",
    "P": "pump",
    "B": "boiler",
    "CH": "chiller",
}

# Text evidence rules, most specific first, applied to the type name, the
# type description and the component description. Each hit names one
# system type; the first hit wins.
TEXT_RULES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("fan_coil", re.compile(r"fan\s*coil", re.I)),
    ("chiller", re.compile(r"chiller", re.I)),
    ("boiler", re.compile(r"boiler", re.I)),
    ("heat_pump", re.compile(r"heat\s*pump", re.I)),
    ("ahu", re.compile(r"air\s*handl|\bAHU\b", re.I)),
    ("rtu", re.compile(r"\bRTU\b|rooftop", re.I)),
    ("vav_box", re.compile(r"\bVAV\b", re.I)),
    ("supply_diffuser", re.compile(r"supply\s*diffuser", re.I)),
    ("return_diffuser", re.compile(r"return\s*diffuser", re.I)),
    ("exhaust_fan", re.compile(r"exhaust", re.I)),
    ("erv", re.compile(r"energy\s*recovery|\bERV\b", re.I)),
    ("condensing_unit", re.compile(r"condens(?:ing|er)\s*unit", re.I)),
    ("heating_coil", re.compile(r"heat(?:ing)?\s*coil", re.I)),
    ("cooling_coil", re.compile(r"cool(?:ing)?\s*coil", re.I)),
    ("coil", re.compile(r"\bcoil\b", re.I)),
    ("fan", re.compile(r"\bfan\b", re.I)),
    ("pump", re.compile(r"\bpump\b", re.I)),
    ("terminal", re.compile(r"diffuser|grille|grill|louver", re.I)),
]

# System types that become first-class MechSystem entities. Air terminals
# (diffusers) stay schedule rows; the drawing path links them to rooms via
# hvac_trace, not via schedule entities.
SYSTEM_CLASSES = {
    "ahu",
    "rtu",
    "fan_coil",
    "condensing_unit",
    "vav_box",
    "fan",
    "exhaust_fan",
    "erv",
    "heat_pump",
    "chiller",
    "boiler",
    "pump",
}

# HVAC capacity units accepted by the capacity parser. Anything else
# (dimensions like "600 x 600" or "200 mm") is not a capacity and is
# skipped. Units are recorded as stated, never converted.
CAPACITY_UNITS = {
    "kw": "kW",
    "w": "W",
    "j": "J",
    "kj": "kJ",
    "mj": "MJ",
    "btu": "BTU",
    "btu/h": "BTU/h",
    "mbh": "MBH",
    "ton": "ton",
    "tons": "ton",
    "tr": "ton",
    "cfm": "CFM",
    "l/s": "L/s",
    "gpm": "GPM",
}

_TAG_RE = re.compile(r"\b([A-Z][A-Z0-9]{0,5})-(\d+[A-Za-z]?)\b")
_NUMBER = r"\d[\d,]*(?:\.\d+)?"
_CAP_RE = re.compile(rf"({_NUMBER})(?:\s*-\s*({_NUMBER}))?\s*([A-Za-z/]+)")
_NOT_A_UNIT = {"x", "mm", "cm", "m", "ft", "in"}

# ---------------------------------------------------------------------------
# Per-value schedule slots (#1236)
# ---------------------------------------------------------------------------

# The value slots a mechanical schedule row can state. ``MechValue.field``
# comes from this set and nothing else.
VALUE_SLOTS = frozenset(
    {
        "cooling_capacity",
        "heating_capacity",
        "airflow",
        "airflow_min",
        "airflow_max",
        "outside_air_flow",
        "water_flow",
        "head",
        "power",
    }
)

# Slots whose values feed zone loads downstream. Recorded inputs only:
# nothing here sizes equipment. Any downstream HVAC sizing consumes zone
# loads first (ledger decision #1236); this module records the stated
# inputs and flags them, it never computes or sizes a load.
ZONE_LOAD_SLOTS = frozenset({"airflow", "airflow_min", "airflow_max", "outside_air_flow"})

# Numeric confidence for a stated value, by the qualitative confidence the
# value carries (#1295). The labels above ("high": explicit qualifier or
# unambiguous unit, "medium": family or IP default, "low": nulled by a
# same-slot conflict) are the reader's evidence grades; these are the
# numbers the rest of the model records and the schedule-truth benchmark
# calibrates them against (see docs/bench_schedule_confidence.md).
#
# * high -> 0.9: the unit names the slot on its own or an explicit
#   qualifier does, so the value is read, not guessed.
# * medium -> 0.8: the slot came from a family or IP default, one step
#   weaker than an explicit statement. It matches the confidence the
#   equipment model records for a schedule row it read as stated
#   (``equipment.SCHEDULE_EQUIPMENT_CONFIDENCE``).
# * low -> 0.4: the value was nulled by a same-slot conflict, so nothing
#   is asserted; the number is the review signal, and it matches the
#   confidence the conflict review item already carries.
VALUE_CONFIDENCE = {"high": 0.9, "medium": 0.8, "low": 0.4}

# The slots whose values the #1240 schedule truth set can score: the
# workbook states airflow requirements (L/s) on its Attribute sheet and
# capacities on its Type sheet.
TRUTH_SCORABLE_SLOTS = frozenset(
    {
        "cooling_capacity",
        "heating_capacity",
        "airflow",
        "airflow_min",
        "airflow_max",
        "outside_air_flow",
        "water_flow",
        "power",
    }
)

# Units that name their slot unambiguously, whatever the equipment family.
_AIRFLOW_UNITS = {"cfm": "CFM", "l/s": "L/s"}
_WATER_FLOW_UNITS = {"gpm": "GPM"}
_POWER_UNITS = {"kw": "kW", "w": "W"}
_HEAD_UNITS = {"ft": "ft", "m": "m", "kpa": "kPa", "psi": "psi"}

# Bare thermal units (no cooling/heating qualifier) map through family
# defaults, at medium confidence. kW and W are deliberately absent: on a
# thermal family an unqualified kW is ambiguous between electrical power
# and thermal capacity, so it is never mapped.
_THERMAL_FAMILY_SLOTS = {
    "chiller": "cooling_capacity",
    "boiler": "heating_capacity",
    "cooling_coil": "cooling_capacity",
    "heating_coil": "heating_capacity",
}

# Families whose bare thermal capacity cannot be read without a qualifier:
# a heat pump or an AHU states both heating and cooling, a generic coil
# states either, so mapping the unit would fabricate a fact. Those values
# are not recorded; each becomes a review item instead.
_AMBIGUOUS_THERMAL_FAMILIES = frozenset(
    {"coil", "heat_pump", "ahu", "fan_coil", "condensing_unit", "erv", "rtu"}
)

# Families where an unqualified kW/W is electrical power, not a capacity.
_POWER_FAMILIES = frozenset({"pump", "fan", "exhaust_fan", "vav_box", "ahu", "erv"})

# Bare-number IP defaults, applied only when the same text states sizes in
# both feet and inches (the schedule is IP-native), at medium confidence.
_BARE_NUMBER_DEFAULTS = {
    "exhaust_fan": ("airflow", "CFM"),
    "pump": ("water_flow", "GPM"),
}

# Qualifier words, priority order, searched in the text before a number.
_QUALIFIER_RES: List[Tuple[str, "re.Pattern[str]"]] = [
    ("cooling", re.compile(r"\bcool(?:ing)?\b", re.I)),
    ("heating", re.compile(r"\bheat(?:ing)?\b", re.I)),
    ("head", re.compile(r"\bhead\b|\bTDH\b", re.I)),
    ("motor", re.compile(r"\bmotor\b", re.I)),
    ("max", re.compile(r"\bmax(?:imum)?\b", re.I)),
    ("min", re.compile(r"\bmin(?:imum)?\b", re.I)),
]

# Family names stripped from the qualifier window first, so the family's
# own label ("Heat Pump", "Fan Coil") never reads as a qualifier on the
# value that follows it.
_FAMILY_PHRASE_RES = [
    re.compile(p, re.I)
    for p in (
        r"heat\s*pump",
        r"fan\s*coil",
        r"heat\s*recovery",
        r"air\s*handling\s*unit",
        r"condens(?:ing|er)\s*unit",
        r"energy\s*recovery",
    )
]

_FEET_RE = re.compile(r"\d+\s*(?:'|ft\b)", re.I)
_INCH_RE = re.compile(r'\d+\s*(?:"|\bin\b)', re.I)
_BARE_NUMBER_RE = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?!\s*[A-Za-z/])(?!\s*-)")


@dataclass(frozen=True)
class MechValue:
    """One stated schedule value, resolved to a slot with provenance.

    Attributes:
        field: The slot this value fills, one of ``VALUE_SLOTS``.
        value: The stated number; ``None`` for a stated range (the
            schedule states an interval, not a number) and for a value
            nulled by a same-slot conflict.
        unit: The unit as the schedule states it (``CFM``, ``ton``,
            ``kW``), never converted.
        raw: The raw substring the value was read from.
        sheet / row / cell: Where the raw string lives (``"Type"``,
            ``5``, ``"E5"``); ``cell`` names column letter and row so a
            reviewer can open the workbook cell directly.
        confidence: ``"high"`` (explicit qualifier or unambiguous unit),
            ``"medium"`` (family default or IP default), ``"low"``
            (nulled by a same-slot conflict).
        zone_load_impacting: True for the airflow slots, which feed
            zone loads downstream. Recorded inputs only: nothing here
            sizes equipment, and any downstream HVAC sizing consumes
            zone loads first.
    """

    field: str
    value: Optional[float]
    unit: str
    raw: str
    sheet: str
    row: int
    cell: str
    confidence: str
    zone_load_impacting: bool = False


def _qualifier_before(text: str, start: int, prev_end: int) -> Optional[str]:
    """Return the qualifier word governing the value at ``text[start:]``.

    Scans the 80 characters before the number, from the end of the
    previous value match, with family-name phrases stripped first so
    ``"Heat Pump, 10 kW"`` never reads ``heat`` as a heating qualifier.
    """

    window = text[prev_end:start][-80:]
    for phrase in _FAMILY_PHRASE_RES:
        window = phrase.sub(" ", window)
    for name, pattern in _QUALIFIER_RES:
        if pattern.search(window):
            return name
    return None


def _map_value_slot(
    family: str, unit_lower: str, unit_raw: str, qualifier: Optional[str]
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Map one unit/qualifier/family triple to a value slot.

    Returns ``(slot, unit, confidence, reason)``: a slot with its
    confidence and the unit as stated (normalized to the capacity
    parser's unit spellings) when the triple is readable, or
    ``(None, None, None, reason)`` when the value must not be recorded.
    ``reason`` is ``None`` for units that are simply not capacities
    (dimensions such as ``200 mm``), which are skipped silently exactly
    as ``parse_capacity`` skips them.
    """

    if unit_lower in _AIRFLOW_UNITS:
        slot = "airflow"
        if qualifier == "min":
            slot = "airflow_min"
        elif qualifier == "max":
            slot = "airflow_max"
        return slot, _AIRFLOW_UNITS[unit_lower], "high", None
    if unit_lower in _WATER_FLOW_UNITS:
        return "water_flow", "GPM", "high", None
    if unit_lower in _POWER_UNITS:
        display = _POWER_UNITS[unit_lower]
        if qualifier == "motor":
            return "power", display, "high", None
        if qualifier in ("cooling", "heating"):
            return f"{qualifier}_capacity", display, "high", None
        if family in _POWER_FAMILIES:
            return "power", display, "medium", None
        return (
            None,
            None,
            None,
            f"unit '{display}' on a '{family}' is ambiguous between power "
            f"and thermal capacity; value not recorded",
        )
    if unit_lower in _HEAD_UNITS:
        if qualifier == "head":
            return "head", _HEAD_UNITS[unit_lower], "high", None
        return None, None, None, None
    if unit_lower in _NOT_A_UNIT:
        # A dimension (``200 mm``, ``12 in``, ``600 x 600``), not a
        # capacity: skipped silently, exactly as parse_capacity skips it.
        return None, None, None, None
    # Thermal units (ton, MBH, BTU, BTU/h, J, kJ, MJ), as the capacity
    # parser already normalizes them.
    known = CAPACITY_UNITS.get(unit_lower)
    if qualifier == "cooling":
        return "cooling_capacity", known, "high", None
    if qualifier == "heating":
        return "heating_capacity", known, "high", None
    family_slot = _THERMAL_FAMILY_SLOTS.get(family)
    if family_slot:
        return family_slot, known, "medium", None
    if family in _AMBIGUOUS_THERMAL_FAMILIES:
        return (
            None,
            None,
            None,
            f"family '{family}' states thermal capacity without saying "
            f"cooling or heating; value not recorded",
        )
    return (
        None,
        None,
        None,
        f"unit '{known or unit_raw}' does not map to a known slot for a "
        f"'{family}'; value not recorded",
    )


def _value_candidates(
    text: str, family: str
) -> Tuple[List[Tuple[str, Optional[float], str, str, str]], List[Tuple[str, str]]]:
    """Extract slot candidates and unmappable-value reasons from one text.

    Returns ``(candidates, reviews)``. Each candidate is
    ``(slot, value, unit, raw, confidence)``; each review entry is
    ``(raw, reason)`` for a stated value that must not be recorded. Bare
    numbers are used only under the IP-default rule (the text states
    sizes in both feet and inches and the family has a default);
    otherwise they are unused, never guessed.
    """

    candidates: List[Tuple[str, Optional[float], str, str, str]] = []
    reviews: List[Tuple[str, str]] = []
    spans: List[Tuple[int, int]] = []
    prev_end = 0
    for m in _CAP_RE.finditer(text):
        unit_lower = m.group(3).lower()
        qualifier = _qualifier_before(text, m.start(), prev_end)
        prev_end = m.end()
        spans.append(m.span())
        raw = m.group(0).strip()
        value = float(m.group(1).replace(",", "")) if m.group(2) is None else None
        slot, unit, confidence, reason = _map_value_slot(family, unit_lower, m.group(3), qualifier)
        if slot is not None and unit is not None and confidence is not None:
            candidates.append((slot, value, unit, raw, confidence))
        elif reason is not None:
            reviews.append((raw, reason))
    default = None
    if _FEET_RE.search(text) and _INCH_RE.search(text):
        default = _BARE_NUMBER_DEFAULTS.get(family)
    if default:
        slot, unit = default
        for m in _BARE_NUMBER_RE.finditer(text):
            if any(s <= m.start() < e for s, e in spans):
                continue
            raw = m.group(0).strip()
            candidates.append((slot, float(raw.replace(",", "")), unit, raw, "medium"))
    return candidates, reviews


_ATTR_NO_VALUE = {"n/a", "na", "", "none", "-"}

_ATTR_RANGE_RE = re.compile(r"^(\d[\d,]*)\s*-\s*(\d[\d,]*)$")


def _attribute_candidates(
    reqs: List[dict], family: Optional[str]
) -> List[Tuple[str, Optional[float], str, str, str, str, int, str]]:
    """Turn stated ``Attribute``-sheet requirements into slot candidates.

    The clinic workbook states per-component requirements on the
    ``Attribute`` sheet (``Total`` / ``Outside Air``, L/s); the text scan
    never sees those cells, so the airflow slots stayed empty and the
    schedule-truth benchmark measured airflow recovery 0/33 (#1240
    follow-up). Each requirement's unit is read directly from the stated
    ``Unit`` cell, no text parsing, at ``high`` confidence; its stated
    ``Name`` picks the slot. ``Outside Air`` is ventilation air, not a
    supply total, so it fills its own slot and can never misstate a
    fan's airflow; ``min``/``max`` in the name map to the min/max slots;
    everything else fills ``airflow``.

    Scope line: only requirements whose unit names airflow are read in
    this pass (the measured gap). Requirements in other units, and value
    cells that state no readable number (``n/a``, prose), are left for a
    separate decision, recorded here rather than silently invented.
    """

    fam = family or "unclassified"
    out: List[Tuple[str, Optional[float], str, str, str, str, int, str]] = []
    for req in reqs:
        unit_lower = req["unit"].strip().lower()
        if unit_lower not in _AIRFLOW_UNITS:
            continue
        text = str(req["value"] if req["value"] is not None else "").strip()
        if text.lower() in _ATTR_NO_VALUE:
            continue
        value: Optional[float]
        range_hit = _ATTR_RANGE_RE.match(text.replace(" ", ""))
        if range_hit:
            value = None  # the requirement states an interval, not a number
        else:
            try:
                value = float(text.replace(",", ""))
            except ValueError:
                continue
        name_lower = req["name"].strip().lower()
        if "outside air" in name_lower:
            slot = "outside_air_flow"
        elif "min" in name_lower:
            slot = "airflow_min"
        elif "max" in name_lower:
            slot = "airflow_max"
        else:
            slot = "airflow"
        _ = fam  # the unit is explicit; no family default is in play
        out.append(
            (
                slot,
                value,
                _AIRFLOW_UNITS[unit_lower],
                text,
                "high",
                "Attribute",
                int(req["row"]),
                req["value_cell"],
            )
        )
    return out


def _row_values(
    tag: str,
    family: Optional[str],
    type_description: str,
    component_description: str,
    type_cell: str,
    component_cell: str,
    attribute_candidates: Optional[
        List[Tuple[str, Optional[float], str, str, str, str, int, str]]
    ] = None,
) -> Tuple[List[MechValue], List[ReviewItem]]:
    """Resolve one row's stated values into ``MechValue``s and review items.

    Both the Type description and the Component description are scanned,
    and any stated ``Attribute``-sheet requirement candidates join them,
    so a slot stated twice with different numbers across sources is one
    conflict: every value for that slot is nulled (``value=None``,
    confidence ``low``) and one review item names both raw strings. A
    stated value that maps to no slot gets no value and one review item.
    Nothing is ever fabricated.
    """

    fam = family or "unclassified"
    candidates: List[Tuple[str, Optional[float], str, str, str, str, int, str]] = []
    reviews: List[Tuple[str, str, str, str]] = []
    if attribute_candidates:
        candidates.extend(attribute_candidates)
    for text, sheet, cell in (
        (type_description, "Type", type_cell),
        (component_description, "Component", component_cell),
    ):
        if not text or not cell:
            continue
        row_num = int(re.sub(r"\D", "", cell) or 0)
        cands, revs = _value_candidates(text, fam)
        candidates.extend(
            (slot, value, unit, raw, confidence, sheet, row_num, cell)
            for slot, value, unit, raw, confidence in cands
        )
        reviews.extend((raw, reason, sheet, cell) for raw, reason in revs)

    review_items: List[ReviewItem] = []
    used_ids: set = set()
    for raw, reason, sheet, cell in reviews:
        item_id = f"mechval:{tag}:{cell}"
        n = 2
        while item_id in used_ids:
            item_id = f"mechval:{tag}:{cell}:{n}"
            n += 1
        used_ids.add(item_id)
        review_items.append(
            ReviewItem(
                id=item_id,
                kind="mech_schedule_value",
                description=(
                    f"Mechanical schedule value '{raw}' on {sheet}!{cell} for '{tag}': {reason}"
                ),
                confidence=0.4,
                target={"kind": "value", "id": tag},
            )
        )

    by_slot: Dict[str, List[Tuple[str, Optional[float], str, str, str, str, int, str]]] = {}
    for cand in candidates:
        by_slot.setdefault(cand[0], []).append(cand)

    values: List[MechValue] = []
    for slot in sorted(by_slot):
        group = by_slot[slot]
        numeric = {c[1] for c in group if c[1] is not None}
        conflicting = len(numeric) > 1
        if conflicting:
            first, last = group[0], group[-1]
            item_id = f"mechval:{tag}:{first[7]}"
            n = 2
            while item_id in used_ids:
                item_id = f"mechval:{tag}:{first[7]}:{n}"
                n += 1
            used_ids.add(item_id)
            review_items.append(
                ReviewItem(
                    id=item_id,
                    kind="mech_schedule_value",
                    description=(
                        f"Mechanical schedule conflict for '{tag}' "
                        f"'{slot}': '{first[3]}' on {first[5]}!{first[7]} "
                        f"vs '{last[3]}' on {last[5]}!{last[7]}; both "
                        f"values nulled, confirm with the schedule"
                    ),
                    confidence=0.4,
                    target={"kind": "value", "id": tag},
                )
            )
        for _, value, unit, raw, confidence, sheet, row_num, cell in group:
            values.append(
                MechValue(
                    field=slot,
                    value=None if conflicting else value,
                    unit=unit,
                    raw=raw,
                    sheet=sheet,
                    row=row_num,
                    cell=cell,
                    confidence="low" if conflicting else confidence,
                    zone_load_impacting=slot in ZONE_LOAD_SLOTS,
                )
            )
    return values, review_items


@dataclass(frozen=True)
class MechScheduleRow:
    """One typed row of a mechanical schedule.

    Attributes:
        tag: Equipment tag parsed from the component name (``AC-1``),
            empty when the name carries none.
        name: Full component name as the schedule states it.
        type_name / type_description: The referenced Type row's name and
            description.
        category: The Type row's category string, carried verbatim. The
            workbook's category column is unreliable, so this is evidence
            for provenance only, never for classification.
        description: The component row's own description.
        service_area: The component's service-area reference (``1B21``),
            empty when the schedule states none.
        system_type: Classified type (``ahu`` and the other values in
            ``SYSTEM_CLASSES`` plus ``supply_diffuser``,
            ``return_diffuser``, ``terminal``, ``other``), ``None`` when
            confidence is low.
        confidence: ``"high"`` (tag and text agree), ``"medium"`` (one
            signal), or ``"low"`` (conflict or neither).
        capacity: ``{"value": float|None, "unit": str, "raw": str,
            "source": str}`` parsed from the type description as stated,
            or ``None`` when the schedule states no capacity. Kept
            exactly as ``parse_capacity`` produces it for backward
            compatibility; the per-slot reading lives in ``values``.
        values: The row's stated values resolved to slots
            (``MechValue``), empty when none are readable.
        provenance: Source path, sheet, row numbers, extension object and
            creation date for this row.
    """

    tag: str
    name: str
    type_name: str
    type_description: str
    category: str
    description: str
    service_area: str
    system_type: Optional[str]
    confidence: str
    capacity: Optional[Dict[str, Any]]
    values: List[MechValue] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MechSystem:
    """A first-class scheduled mechanical system, linked to rooms.

    The follow-on MEP slice consumes these for air-loop building; each
    scheduled AHU/RTU/fan-coil becomes one entity with its stated
    capacity and the rooms its service area names. ``values`` copies the
    row's slot values, so a system carries its stated capacities,
    airflows and the review provenance for each without a re-parse.
    """

    tag: str
    name: str
    system_type: str
    capacity: Optional[Dict[str, Any]]
    service_area: str
    rooms: List[Dict[str, Any]] = field(default_factory=list)
    values: List[MechValue] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MechScheduleResult:
    """Parsed schedule: typed rows, system entities, review items, stats."""

    rows: List[MechScheduleRow] = field(default_factory=list)
    systems: List[MechSystem] = field(default_factory=list)
    review_items: List[ReviewItem] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


def parse_tag(name: str) -> str:
    """Return the equipment tag parsed from a component name.

    The clinic workbook leaves ``TagNumber`` as ``n/a`` and embeds the
    tag in the name (``"AC Unit Type 1 AC-1"``). The trailing
    ``PREFIX-digits`` token wins; ``Fan- EF1-1`` yields ``EF1-1``.
    """

    tokens = _TAG_RE.findall(name)
    if tokens:
        prefix, digits = tokens[-1]
        return f"{prefix}-{digits}"
    return ""


def _tag_prefix(tag: str) -> Optional[str]:
    m = _TAG_RE.fullmatch(tag)
    if not m:
        return None
    return re.sub(r"\d+$", "", m.group(1)) or None


def _text_type(*texts: str) -> Optional[str]:
    joined = " ".join(t for t in texts if t)
    for system_type, pattern in TEXT_RULES:
        if pattern.search(joined):
            return system_type
    return None


_FAN_FAMILY = {"fan", "exhaust_fan"}


def classify(
    tag: str,
    type_name: str,
    type_description: str,
    component_description: str = "",
) -> Tuple[Optional[str], str]:
    """Classify one row's system type from tag and text evidence.

    Returns ``(system_type, confidence)``. ``system_type`` is ``None``
    when confidence is not high/medium. Tag and text agreement is high
    confidence; a single signal is medium; conflicting evidence is low
    (the caller routes those rows to the review queue); no evidence at
    all is ``"none"`` (the caller excludes those rows from the
    schedule).
    """

    tag_prefix = _tag_prefix(tag)
    tag_type = TAG_PREFIX_TYPES.get(tag_prefix) if tag_prefix else None
    text_type = _text_type(type_name, type_description, component_description)
    if tag_type and text_type:
        if tag_type == text_type:
            return tag_type, "high"
        if tag_type in _FAN_FAMILY and text_type in _FAN_FAMILY:
            return ("exhaust_fan" if "exhaust_fan" in (tag_type, text_type) else "fan"), "high"
        return None, "low"
    if tag_type:
        return tag_type, "medium"
    if text_type:
        return text_type, "medium"
    return None, "none"


def parse_capacity(text: str) -> Optional[Dict[str, Any]]:
    """Parse a stated capacity string without converting units.

    ``"63300000 J"`` parses to ``{"value": 63300000.0, "unit": "J"}``;
    ``"633-703 kW"`` is a stated range, so ``value`` stays ``None`` and
    the raw string is kept. Dimension strings (``"600 x 600 Face"``,
    ``"200 mm"``) never parse. ``source`` records where the string came
    from; the caller fills the row reference in.
    """

    if not text:
        return None
    for m in _CAP_RE.finditer(text):
        lo, hi, unit_raw = m.group(1), m.group(2), m.group(3)
        unit = CAPACITY_UNITS.get(unit_raw.lower())
        if unit is None or unit_raw.lower() in _NOT_A_UNIT:
            continue
        raw = m.group(0).strip()
        if hi is not None:
            return {"value": None, "unit": unit, "raw": raw, "source": "type.description"}
        value = float(lo.replace(",", ""))
        return {"value": value, "unit": unit, "raw": raw, "source": "type.description"}
    return None


def parse_cobie_workbook(path: "str | Path") -> MechScheduleResult:
    """Parse a COBie 2.4 workbook's mechanical schedule into typed rows.

    Only components with positive air-system evidence (a known tag
    prefix or a type-name/description text hit) enter the schedule;
    everything else is counted in ``stats["excluded"]`` with a sample of
    the excluded type names. Service areas are matched against the
    workbook's own ``Space`` sheet, so ``MechSystem.rooms`` is
    self-contained; ``link_to_model`` later binds them to a model.
    Stated per-component requirements on the ``Attribute`` sheet fill
    slots too (airflow units in this pass; see ``_attribute_candidates``).
    """

    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ImportError(
            "parse_cobie_workbook needs openpyxl; install it with "
            "`pip install matchline[mech]` or `pip install openpyxl`"
        ) from exc

    from openpyxl.utils import get_column_letter

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    types = list(wb["Type"].iter_rows(values_only=True))
    comps = list(wb["Component"].iter_rows(values_only=True))
    spaces = list(wb["Space"].iter_rows(values_only=True))
    attr_reqs: Dict[str, List[dict]] = {}
    if "Attribute" in wb.sheetnames:
        attrs = list(wb["Attribute"].iter_rows(values_only=True))
        acol = {str(h): i for i, h in enumerate(attrs[0]) if h is not None}
        if all(k in acol for k in ("SheetName", "RowName", "Name", "Value", "Unit")):
            value_letter = get_column_letter(acol["Value"] + 1)
            for ai, arow in enumerate(attrs[1:], start=2):
                if str(arow[acol["SheetName"]] or "") != "Component":
                    continue
                row_name = str(arow[acol["RowName"]] or "")
                if not row_name:
                    continue
                attr_reqs.setdefault(row_name, []).append(
                    {
                        "name": str(arow[acol["Name"]] or ""),
                        "value": arow[acol["Value"]],
                        "unit": str(arow[acol["Unit"]] or ""),
                        "row": ai,
                        "value_cell": f"{value_letter}{ai}",
                    }
                )
    theader = [str(h) for h in types[0]]
    cheader = [str(h) for h in comps[0]]
    space_names = {str(r[0]) for r in spaces[1:] if r[0] is not None}
    tcol = {h: i for i, h in enumerate(theader)}
    ccol = {h: i for i, h in enumerate(cheader)}
    tcol_letter = get_column_letter(tcol["Description"] + 1)
    ccol_letter = get_column_letter(ccol["Description"] + 1)

    result = MechScheduleResult()
    by_type: Counter = Counter()
    by_confidence: Counter = Counter()
    excluded = 0
    excluded_samples: List[str] = []
    systems_by_tag: Dict[str, MechSystem] = {}

    type_rows: Dict[str, Tuple[int, tuple]] = {}
    for ti, trow in enumerate(types[1:], start=2):
        type_rows[str(trow[tcol["Name"]])] = (ti, trow)

    for ri, crow in enumerate(comps[1:], start=2):
        name = str(crow[ccol["Name"]] or "")
        type_name = str(crow[ccol["TypeName"]] or "")
        entry = type_rows.get(type_name)
        type_row, type_description, category = (None, "", "")
        if entry is not None:
            type_row, trow = entry
            type_description = str(trow[tcol["Description"]] or "")
            category = str(trow[tcol["Category"]] or "")
        description = str(crow[ccol["Description"]] or "")
        service_area = str(crow[ccol["Space"]] or "")
        if service_area.lower() in {"n/a", "none"}:
            service_area = ""
        ext_object = str(crow[ccol["ExtObject"]] or "")
        created_on = str(crow[ccol["CreatedOn"]] or "")

        tag = parse_tag(name)
        system_type, confidence = classify(tag, type_name, type_description, description)
        if system_type is None and confidence == "none":
            # No positive evidence anywhere (no known tag prefix, no
            # type/description text hit): not schedule material.
            excluded += 1
            if len(excluded_samples) < 8:
                excluded_samples.append(type_name or name)
            continue
        provenance = {
            "source": str(Path(path).name),
            "sheet": "Component",
            "row": ri,
            "type_row": type_row,
            "ext_object": ext_object,
            "created_on": created_on,
        }
        capacity = parse_capacity(type_description)
        values, value_reviews = _row_values(
            tag or name,
            system_type,
            type_description,
            description,
            f"{tcol_letter}{type_row}" if type_row is not None else "",
            f"{ccol_letter}{ri}",
            attribute_candidates=_attribute_candidates(attr_reqs.get(name, []), system_type),
        )
        result.review_items.extend(value_reviews)
        row = MechScheduleRow(
            tag=tag,
            name=name,
            type_name=type_name,
            type_description=type_description,
            category=category,
            description=description,
            service_area=service_area,
            system_type=system_type,
            confidence=confidence,
            capacity=capacity,
            values=values,
            provenance=provenance,
        )
        result.rows.append(row)
        by_type[system_type or "unclassified"] += 1
        by_confidence[confidence] += 1

        if confidence == "low":
            item_id = f"mech:{tag or name}:{ri}"
            result.review_items.append(
                ReviewItem(
                    id=item_id,
                    kind="mech_schedule",
                    description=(
                        f"Mechanical schedule row '{name}' has conflicting "
                        f"tag/text evidence (tag '{tag}', type "
                        f"'{type_name}'); system type unclassified"
                    ),
                    confidence=0.3,
                    target={"kind": "system", "id": tag or name},
                )
            )
            continue

        if system_type in SYSTEM_CLASSES:
            rooms = (
                [{"space": service_area, "matched": service_area in space_names}]
                if service_area
                else []
            )
            system = MechSystem(
                tag=tag or name,
                name=name,
                system_type=system_type,
                capacity=capacity,
                service_area=service_area,
                rooms=rooms,
                values=list(values),
                provenance=provenance,
            )
            result.systems.append(system)
            systems_by_tag[system.tag] = system

    result.stats = {
        "schema": SCHEMA,
        "total_components": len(comps) - 1,
        "rows": len(result.rows),
        "systems": len(result.systems),
        "review_items": len(result.review_items),
        "excluded": excluded,
        "excluded_samples": excluded_samples,
        "by_type": dict(by_type),
        "by_confidence": dict(by_confidence),
        "values": sum(len(r.values) for r in result.rows),
        "zone_load_inputs": sum(1 for r in result.rows for v in r.values if v.zone_load_impacting),
        "value_review_items": sum(
            1 for i in result.review_items if i.kind == "mech_schedule_value"
        ),
    }
    wb.close()
    return result


def link_to_model(systems: List[MechSystem], model: Any) -> Dict[str, List[str]]:
    """Bind each system's service area to model rooms by room number/name.

    Returns ``{system tag: [matched space ids]}``. A service area that no
    model room carries is left unmatched and reported in the returned
    mapping as an empty list, never guessed.
    """

    spaces = getattr(model, "spaces", {}) or {}
    by_number: Dict[str, List[str]] = {}
    by_name: Dict[str, List[str]] = {}
    for sid, space in spaces.items():
        number = str(getattr(space, "number", "") or "")
        name = str(getattr(space, "name", "") or "")
        if number:
            by_number.setdefault(number, []).append(sid)
        if name:
            by_name.setdefault(name, []).append(sid)
    matched: Dict[str, List[str]] = {}
    for system in systems:
        refs = [room["space"] for room in system.rooms]
        ids: List[str] = []
        for ref in refs:
            candidates = by_number.get(ref) or by_name.get(ref) or []
            ids.extend(candidates)
        matched[system.tag] = ids
    return matched
