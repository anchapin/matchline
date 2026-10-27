"""#1292: HVAC sizing inputs consumed from the recorded zone-load values.

The two-phase contract of ``hvac_sizing``: collect the recorded
zone-load-impacting inputs first, then size from them. The ordering
guard must refuse to size before the inputs are registered.
"""

import pytest

from hvac_sizing import (
    ORDERING_RULE,
    ZoneLoadInput,
    ZoneLoadInputs,
    ZoneLoadInputsNotSet,
    collect_zone_load_inputs,
    size_from_zone_loads,
    zone_load_slots,
)
from mech_schedule import ZONE_LOAD_SLOTS, MechScheduleResult, MechScheduleRow, MechValue


def _value(field, value, unit="CFM", raw=None, sheet="Type", row=5, cell="E5", confidence="high"):
    return MechValue(
        field=field,
        value=value,
        unit=unit,
        raw=raw if raw is not None else f"{value} {unit}",
        sheet=sheet,
        row=row,
        cell=cell,
        confidence=confidence,
        zone_load_impacting=field in {"airflow", "airflow_min", "airflow_max", "outside_air_flow"},
    )


def _row(tag, values, service_area="1B21", system_type="ahu", name=None):
    return MechScheduleRow(
        tag=tag,
        name=name or tag,
        type_name="AHU",
        type_description="",
        category="23-75",
        description="",
        service_area=service_area,
        system_type=system_type,
        confidence="high",
        capacity=None,
        values=list(values),
        provenance={},
    )


def _result(rows):
    return MechScheduleResult(rows=list(rows), systems=[], review_items=[], stats={})


def _sample_result():
    return _result(
        [
            _row(
                "AHU-1",
                [
                    _value("airflow", 2000.0),
                    _value("airflow_min", 1200.0),
                    _value("airflow_max", 2600.0),
                    _value("outside_air_flow", 400.0, unit="L/s", raw="400 L/s"),
                    _value("power", 12.0, unit="kW", raw="12 kW"),
                ],
            ),
            _row(
                "VAV-1",
                [
                    _value("airflow", 350.0, sheet="Component", row=7, cell="F7"),
                    _value("head", 60.0, unit="ft", raw="60 ft"),
                ],
                service_area="2B12",
                system_type="vav_box",
            ),
        ]
    )


def test_zone_load_slots_match_the_readers_set():
    assert set(zone_load_slots()) == set(ZONE_LOAD_SLOTS)
    assert zone_load_slots() == ("airflow", "airflow_min", "airflow_max", "outside_air_flow")


def test_collect_keeps_only_zone_load_impacting_values():
    inputs = collect_zone_load_inputs(_sample_result())
    assert inputs.registered is True
    assert len(inputs) == 5
    assert {i.field for i in inputs.inputs} == {
        "airflow",
        "airflow_min",
        "airflow_max",
        "outside_air_flow",
    }
    # power and head are recorded but never zone-load impacting
    assert not any(i.field in {"power", "head"} for i in inputs.inputs)


def test_collect_preserves_provenance_and_equipment():
    inputs = collect_zone_load_inputs(_sample_result())
    ahu = [i for i in inputs.inputs if i.equipment == "AHU-1"]
    airflow = next(i for i in ahu if i.field == "airflow")
    assert airflow.service_area == "1B21"
    assert airflow.system_type == "ahu"
    assert airflow.sheet == "Type"
    assert airflow.cell == "E5"
    assert airflow.row == 5
    assert airflow.confidence == "high"
    assert airflow.provenance == "Type!E5"
    vav = [i for i in inputs.inputs if i.equipment == "VAV-1"]
    assert vav[0].sheet == "Component"
    assert vav[0].service_area == "2B12"


def test_sizing_totals_match_hand_computed_sums():
    inputs = collect_zone_load_inputs(_sample_result())
    sized = size_from_zone_loads(inputs)
    ahu = sized.by_equipment["AHU-1"]
    assert ahu.total_for("airflow").total == 2000.0
    assert ahu.total_for("airflow_min").total == 1200.0
    assert ahu.total_for("airflow_max").total == 2600.0
    # outside air is stated in L/s, so it is totalled in L/s, never converted
    oa = ahu.total_for("outside_air_flow")
    assert oa.unit == "L/s"
    assert oa.total == 400.0
    vav = sized.by_equipment["VAV-1"]
    assert vav.total_for("airflow").total == 350.0
    assert vav.total_for("power") is None
    assert vav.total_for("head") is None
    # the whole-schedule total is the sum of the per-equipment totals
    assert sized.totals[("airflow", "CFM")].total == 2000.0 + 350.0
    assert sized.totals[("airflow", "CFM")].count == 2
    assert sized.input_count == 5


def test_totals_are_grouped_by_stated_unit():
    result = _result(
        [
            _row("A-1", [_value("airflow", 1000.0, unit="CFM")]),
            _row("B-1", [_value("airflow", 5.0, unit="L/s", raw="5 L/s")]),
        ]
    )
    sized = size_from_zone_loads(collect_zone_load_inputs(result))
    assert sized.totals[("airflow", "CFM")].total == 1000.0
    assert sized.totals[("airflow", "L/s")].total == 5.0
    # the two units are never added together
    assert len([k for k in sized.totals if k[0] == "airflow"]) == 2


