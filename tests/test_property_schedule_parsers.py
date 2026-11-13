"""Property-based tests for the schedule-table parsers and the quantity paths.

Audit of existing Hypothesis coverage (done first, per #1392):

Already covered by ``tests/test_property_based.py`` (the only Hypothesis file
in the repo — a repo-wide grep for ``hypothesis`` outside that file finds no
other property tests):

* ``geometry/geometry_simplify.simplify_ring`` — the envelope-area simplifier
  area gate is ALREADY property-tested there:
  ``test_simplify_ring_area_delta_within_tolerance`` (area delta within the
  tolerance), ``test_simplify_ring_area_never_grows``, plus vertex-count,
  convexity and two regression properties. The area-gate target is therefore
  DROPPED from this file (not duplicated).
* ``geometry/polygon_classify`` — ``_classify_from_evidence`` determinism,
  valid ``poly_type``, confidence bounds and ``classify_polygons``
  determinism. Not a schedule/quantity target, so nothing dropped from it.

Newly covered here (no property existed for any of these):

* ``schedule_parse`` — the DWG schedule-table parsers: ``parse_schedules``,
  ``build_schedule_table`` (entities, blocks and OLE parts), cell/row
  coordinates, and the quantity totals carried by a schedule table. A fixed
  hostile fixture exists in ``tests/test_schedule_parse.py``
  (``test_parse_schedules_never_raises_on_hostile_drawing``), but it is one
  hand-written drawing; the properties below are the generative versions.
* ``mech_schedule`` — the stated-quantity path: ``parse_capacity`` (value,
  unit, raw) and ``_same_quantity`` (the unit-blind comparison that decides
  whether two statements of one slot are one fact).
* ``ingest/pdf_schedules`` — the PDF schedule-table quantity helpers:
  ``_number``, ``_size`` and ``_capacities``.
* ``facade_takeoff`` — the takeoff quantity path: pixel-class counts, the
  wall-plane fractions and the absolute areas derived from a supplied scale.

Conventions follow the existing property file (#345): every property uses
``@settings(..., derandomize=True)`` so the suite is deterministic and
reproducible, and the settings are the repo's own (no new profile is
registered, no global Hypothesis configuration is changed).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from facade_takeoff import WALL_PLANE, class_counts, facade_takeoff
from ingest.pdf_schedules import _capacities, _number, _size
from mech_schedule import CAPACITY_UNITS, _same_quantity, parse_capacity
from schedule_parse import parse_schedules

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_JSON_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**6), max_value=10**6),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=12),
)

_JSON_VALUES = st.recursive(
    _JSON_SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(max_size=6), children, max_size=4),
    ),
    max_leaves=12,
)

_DRAWINGS = st.fixed_dictionaries(
    {
        "table_entities": st.lists(_JSON_VALUES, max_size=4),
        "block_refs": st.lists(_JSON_VALUES, max_size=4),
        "ole_entities": st.lists(_JSON_VALUES, max_size=4),
        "block_defs": st.one_of(
            st.none(),
            st.dictionaries(st.text(max_size=6), _JSON_VALUES, max_size=4),
        ),
    }
)

# A well-formed schedule table: a tag column and two quantity columns.
_QUANTITIES = st.lists(st.integers(min_value=0, max_value=100_000), min_size=1, max_size=6)
_WELL_FORMED_TABLES = st.fixed_dictionaries(
    {
        "name": st.text(min_size=1, max_size=20),
        "headers": st.just(["TAG", "QTY", "AREA"]),
        "rows": st.lists(
            st.tuples(st.text(max_size=6), st.integers(0, 100_000), st.floats(0.0, 1000.0)),
            min_size=1,
            max_size=6,
        ),
    }
)

_MASKS = st.lists(
    st.lists(st.integers(min_value=1, max_value=12), min_size=5, max_size=5),
    min_size=4,
    max_size=4,
)

_CAPACITY_TEXTS = st.one_of(
    st.text(max_size=30),
    st.integers(min_value=0, max_value=100_000).map(lambda n: f"{n} kW"),
    st.tuples(st.integers(0, 100_000), st.integers(0, 100_000)).map(lambda p: f"{p[0]}-{p[1]} kW"),
    st.sampled_from(
        [
            "63300000 J",
            "10 kW",
            "10000 watts",
            "200 mm",
            "600 x 600 Face",
            "5 tons",
            "",
        ]
    ),
)

_SCHEDULE_HEADERS = st.lists(
    st.sampled_from(
        [
            "COOLING TOTAL (MBH)",
            "COOLING SENSIBLE (MBH)",
            "HEATING (MBH)",
            "REHEAT (MBH)",
            "COOLING CAPACITY (kW)",
            "AIRFLOW (CFM)",
            "EAT",
            "LWT",
            "",
        ]
    ),
    max_size=5,
)


def _is_table_entity(entity: object) -> bool:
    """Whether a raw drawing entity payload names a TABLE entity."""
    if not isinstance(entity, dict):
        return False
    kind = entity.get("type", entity.get("entity_type", ""))
    return isinstance(kind, str) and kind == "TABLE"


# ---------------------------------------------------------------------------
# schedule_parse: the schedule-table parsers
# ---------------------------------------------------------------------------


@given(drawing=_DRAWINGS)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_parse_schedules_never_raises_on_generated_drawings(drawing):
    """Any generated drawing — valid or malformed — parses without raising."""
    tables = parse_schedules(drawing, "sheet_1", "r1")

    assert isinstance(tables, list)
    for table in tables:
        assert 0.0 <= table.provenance.confidence <= 1.0
        for row in table.rows:
            assert 0.0 <= row.provenance.confidence <= 1.0
            for cell in row.cells:
                assert 0.0 <= cell.provenance.confidence <= 1.0


@given(payload=st.none())
@settings(max_examples=20, deadline=30000, derandomize=True)
def test_parse_schedules_of_a_non_drawing_is_empty(payload):
    """A drawing that is not a mapping yields no tables at all."""
    assert parse_schedules(payload, "sheet_1", "r1") == []


@given(drawing=_DRAWINGS)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_parse_schedules_table_count_follows_the_entities(drawing):
    """One table per entity; sources without an owner add one combined table."""
    entities = drawing["table_entities"]
    has_table = any(_is_table_entity(e) for e in entities)
    has_sources = bool(drawing["block_refs"]) or bool(drawing["ole_entities"])

    tables = parse_schedules(drawing, "sheet_1", "r1")

    expected = len(entities) + (1 if (not has_table and has_sources) else 0)
    assert len(tables) == expected


@given(drawing=_DRAWINGS)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_schedule_table_cell_coordinates_are_unique_and_contiguous(drawing):
    """Row coordinates are contiguous and every cell coordinate is unique."""
    tables = parse_schedules(drawing, "sheet_1", "r1")

    for table in tables:
        seen: set[tuple[int, int]] = set()
        for index, row in enumerate(table.rows):
            assert row.row_index == index
            for col, cell in enumerate(row.cells):
                assert cell.row == row.row_index
                assert cell.col == col
                assert (cell.row, cell.col) not in seen
                seen.add((cell.row, cell.col))


@given(table_spec=_WELL_FORMED_TABLES)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_schedule_table_preserves_wellformed_rows(table_spec):
    """A well-formed table comes back with its rows, cells and headers intact."""
    drawing = {
        "table_entities": [
            {
                "handle": "H1",
                "type": "TABLE",
                "data": {
                    "name": table_spec["name"],
                    "headers": list(table_spec["headers"]),
                    "rows": [list(row) for row in table_spec["rows"]],
                },
            }
        ],
        "block_refs": [],
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_1", "r1")

    assert len(tables) == 1
    table = tables[0]
    assert table.name == table_spec["name"]
    assert table.headers == list(table_spec["headers"])
    assert len(table.rows) == len(table_spec["rows"])
    for row, raw in zip(table.rows, table_spec["rows"]):
        assert [cell.value for cell in row.cells] == [str(value) for value in raw]


@given(table_spec=_WELL_FORMED_TABLES)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_schedule_table_quantities_conserve_their_totals(table_spec):
    """Quantity conservation: the parsed QTY and AREA totals equal the stated ones."""
    drawing = {
        "table_entities": [
            {
                "handle": "H1",
                "type": "TABLE",
                "data": {
                    "name": table_spec["name"],
                    "headers": list(table_spec["headers"]),
                    "rows": [list(row) for row in table_spec["rows"]],
                },
            }
        ],
        "block_refs": [],
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_1", "r1")
    rows = tables[0].rows

    qty_total = sum(float(row.cells[1].value) for row in rows)
    area_total = sum(float(row.cells[2].value) for row in rows)

    assert qty_total == pytest.approx(sum(int(row[1]) for row in table_spec["rows"]))
    assert area_total == pytest.approx(sum(float(row[2]) for row in table_spec["rows"]))


# ---------------------------------------------------------------------------
# mech_schedule: the stated-quantity path
# ---------------------------------------------------------------------------


@given(text=_CAPACITY_TEXTS)
@settings(max_examples=300, deadline=30000, derandomize=True)
def test_parse_capacity_never_raises_and_names_a_known_unit(text):
    """Any text parses to nothing, or to a stated value with a known unit."""
    result = parse_capacity(text)

    if result is None:
        return
    assert set(result) == {"value", "unit", "raw", "source"}
    assert result["unit"] in set(CAPACITY_UNITS.values())
    assert result["source"] == "type.description"
    if result["value"] is None:
        # A stated range keeps the raw string and states no number.
        assert "-" in result["raw"]
    else:
        assert isinstance(result["value"], float)
        assert math.isfinite(result["value"])
        assert result["value"] >= 0.0
        assert "-" not in result["raw"]


@given(value=st.floats(min_value=0.01, max_value=1e6, allow_nan=False))
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_same_quantity_is_scale_blind_within_a_known_family(value):
    """A kW and a W statement of one magnitude are one fact; 0.5% more is not."""
    assert _same_quantity(value, "kW", value * 1000.0, "W")
    assert _same_quantity(value, "W", value, "W")
    assert not _same_quantity(value, "kW", value * 1000.0 * 1.005, "W")
    assert not _same_quantity(value, "kW", value * 1000.0 * 0.5, "W")


@given(
    value=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    other=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_same_quantity_compares_raw_magnitudes_across_families(value, other):
    """Across families (or families with no known scale) the raw magnitudes rule."""
    assert _same_quantity(value, "kW", other, "J") == (value == other)
    assert _same_quantity(value, "CFM", other, "CFM") == (value == other)
    assert _same_quantity(value, "ton", other, "MBH") == (value == other)


# ---------------------------------------------------------------------------
# ingest/pdf_schedules: the PDF schedule-table quantity helpers
# ---------------------------------------------------------------------------


@given(text=st.text(max_size=30))
@settings(max_examples=300, deadline=30000, derandomize=True)
def test_pdf_number_reads_a_finite_number_or_nothing(text):
    """``_number`` returns a finite, non-negative float or nothing at all."""
    value = _number(text)

    if value is None:
        return
    assert isinstance(value, float)
    assert math.isfinite(value)
    assert value >= 0.0


@given(text=st.text(max_size=30))
@settings(max_examples=300, deadline=30000, derandomize=True)
def test_pdf_size_returns_two_dimensions_or_nothing(text):
    """``_size`` returns two dimensions, each a number or nothing."""
    parts = _size(text)

    assert isinstance(parts, tuple)
    assert len(parts) == 2
    for part in parts:
        if part is None:
            continue
        assert isinstance(part, float)
        assert math.isfinite(part)
        assert part >= 0.0


@given(headers=_SCHEDULE_HEADERS, values=st.lists(st.text(max_size=12), max_size=5))
@settings(max_examples=300, deadline=30000, derandomize=True)
def test_pdf_capacities_only_report_known_duties(headers, values):
    """``_capacities`` reports only the four known duties, each with a number."""
    found = _capacities(headers, values)

    assert set(found) <= {"cooling_total", "cooling_sensible", "heating", "reheat"}
    for entry in found.values():
        assert set(entry) == {"value", "unit", "column"}
        assert isinstance(entry["value"], float)
        assert math.isfinite(entry["value"])
        assert isinstance(entry["unit"], str)
        assert isinstance(entry["column"], str)


@given(
    headers=st.lists(
        st.sampled_from(
            ["COOLING TOTAL (MBH)", "COOLING CAPACITY (kW)", "HEATING (MBH)", "REHEAT (MBH)"]
        ),
        min_size=2,
        max_size=4,
    ),
    values=st.lists(st.text(max_size=12), max_size=5),
)
@settings(max_examples=200, deadline=30000, derandomize=True)
def test_pdf_capacities_never_pick_one_of_two_columns_for_one_duty(headers, values):
    """Two columns for one duty leave that duty out instead of picking one."""
    found = _capacities(headers, values)

    duties = []
    for header in headers:
        upper = header.upper()
        if "COOLING" in upper and "SENSIBLE" not in upper:
            duties.append("cooling_total")
        elif "HEATING" in upper:
            duties.append("heating")
        elif "REHEAT" in upper:
            duties.append("reheat")
    for duty in set(duties):
        if duties.count(duty) > 1:
            assert duty not in found


# ---------------------------------------------------------------------------
# facade_takeoff: the takeoff quantity path
# ---------------------------------------------------------------------------


@given(mask_rows=_MASKS)
@settings(
    max_examples=200,
    deadline=30000,
    suppress_health_check=[HealthCheck.filter_too_much],
    derandomize=True,
)
def test_facade_takeoff_class_counts_conserve_the_total_pixels(mask_rows):
    """The per-class pixel counts add up to the pixels of the mask."""
    mask = np.array(mask_rows, dtype=np.int32)

    counts = class_counts(mask)

    assert sum(counts.values()) == mask.size
    assert all(count >= 0 for count in counts.values())


@given(mask_rows=_MASKS)
@settings(
    max_examples=200,
    deadline=30000,
    suppress_health_check=[HealthCheck.filter_too_much],
    derandomize=True,
)
def test_facade_takeoff_fractions_conserve_the_wall_plane(mask_rows):
    """Opaque, glazing and door fractions sum to one over the wall plane."""
    mask = np.array(mask_rows, dtype=np.int32)

    takeoff = facade_takeoff(mask, "facade_1")

    assert takeoff.wall_plane_px == sum(takeoff.class_px.get(c, 0) for c in WALL_PLANE)
    if takeoff.wall_plane_px == 0:
        assert takeoff.frac_opaque is None
        assert takeoff.frac_glazing is None
        assert takeoff.frac_door is None
        return
    assert takeoff.frac_opaque + takeoff.frac_glazing + takeoff.frac_door == pytest.approx(1.0)
    assert takeoff.wwr == pytest.approx(takeoff.frac_glazing)


@given(
    mask_rows=_MASKS,
    width_m=st.floats(min_value=0.5, max_value=100.0, allow_nan=False),
)
@settings(
    max_examples=200,
    deadline=30000,
    suppress_health_check=[HealthCheck.filter_too_much],
    derandomize=True,
)
def test_facade_takeoff_absolute_areas_conserve_the_wall_plane_area(mask_rows, width_m):
    """With a scale, the three absolute areas add up to the wall-plane area."""
    mask = np.array(mask_rows, dtype=np.int32)
    assume((mask != 1).any())

    takeoff = facade_takeoff(mask, "facade_1", width_m=width_m)

    if takeoff.px_per_m is None:
        assert takeoff.area_wall_plane_m2 is None
        return
    assert takeoff.area_opaque_m2 + takeoff.area_glazing_m2 + takeoff.area_door_m2 == pytest.approx(
        takeoff.area_wall_plane_m2
    )
    if takeoff.wall_plane_px == 0:
        assert takeoff.area_wall_plane_m2 == 0.0
        return
    assert takeoff.area_glazing_m2 / takeoff.area_wall_plane_m2 == pytest.approx(
        takeoff.frac_glazing
    )
