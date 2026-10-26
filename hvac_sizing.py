"""HVAC sizing inputs from the recorded zone-load-impacting schedule values.

``mech_schedule`` records the zone-load-impacting airflow inputs a
mechanical schedule states (the slots in ``mech_schedule.ZONE_LOAD_SLOTS``,
flagged on every ``MechValue`` with ``zone_load_impacting=True``) and
nothing else: it never sizes equipment (ledger decision #1236). This
module is the downstream consumer of those recorded inputs, and it is the
first place they are aggregated.

Two phases, in this order, enforced in code:

1. **Collect.** :func:`collect_zone_load_inputs` walks a parsed
   ``mech_schedule.MechScheduleResult`` and gathers every value flagged
   ``zone_load_impacting`` into a :class:`ZoneLoadInputs` registry. Every
   extracted fact keeps its provenance (sheet, row, cell, raw text,
   confidence) and the equipment tag and service area it was stated for.
2. **Size.** :func:`size_from_zone_loads` turns a registered registry into
   a :class:`SizingResult`: one summary per equipment with the stated
   totals for ``airflow``, ``airflow_min``, ``airflow_max`` and
   ``outside_air_flow``.

**Ordering guard.** Sizing refuses to run before the inputs are
registered: :func:`size_from_zone_loads` raises
:class:`ZoneLoadInputsNotSet` when it is handed anything other than a
registered :class:`ZoneLoadInputs` (an empty or unregistered registry, or
a raw parse result that was never collected). The rule is the ledger
decision #1236 one: *any downstream HVAC sizing consumes zone loads
first.*

This is an aggregation of stated inputs, not a load model. It never
converts units (totals are grouped by the unit the schedule stated),
never invents a capacity, and never picks between two conflicting
statements: a conflicting or duplicated slot is surfaced as a
:class:`SizingIssue` instead of being silently double-counted.

Typical use::

    from mech_schedule import parse_cobie_workbook
    from hvac_sizing import collect_zone_load_inputs, size_from_zone_loads

    result = parse_cobie_workbook("/path/clinic-design.xlsx")
    inputs = collect_zone_load_inputs(result)   # phase 1
    sized = size_from_zone_loads(inputs)        # phase 2
    for summary in sized.by_equipment.values():
        print(summary.equipment, summary.totals)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mech_schedule import ZONE_LOAD_SLOTS

SCHEMA = "matchline.hvac_sizing/1"

#: The zone-load slots this module totals, in a fixed order so every
#: summary is deterministic. It mirrors ``mech_schedule.ZONE_LOAD_SLOTS``,
#: the reader's set is the source of truth for which slots exist.
ZONE_LOAD_SLOT_ORDER: Tuple[str, ...] = (
    "airflow",
    "airflow_min",
    "airflow_max",
    "outside_air_flow",
)

#: The rule the ordering guard enforces, quoted from ledger decision
#: #1236 so the error message names the rule it enforces.
ORDERING_RULE = "any downstream HVAC sizing consumes zone loads first (ledger decision #1236)"


class ZoneLoadInputsNotSet(RuntimeError):
    """Raised when HVAC sizing runs before the zone-load inputs are registered.

    The guard for ledger decision #1236: zone loads are consumed first, so
    sizing never runs against an unset or unregistered input set.
    """


@dataclass(frozen=True)
class ZoneLoadInput:
    """One recorded zone-load-impacting value, with its provenance.

    Attributes:
        equipment: The equipment tag the value was stated for (the row
            tag, or the row name when the schedule states no tag).
        system_type: The classified system type, ``None`` when the row
            classification is low confidence.
        service_area: The service area the equipment serves, as the
            schedule states it (empty when it states none).
        field: The slot the value fills (one of ``ZONE_LOAD_SLOTS``).
        value: The stated number, ``None`` when the schedule states a
            range or the slot was nulled by a conflict upstream.
        unit: The unit as the schedule states it (``CFM``, ``L/s``),
            never converted.
        raw: The raw substring the value was read from.
        sheet / row / cell: Where the raw string lives in the workbook.
        confidence: The confidence the schedule reader recorded.
    """

    equipment: str
    system_type: Optional[str]
    service_area: str
    field: str
    value: Optional[float]
    unit: str
    raw: str
    sheet: str
    row: int
    cell: str
    confidence: str

    @property
    def provenance(self) -> str:
        """A short ``sheet!cell`` provenance string for messages."""
        return f"{self.sheet}!{self.cell}"


@dataclass(frozen=True)
class ZoneLoadInputs:
    """Registry of the zone-load-impacting inputs, collected before sizing.

    ``registered`` is the ordering guard's state flag: only
    :func:`collect_zone_load_inputs` sets it, so a hand-built or
    unregistered registry cannot be sized.

    Attributes:
        inputs: The collected inputs, in collection order.
        registered: Whether the inputs were collected and registered.
    """

    inputs: Tuple[ZoneLoadInput, ...] = ()
    registered: bool = False

    def __len__(self) -> int:
        return len(self.inputs)

    def __bool__(self) -> bool:
        return bool(self.inputs)

    def by_equipment(self) -> Dict[str, List[ZoneLoadInput]]:
        """The collected inputs grouped by equipment tag."""
        grouped: Dict[str, List[ZoneLoadInput]] = {}
        for item in self.inputs:
            grouped.setdefault(item.equipment, []).append(item)
        return grouped


@dataclass(frozen=True)
class SlotTotal:
    """The stated total for one slot, in the unit the schedule stated.

    Attributes:
        field: The slot summed.
        unit: The unit as stated; totals are never converted.
        total: The summed stated value.
        count: How many distinct stated values the total is made of.
        inputs: The inputs that contributed (the distinct values only;
            duplicates and conflicts are reported as issues instead).
    """

    field: str
    unit: str
    total: float
    count: int
    inputs: Tuple[ZoneLoadInput, ...] = ()

    @property
    def key(self) -> Tuple[str, str]:
        """The ``(field, unit)`` key this total is filed under."""
        return (self.field, self.unit)


@dataclass(frozen=True)
class SizingIssue:
    """A slot that could not be totalled, and why.

    Attributes:
        equipment: The equipment the slot belongs to.
        field: The slot in question.
        unit: The unit as stated.
        kind: ``"conflict"`` (two different stated values for one slot),
            ``"duplicate"`` (the same value stated more than once) or
            ``"unresolved"`` (the schedule reader nulled the value).
        detail: A human-readable description naming the statements.
        inputs: The inputs involved.
    """

    equipment: str
    field: str
    unit: str
    kind: str
    detail: str
    inputs: Tuple[ZoneLoadInput, ...] = ()

    @property
    def key(self) -> Tuple[str, str]:
        """The ``(field, unit)`` key this issue belongs to."""
        return (self.field, self.unit)


@dataclass(frozen=True)
class SizingSummary:
    """The sizing inputs of one equipment, totalled per slot and unit.

    Attributes:
        equipment: The equipment tag.
        system_type: The classified system type, ``None`` when the row
            classification is low confidence.
        service_area: The service area the equipment serves, as stated.
        totals: The stated totals, keyed by ``(field, unit)``.
        issues: Slots that were surfaced instead of totalled.
    """

    equipment: str
    system_type: Optional[str]
    service_area: str
    totals: Dict[Tuple[str, str], SlotTotal] = field(default_factory=dict)
    issues: Tuple[SizingIssue, ...] = ()

    def total_for(self, slot: str, unit: Optional[str] = None) -> Optional[SlotTotal]:
        """The stated total for ``slot`` (optionally in one ``unit``).

        ``None`` when the equipment states no such slot, when the unit
        does not match, or when the slot was surfaced as an issue.
        """
        if unit is not None:
            return self.totals.get((slot, unit))
        for (name, _unit), total in self.totals.items():
            if name == slot:
                return total
        return None


@dataclass(frozen=True)
class SizingResult:
    """The sizing inputs of a whole schedule, totalled per equipment.

    Attributes:
        by_equipment: One summary per equipment, keyed by tag.
        totals: The stated totals of the whole schedule, keyed by
            ``(field, unit)``; the sum of the per-equipment totals.
        issues: Every issue raised anywhere in the schedule.
        input_count: How many zone-load-impacting inputs were collected.
    """

    by_equipment: Dict[str, SizingSummary] = field(default_factory=dict)
    totals: Dict[Tuple[str, str], SlotTotal] = field(default_factory=dict)
    issues: Tuple[SizingIssue, ...] = ()
    input_count: int = 0

    def issues_for(self, equipment: str) -> Tuple[SizingIssue, ...]:
        """The issues raised for one equipment."""
        return tuple(i for i in self.issues if i.equipment == equipment)


def _row_equipment(row: Any) -> str:
    """The equipment key of a schedule row: its tag, else its name."""
    return getattr(row, "tag", "") or getattr(row, "name", "")


def _collect_row(row: Any, seen: set, out: List[ZoneLoadInput]) -> None:
    """Append the row's zone-load-impacting values to ``out``.

    Only values flagged ``zone_load_impacting`` are taken, so power,
    head, water flow and capacity slots never enter a zone-load total.
    """

    equipment = _row_equipment(row)
    system_type = getattr(row, "system_type", None)
    service_area = getattr(row, "service_area", "") or ""
    for value in getattr(row, "values", None) or []:
        if not getattr(value, "zone_load_impacting", False):
            continue
        key = (
            equipment,
            value.field,
            value.unit,
            value.raw,
            value.sheet,
            value.row,
            value.cell,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ZoneLoadInput(
                equipment=equipment,
                system_type=system_type,
                service_area=service_area,
                field=value.field,
                value=value.value,
                unit=value.unit,
                raw=value.raw,
                sheet=value.sheet,
                row=value.row,
                cell=value.cell,
                confidence=value.confidence,
            )
        )


def collect_zone_load_inputs(result: Any) -> ZoneLoadInputs:
    """Collect the zone-load-impacting inputs of a parsed schedule (phase 1).

    Walks the rows and systems of a ``mech_schedule.MechScheduleResult``
    and keeps only the values flagged ``zone_load_impacting``. Rows carry
    the service area; the systems' copies of the same values are not
    counted twice. Every input keeps its sheet/row/cell provenance and
    the confidence the schedule reader recorded.

    Returns a registered :class:`ZoneLoadInputs`, which is what
    :func:`size_from_zone_loads` requires.
    """

    inputs: List[ZoneLoadInput] = []
    seen: set = set()
    for row in getattr(result, "rows", None) or []:
        _collect_row(row, seen, inputs)
    for system in getattr(result, "systems", None) or []:
        # A system copies its row's values; only systems whose values are
        # not already carried by a row add anything here.
        _collect_row(system, seen, inputs)
    return ZoneLoadInputs(inputs=tuple(inputs), registered=True)


def _group_inputs(inputs: Sequence[ZoneLoadInput]) -> Dict[Tuple[str, str], List[ZoneLoadInput]]:
    """Group inputs by ``(equipment, field, unit)``."""
    grouped: Dict[Tuple[str, str], List[ZoneLoadInput]] = {}
    for item in inputs:
        grouped.setdefault((item.equipment, item.field, item.unit), []).append(item)
    return grouped


def _sum_slot(
    equipment: str, items: Sequence[ZoneLoadInput]
) -> Tuple[Optional[SlotTotal], List[SizingIssue]]:
    """Total one ``(equipment, field, unit)`` group, or surface its issues.

    Conflicting stated values (``value`` differs) and values the schedule
    reader nulled (``value is None``) are never totalled and never
    silently resolved: they become issues. The same value stated more
    than once is counted once and reported as a duplicate.
    """

    issues: List[SizingIssue] = []
    resolved = [i for i in items if i.value is not None]
    unresolved = [i for i in items if i.value is None]
    distinct: Dict[float, List[ZoneLoadInput]] = {}
    for item in resolved:
        distinct.setdefault(float(item.value), []).append(item)

    if unresolved:
        issues.append(
            SizingIssue(
                equipment=equipment,
                field=items[0].field,
                unit=items[0].unit,
                kind="unresolved",
                detail=(
                    f"{items[0].field} for '{equipment}' was stated as "
                    f"{'; '.join(repr(i.raw) for i in unresolved)} and could not "
                    f"be resolved upstream, so it is not totalled"
                ),
                inputs=tuple(unresolved),
            )
        )
    if len(distinct) > 1:
        detail = "; ".join(
            f"{value} ({items_of_value[0].raw}, {items_of_value[0].provenance})"
            for value, items_of_value in sorted(distinct.items())
        )
        issues.append(
            SizingIssue(
                equipment=equipment,
                field=items[0].field,
                unit=items[0].unit,
                kind="conflict",
                detail=(
                    f"{items[0].field} for '{equipment}' is stated with "
                    f"conflicting values ({detail}); none is totalled"
                ),
                inputs=tuple(resolved),
            )
        )
        return None, issues
    if not distinct:
        return None, issues

    value = next(iter(distinct))
    contributors = distinct[value]
    if len(contributors) > 1:
        detail = "; ".join(f"{i.raw} ({i.provenance})" for i in contributors)
        issues.append(
            SizingIssue(
                equipment=equipment,
                field=items[0].field,
                unit=items[0].unit,
                kind="duplicate",
                detail=(
                    f"{items[0].field} for '{equipment}' is stated more than "
                    f"once with the same value {value} ({detail}); counted once"
                ),
                inputs=tuple(contributors),
            )
        )
    total = SlotTotal(
        field=items[0].field,
        unit=items[0].unit,
        total=float(value),
        count=1,
        inputs=tuple(contributors),
    )
    return total, issues


def size_from_zone_loads(inputs: ZoneLoadInputs) -> SizingResult:
    """Size from the registered zone-load inputs (phase 2).

    Returns the stated totals per equipment and for the schedule as a
    whole, grouped by ``(field, unit)`` so no unit is ever converted.

    Raises:
        ZoneLoadInputsNotSet: when the inputs were not registered, are
            empty, or are not a collected registry at all. Zone loads
            are consumed first (ledger decision #1236), so sizing never
            runs before the inputs exist.
    """

    if not isinstance(inputs, ZoneLoadInputs):
        raise ZoneLoadInputsNotSet(
            f"HVAC sizing requires collected zone-load inputs: {ORDERING_RULE}. "
            f"Call collect_zone_load_inputs(result) first; got {type(inputs).__name__}."
        )
    if not inputs.registered:
        raise ZoneLoadInputsNotSet(
            f"HVAC sizing requires registered zone-load inputs: {ORDERING_RULE}. "
            f"The registry was never registered by collect_zone_load_inputs()."
        )
    if not inputs.inputs:
        raise ZoneLoadInputsNotSet(
            f"HVAC sizing requires registered zone-load inputs: {ORDERING_RULE}. "
            f"The registry holds no zone-load-impacting inputs."
        )

    grouped = _group_inputs(inputs.inputs)
    meta: Dict[str, Tuple[Optional[str], str]] = {}
    equipment_totals: Dict[str, Dict[Tuple[str, str], SlotTotal]] = {}
    equipment_issues: Dict[str, List[SizingIssue]] = {}
    all_issues: List[SizingIssue] = []
    totals: Dict[Tuple[str, str], SlotTotal] = {}

    for (equipment, slot, unit), items in sorted(grouped.items()):
        total, issues = _sum_slot(equipment, items)
        meta.setdefault(equipment, (items[0].system_type, items[0].service_area))
        equipment_totals.setdefault(equipment, {})
        equipment_issues.setdefault(equipment, [])
        if total is not None:
            equipment_totals[equipment][(slot, unit)] = total
            key = (slot, unit)
            running = totals.get(key)
            if running is None:
                totals[key] = SlotTotal(
                    field=slot,
                    unit=unit,
                    total=total.total,
                    count=total.count,
                    inputs=total.inputs,
                )
            else:
                totals[key] = SlotTotal(
                    field=slot,
                    unit=unit,
                    total=running.total + total.total,
                    count=running.count + total.count,
                    inputs=running.inputs + total.inputs,
                )
        equipment_issues[equipment].extend(issues)
        all_issues.extend(issues)

    by_equipment = {
        equipment: SizingSummary(
            equipment=equipment,
            system_type=meta[equipment][0],
            service_area=meta[equipment][1],
            totals=dict(equipment_totals.get(equipment, {})),
            issues=tuple(equipment_issues.get(equipment, [])),
        )
        for equipment in sorted(meta)
    }

    return SizingResult(
        by_equipment=by_equipment,
        totals=totals,
        issues=tuple(all_issues),
        input_count=len(inputs.inputs),
    )


def zone_load_slots() -> Tuple[str, ...]:
    """The zone-load slots this module totals, in a fixed order.

    The set is ``mech_schedule.ZONE_LOAD_SLOTS`` (the reader's source of
    truth); the order is fixed here so every summary is deterministic.
    """
    return tuple(s for s in ZONE_LOAD_SLOT_ORDER if s in ZONE_LOAD_SLOTS)


__all__ = [
    "ORDERING_RULE",
    "SCHEMA",
    "SizingIssue",
    "SizingResult",
    "SizingSummary",
    "SlotTotal",
    "ZoneLoadInput",
    "ZoneLoadInputs",
    "ZoneLoadInputsNotSet",
    "collect_zone_load_inputs",
    "size_from_zone_loads",
    "zone_load_slots",
]
