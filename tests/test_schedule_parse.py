"""Tests for schedule_parse.py — schedule and table parsing from DWG sources."""

from schedule_parse import (
    BlockSchedule,
    OleSchedule,
    ScheduleTable,
    TableEntity,
    build_schedule_table,
    parse_block_schedule,
    parse_ole_schedule,
    parse_schedules,
    parse_table_entity,
)


def test_parse_table_entity_basic():
    entity_data = {
        "layer": "A_SCHEDS",
        "handle": "ABC123",
        "type": "TABLE",
        "owner_handle": "OWNER456",
        "data": {"name": "Door Schedule", "headers": ["Tag", "Type"], "rows": [["D1", "Single"]]},
    }
    entity = parse_table_entity(entity_data)

    assert isinstance(entity, TableEntity)
    assert entity.layer == "A_SCHEDS"
    assert entity.handle == "ABC123"
    assert entity.entity_type == "TABLE"
    assert entity.owner_handle == "OWNER456"
    assert entity.provenance.method == "dwg_table_entity"
    assert entity.provenance.confidence == 0.95


def test_parse_table_entity_missing_handle():
    entity_data = {
        "layer": "A_SCHEDS",
        "type": "TABLE",
    }
    entity = parse_table_entity(entity_data)

    assert entity.handle == ""
    assert entity.provenance.confidence == 0.70


def test_parse_block_schedule_basic():
    block_ref = {
        "handle": "BLOCK001",
        "name": "SCHEDULE_BLOCK",
        "x": 10.5,
        "y": 20.3,
        "z": 0.0,
        "attributes": [
            {"tag": "ROOM", "value": "L1-101"},
            {"tag": "AREA", "value": "25.5"},
        ],
    }
    block_def = {"name": "SCHEDULE_BLOCK"}

    schedule = parse_block_schedule(block_ref, block_def)

    assert isinstance(schedule, BlockSchedule)
    assert schedule.block_name == "SCHEDULE_BLOCK"
    assert schedule.handle == "BLOCK001"
    assert schedule.insertion_point == (10.5, 20.3, 0.0)
    assert schedule.attribute_tags == ["ROOM", "AREA"]
    assert schedule.attribute_values == ["L1-101", "25.5"]
    assert schedule.provenance.method == "dwg_block_schedule"
    assert schedule.provenance.confidence == 0.90


def test_parse_block_schedule_no_attrs():
    block_ref = {
        "handle": "BLOCK002",
        "name": "ANNOTATION",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "attributes": [],
    }
    block_def = {"name": "ANNOTATION"}

    schedule = parse_block_schedule(block_ref, block_def)

    assert schedule.attribute_tags == []
    assert schedule.attribute_values == []
    assert schedule.provenance.confidence == 0.75


def test_parse_ole_schedule_basic():
    ole_data = {
        "type": "Microsoft_Excel_Sheet",
        "data": {"headers": ["A", "B"], "rows": [[1, 2]]},
        "source_entity": "OLE001",
    }
    schedule = parse_ole_schedule(ole_data)

    assert schedule is not None
    assert isinstance(schedule, OleSchedule)
    assert schedule.ole_type == "Microsoft_Excel_Sheet"
    assert schedule.source_entity == "OLE001"
    assert schedule.provenance.method == "ole_spreadsheet"
    assert schedule.provenance.confidence == 0.60


def test_parse_ole_schedule_missing_type():
    ole_data = {"data": {}}
    schedule = parse_ole_schedule(ole_data)

    assert schedule is None


def test_build_schedule_table_table_entity():
    entity_data = {
        "layer": "A_SCHEDS",
        "handle": "TBL001",
        "type": "TABLE",
        "owner_handle": "",
        "data": {
            "name": "Window Schedule",
            "headers": ["Mark", "Width", "Height"],
            "rows": [["W1", "1200", "1500"], ["W2", "900", "1200"]],
        },
    }
    entity = parse_table_entity(entity_data)
    entities = [entity]
    blocks: list[BlockSchedule] = []
    ole_schedules: list[OleSchedule | None] = []

    table = build_schedule_table(entities, blocks, ole_schedules, "sheet_A101", "1")

    assert isinstance(table, ScheduleTable)
    assert table.name == "Window Schedule"
    assert table.headers == ["Mark", "Width", "Height"]
    assert len(table.rows) == 2
    assert table.rows[0].cells[0].value == "W1"
    assert table.rows[0].cells[0].provenance.sheet_id == "sheet_A101"
    assert table.rows[0].cells[0].provenance.revision == "1"
    assert table.rows[0].cells[0].provenance.method == "dwg_table_entity"


def test_build_schedule_table_block_schedule():
    block_ref = {
        "handle": "SCH001",
        "name": "ROOM_SCHEDULE",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "attributes": [
            {"tag": "Room", "value": "101"},
            {"tag": "Area", "value": "25"},
        ],
    }
    block_def = {"name": "ROOM_SCHEDULE"}
    block = parse_block_schedule(block_ref, block_def)

    table = build_schedule_table([], [block], [], "sheet_A101", "2")

    assert len(table.rows) == 1
    assert table.rows[0].cells[0].value == "101"
    assert table.rows[0].cells[0].provenance.method == "dwg_block_schedule"