def test_ordering_guard_refuses_sizing_before_registration():
    with pytest.raises(ZoneLoadInputsNotSet) as excinfo:
        size_from_zone_loads(ZoneLoadInputs())
    assert "zone loads" in str(excinfo.value)
    assert "#1236" in str(excinfo.value)


def test_ordering_guard_refuses_unregistered_registry():
    inputs = ZoneLoadInputs(
        inputs=(
            ZoneLoadInput(
                equipment="AHU-1",
                system_type="ahu",
                service_area="1B21",
                field="airflow",
                value=100.0,
                unit="CFM",
                raw="100 CFM",
                sheet="Type",
                row=2,
                cell="E2",
                confidence="high",
            ),
        ),
        registered=False,
    )
    with pytest.raises(ZoneLoadInputsNotSet):
        size_from_zone_loads(inputs)


def test_ordering_guard_refuses_a_non_registry_argument():
    with pytest.raises(ZoneLoadInputsNotSet):
        size_from_zone_loads(_sample_result())


def test_sizing_succeeds_once_inputs_are_registered():
    inputs = collect_zone_load_inputs(_sample_result())
    sized = size_from_zone_loads(inputs)
    assert sized.by_equipment["AHU-1"].total_for("airflow").total == 2000.0


def test_non_zone_load_values_are_not_consumed():
    result = _result(
        [
            _row(
                "P-1",
                [
                    _value("power", 7.5, unit="kW", raw="7.5 kW"),
                    _value("head", 60.0, unit="ft", raw="60 ft"),
                    _value("water_flow", 50.0, unit="GPM", raw="50 GPM"),
                ],
                system_type="pump",
            )
        ]
    )
    inputs = collect_zone_load_inputs(result)
    assert len(inputs) == 0
    with pytest.raises(ZoneLoadInputsNotSet):
        size_from_zone_loads(inputs)


def test_conflicting_slot_values_are_surfaced_not_double_counted():
    # two different stated values for one slot on one equipment
    result = _result(
        [
            _row(
                "AHU-1",
                [
                    _value("airflow", 2000.0, raw="2000 CFM", cell="E5"),
                    _value("airflow", 2400.0, raw="2400 CFM", cell="E6"),
                ],
            )
        ]
    )
    sized = size_from_zone_loads(collect_zone_load_inputs(result))
    summary = sized.by_equipment["AHU-1"]
    assert summary.total_for("airflow") is None
    conflicts = [i for i in sized.issues if i.kind == "conflict"]
    assert len(conflicts) == 1
    assert "2000 CFM" in conflicts[0].detail
    assert "2400 CFM" in conflicts[0].detail
    assert ("airflow", "CFM") not in sized.totals


def test_duplicate_slot_values_are_counted_once_and_reported():
    result = _result(
        [
            _row(
                "AHU-1",
                [
                    _value("airflow", 2000.0, raw="2000 CFM", sheet="Type", cell="E5"),
                    _value("airflow", 2000.0, raw="2000 CFM", sheet="Component", cell="F5"),
                ],
            )
        ]
    )
    sized = size_from_zone_loads(collect_zone_load_inputs(result))
    summary = sized.by_equipment["AHU-1"]
    assert summary.total_for("airflow").total == 2000.0
    assert summary.total_for("airflow").count == 1
    duplicates = [i for i in sized.issues if i.kind == "duplicate"]
    assert len(duplicates) == 1
    assert "Type!E5" in duplicates[0].detail
    assert "Component!F5" in duplicates[0].detail
    assert sized.totals[("airflow", "CFM")].total == 2000.0


def test_nulled_value_is_surfaced_as_unresolved():
    result = _result(
        [
            _row(
                "AHU-1",
                [
                    _value(
                        "airflow",
                        None,
                        raw="633-703 CFM",
                        confidence="low",
                    )
                ],
            )
        ]
    )
    sized = size_from_zone_loads(collect_zone_load_inputs(result))
    assert sized.by_equipment["AHU-1"].total_for("airflow") is None
    unresolved = [i for i in sized.issues if i.kind == "unresolved"]
    assert len(unresolved) == 1
    assert "633-703 CFM" in unresolved[0].detail


def test_system_values_are_not_double_counted_with_their_row():
    row = _row("AHU-1", [_value("airflow", 2000.0)])
    system = type(
        "S",
        (),
        {
            "tag": "AHU-1",
            "name": "AHU-1",
            "system_type": "ahu",
            "service_area": "1B21",
            "values": list(row.values),
        },
    )()
    result = MechScheduleResult(rows=[row], systems=[system], review_items=[], stats={})
    inputs = collect_zone_load_inputs(result)
    assert len(inputs) == 1
    sized = size_from_zone_loads(inputs)
    assert sized.totals[("airflow", "CFM")].total == 2000.0
    assert sized.totals[("airflow", "CFM")].count == 1


def test_sizing_is_deterministic():
    inputs = collect_zone_load_inputs(_sample_result())
    first = size_from_zone_loads(inputs)
    second = size_from_zone_loads(inputs)
    assert first.by_equipment.keys() == second.by_equipment.keys()
    assert first.totals == second.totals
    assert list(first.by_equipment) == sorted(first.by_equipment)


def test_ordering_rule_message_names_the_ledger_decision():
    with pytest.raises(ZoneLoadInputsNotSet) as excinfo:
        size_from_zone_loads(ZoneLoadInputs())
    assert "#1236" in str(excinfo.value)
    assert "zone loads" in str(excinfo.value)
    assert ORDERING_RULE.startswith("any downstream HVAC sizing consumes zone loads first")
