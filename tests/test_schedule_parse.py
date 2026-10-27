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


def _two_table_drawing():
    return {
        "table_entities": [
            {
                "layer": "A_SCHEDS",
                "handle": "TBL001",
                "type": "TABLE",
                "owner_handle": "",
                "data": {
                    "name": "WINDOW SCHEDULE",
                    "headers": ["Mark", "Width"],
                    "rows": [["W1", "1200"], ["W2", "900"]],
                },
            },
            {
                "layer": "A_SCHEDS",
                "handle": "TBL002",
                "type": "TABLE",
                "owner_handle": "",
                "data": {
                    "name": "DOOR SCHEDULE",
                    "headers": ["Tag", "Type"],
                    "rows": [["D1", "Single"], ["D2", "Double"]],
                },
            },
        ],
        "block_refs": [],
        "block_defs": {},
        "ole_entities": [],
    }


def test_parse_schedules_splits_per_table_entity():
    tables = parse_schedules(_two_table_drawing(), "sheet_002", "rev2")

    assert len(tables) == 2

    window, door = tables
    assert window.name == "WINDOW SCHEDULE"
    assert window.headers == ["Mark", "Width"]
    assert door.name == "DOOR SCHEDULE"
    assert door.headers == ["Tag", "Type"]

    assert [r.cells[0].value for r in window.rows] == ["W1", "W2"]
    assert [r.cells[0].value for r in door.rows] == ["D1", "D2"]


def test_parse_schedules_tables_do_not_share_rows():
    tables = parse_schedules(_two_table_drawing(), "sheet_002", "rev2")

    window, door = tables
    window_values = {c.value for r in window.rows for c in r.cells}
    door_values = {c.value for r in door.rows for c in r.cells}

    assert not window_values & door_values


def test_parse_schedules_cell_coords_unique_and_match_row_index():
    tables = parse_schedules(_two_table_drawing(), "sheet_002", "rev2")

    for table in tables:
        coords = set()
        for row in table.rows:
            assert row.row_index == table.rows.index(row)
            for cell in row.cells:
                assert cell.row == row.row_index
                assert (cell.row, cell.col) not in coords
                coords.add((cell.row, cell.col))


def test_parse_schedules_block_rows_keep_consistent_coords():
    drawing = {
        "table_entities": [
            {
                "layer": "A_SCHEDS",
                "handle": "TBL001",
                "type": "TABLE",
                "owner_handle": "",
                "data": {
                    "name": "WINDOW SCHEDULE",
                    "headers": ["Mark", "Width"],
                    "rows": [["W1", "1200"]],
                },
            }
        ],
        "block_refs": [
            {
                "handle": "BLK001",
                "name": "WINDOW_SCHEDULE",
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "attributes": [{"tag": "Mark", "value": "W2"}],
            }
        ],
        "block_defs": {},
        "ole_entities": [],
    }

    tables = parse_schedules(drawing, "sheet_003", "rev3")

    assert len(tables) == 1
    table = tables[0]
    assert len(table.rows) == 2

    coords = set()
    for row in table.rows:
        for cell in row.cells:
            assert cell.row == row.row_index
            assert (cell.row, cell.col) not in coords
            coords.add((cell.row, cell.col))

    block_row = table.rows[-1]
    assert block_row.cells[0].value == "W2"
    assert block_row.cells[0].row == block_row.row_index


def test_parse_schedules_single_table_keeps_combined_sources():
    drawing = {
        "table_entities": [
            {
                "layer": "A_SCHEDS",
                "handle": "TBL001",
                "type": "TABLE",
                "owner_handle": "",
                "data": {
                    "name": "ROOM SCHEDULE",
                    "headers": ["Mark", "Area"],
                    "rows": [["101", "25"]],
                },
            }
        ],
        "block_refs": [
            {
                "handle": "BLK001",
                "name": "ROOM_SCHEDULE",
                "x": 0.0,
                "y": 0.0,
                "z": 0.0,
                "attributes": [{"tag": "Mark", "value": "102"}],
            }
        ],
        "block_defs": {},
        "ole_entities": [
            {
                "type": "Excel",
                "data": {"headers": ["Mark", "Area"], "rows": [["103", "30"]]},
                "source_entity": "TBL001",
            }
        ],
    }

    tables = parse_schedules(drawing, "sheet_004", "rev4")

    assert len(tables) == 1
    table = tables[0]
    assert table.name == "ROOM SCHEDULE"
    assert table.headers == ["Mark", "Area"]
    assert [r.cells[0].value for r in table.rows] == ["101", "102", "103"]


def test_build_schedule_table_block_cell_row_matches_row_index():
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
    block = parse_block_schedule(block_ref, {"name": "ROOM_SCHEDULE"})

    table = build_schedule_table([], [block], [], "sheet_A101", "2")

    assert len(table.rows) == 1
    row = table.rows[0]
    for cell in row.cells:
        assert cell.row == row.row_index