def test_build_schedule_table_ole_schedule():
    ole_data = {
        "type": "Excel",
        "data": {
            "headers": ["Name", "Value"],
            "rows": [["Item1", "100"]],
        },
        "source_entity": "OLE001",
    }
    ole = parse_ole_schedule(ole_data)

    table = build_schedule_table([], [], [ole], "sheet_A101", "3")

    assert len(table.rows) == 1
    assert table.rows[0].cells[0].value == "Item1"
    assert table.rows[0].cells[0].provenance.method == "ole_spreadsheet"


def test_build_schedule_table_combined():
    entity_data = {
        "layer": "A_SCHEDS",
        "handle": "TBL001",
        "type": "TABLE",
        "owner_handle": "",
        "data": {
            "name": "Combined",
            "headers": ["Col1"],
            "rows": [["Val1"]],
        },
    }
    entity = parse_table_entity(entity_data)

    block_ref = {
        "handle": "SCH001",
        "name": "SCHEDULE",
        "x": 0.0,
        "y": 0.0,
        "z": 0.0,
        "attributes": [{"tag": "Tag1", "value": "V1"}],
    }
    block = parse_block_schedule(block_ref, {})

    ole = parse_ole_schedule(
        {"type": "Excel", "data": {"headers": [], "rows": []}, "source_entity": None}
    )

    table = build_schedule_table([entity], [block], [ole], "sheet_X", "1")

    assert len(table.rows) == 2
    assert table.provenance.sheet_id == "sheet_X"


