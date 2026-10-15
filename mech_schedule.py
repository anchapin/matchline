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
  ``"mech_schedule"``) and keeps ``system_type=None`` until confirmed.

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
            or ``None`` when the schedule states no capacity.
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
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MechSystem:
    """A first-class scheduled mechanical system, linked to rooms.

    The follow-on MEP slice consumes these for air-loop building; each
    scheduled AHU/RTU/fan-coil becomes one entity with its stated
    capacity and the rooms its service area names.
    """

    tag: str
    name: str
    system_type: str
    capacity: Optional[Dict[str, Any]]
    service_area: str
    rooms: List[Dict[str, Any]] = field(default_factory=list)
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

    tag_type = TAG_PREFIX_TYPES.get(_tag_prefix(tag))
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
    """

    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ImportError(
            "parse_cobie_workbook needs openpyxl; install it with "
            "`pip install matchline[mech]` or `pip install openpyxl`"
        ) from exc

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    types = list(wb["Type"].iter_rows(values_only=True))
    comps = list(wb["Component"].iter_rows(values_only=True))
    spaces = list(wb["Space"].iter_rows(values_only=True))
    theader = [str(h) for h in types[0]]
    cheader = [str(h) for h in comps[0]]
    space_names = {str(r[0]) for r in spaces[1:] if r[0] is not None}
    tcol = {h: i for i, h in enumerate(theader)}
    ccol = {h: i for i, h in enumerate(cheader)}

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