def test_parse_schedules_integration():
    drawing = {
        "table_entities": [
            {
                "layer": "A_SCHEDS",
                "handle": "TBL001",
                "type": "TABLE",
                "owner_handle": "",
                "data": {
                    "name": "Test Schedule",
                    "headers": ["Name"],
                    "rows": [["Test"]],
                },
            }
        ],
        "block_refs": [],
        "block_defs": {},
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_001", "rev1")

    assert len(tables) == 1
    assert tables[0].name == "Test Schedule"
    assert tables[0].provenance.sheet_id == "sheet_001"


def test_provenance_attached_to_all_facts():
    entity_data = {
        "layer": "A_SCHEDS",
        "handle": "TBL001",
        "type": "TABLE",
        "owner_handle": "",
        "data": {
            "headers": ["A", "B"],
            "rows": [["1", "2"]],
        },
    }
    entity = parse_table_entity(entity_data)
    table = build_schedule_table([entity], [], [], "S1", "R1")

    assert table.provenance.sheet_id == "S1"
    assert table.provenance.revision == "R1"

    for row in table.rows:
        assert row.provenance.sheet_id == "S1"
        for cell in row.cells:
            assert cell.provenance.sheet_id == "S1"
            assert cell.provenance.revision == "R1"
            assert cell.provenance.method == "dwg_table_entity"
            assert 0.0 <= cell.provenance.confidence <= 1.0


# --- Malformed / untrusted drawing data (#1309) -------------------------------
# Policy: a malformed field degrades to the existing low-confidence/default path
# (or the entry is skipped with a provenance note). Nothing may raise out of
# parse_schedules, and every affected fact must carry a reduced confidence or a
# provenance note.


def _all_cells(tables):
    for table in tables:
        for row in table.rows:
            for cell in row.cells:
                yield cell


def test_parse_block_schedule_non_numeric_coordinates():
    schedule = parse_block_schedule(
        {"handle": "B1", "name": "SCHED", "x": "N/A", "y": None, "z": "1.5"},
        {"name": "SCHED"},
    )

    assert schedule.insertion_point == (0.0, 0.0, 1.5)
    assert schedule.provenance.confidence <= 0.50
    assert "x" in schedule.provenance.note
    assert "y" in schedule.provenance.note


def test_parse_block_schedule_non_dict_attribute_entries():
    schedule = parse_block_schedule(
        {
            "handle": "B2",
            "name": "SCHED",
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "attributes": ["oops", {"tag": "T", "value": "V"}, 42],
        },
        {"name": "SCHED"},
    )

    assert schedule.attribute_tags == ["T"]
    assert schedule.attribute_values == ["V"]
    assert schedule.provenance.confidence <= 0.50
    assert "attribute" in schedule.provenance.note


def test_parse_block_schedule_non_list_attributes():
    schedule = parse_block_schedule(
        {"handle": "B3", "name": "SCHED", "attributes": "oops"},
        {"name": "SCHED"},
    )

    assert schedule.attribute_tags == []
    assert schedule.attribute_values == []
    assert schedule.provenance.confidence <= 0.50
    assert schedule.provenance.note


def test_parse_table_entity_non_dict_data():
    entity = parse_table_entity({"layer": "L", "handle": "H", "type": "TABLE", "data": "oops"})

    assert entity.data == {}
    assert entity.provenance.confidence <= 0.50
    assert entity.provenance.note


def test_parse_table_entity_non_dict_payload():
    entity = parse_table_entity("oops")

    assert entity.handle == ""
    assert entity.entity_type == ""
    assert entity.provenance.confidence <= 0.50
    assert entity.provenance.note


def test_parse_ole_schedule_non_dict_payload():
    assert parse_ole_schedule("oops") is None


def test_build_schedule_table_string_row():
    entity = parse_table_entity(
        {
            "layer": "L",
            "handle": "H",
            "type": "TABLE",
            "data": {"name": "T", "headers": ["A", "B"], "rows": ["hello"]},
        }
    )

    table = build_schedule_table([entity], [], [], "S1", "R1")

    assert len(table.rows) == 1
    assert [c.value for c in table.rows[0].cells] == ["hello"]
    assert table.rows[0].provenance.confidence <= 0.50
    assert table.rows[0].provenance.note
    assert table.provenance.note


def test_parse_schedules_malformed_block_coordinates():
    drawing = {
        "table_entities": [],
        "block_refs": [
            {
                "block_name": "SCHEDULE_BLOCK",
                "handle": "B1",
                "x": "N/A",
                "y": "N/A",
                "z": "N/A",
                "attributes": [{"tag": "ROOM", "value": "101"}],
            }
        ],
        "block_defs": {"SCHEDULE_BLOCK": {"name": "SCHEDULE_BLOCK"}},
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_1", "r1")

    assert tables
    cells = list(_all_cells(tables))
    assert cells
    assert all(c.provenance.confidence <= 0.50 for c in cells)
    assert any(c.provenance.note for c in cells)
    assert tables[0].provenance.note
    assert tables[0].provenance.confidence <= 0.50


def test_parse_schedules_malformed_attribute_entries():
    drawing = {
        "table_entities": [],
        "block_refs": [
            {
                "block_name": "SCHEDULE_BLOCK",
                "handle": "B2",
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "attributes": ["oops"],
            }
        ],
        "block_defs": {"SCHEDULE_BLOCK": {"name": "SCHEDULE_BLOCK"}},
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_1", "r1")

    assert tables
    assert tables[0].provenance.note
    assert tables[0].provenance.confidence <= 0.50


def test_parse_schedules_non_dict_entity_data():
    drawing = {
        "table_entities": [
            {"layer": "L", "handle": "H", "type": "TABLE", "data": "oops"},
            "oops",
        ],
        "block_refs": [],
        "block_defs": {},
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_1", "r1")

    assert tables
    assert all(t.provenance.confidence <= 0.50 for t in tables)
    assert any(t.provenance.note for t in tables)


def test_parse_schedules_string_valued_rows():
    drawing = {
        "table_entities": [
            {
                "layer": "L",
                "handle": "H",
                "type": "TABLE",
                "data": {"name": "T", "headers": ["A", "B"], "rows": ["hello", ["ok", "fine"]]},
            }
        ],
        "block_refs": [],
        "block_defs": {},
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_1", "r1")

    assert len(tables) == 1
    values = [c.value for c in _all_cells(tables)]
    assert "hello" in values
    assert "o" not in values  # a string row is not exploded into characters
    assert tables[0].provenance.note
    assert tables[0].provenance.confidence <= 0.50


def test_parse_schedules_missing_block_defs():
    drawing = {
        "table_entities": [],
        "block_refs": [
            {
                "block_name": "SCHEDULE_BLOCK",
                "handle": "B3",
                "x": 1.0,
                "y": 2.0,
                "z": 0.0,
                "attributes": [{"tag": "ROOM", "value": "101"}],
            }
        ],
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_1", "r1")

    assert tables
    cells = list(_all_cells(tables))
    assert cells
    assert all(c.provenance.confidence <= 0.50 for c in cells)
    assert any("block definitions" in c.provenance.note for c in cells)


def test_parse_schedules_never_raises_on_hostile_drawing():
    hostile = {
        "table_entities": [
            None,
            "oops",
            {"type": "TABLE", "data": {"headers": "not-a-list", "rows": "not-a-list"}},
            {"type": "TABLE", "data": {"headers": ["A"], "rows": [None, 5, {"a": 1}]}},
        ],
        "block_refs": [None, "oops", {"block_name": 42, "attributes": {"a": 1}}],
        "block_defs": None,
        "ole_entities": [None, "oops", {"type": "Excel", "data": {"rows": ["x"]}}],
    }

    tables = parse_schedules(hostile, "sheet_1", "r1")

    assert isinstance(tables, list)
    for table in tables:
        assert 0.0 <= table.provenance.confidence <= 1.0
        for row in table.rows:
            assert 0.0 <= row.provenance.confidence <= 1.0
            for cell in row.cells:
                assert 0.0 <= cell.provenance.confidence <= 1.0
