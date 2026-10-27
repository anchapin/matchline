from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from building_model import Provenance


@dataclass
class ScheduleCell:
    value: str
    row: int
    col: int
    provenance: Provenance


@dataclass
class ScheduleRow:
    cells: list[ScheduleCell]
    row_index: int
    provenance: Provenance


@dataclass
class ScheduleTable:
    name: str
    headers: list[str]
    rows: list[ScheduleRow]
    source_entity: str | None
    provenance: Provenance


@dataclass
class TableEntity:
    layer: str
    handle: str
    entity_type: str
    owner_handle: str
    data: dict[str, Any]
    provenance: Provenance


@dataclass
class BlockSchedule:
    block_name: str
    attribute_tags: list[str]
    attribute_values: list[str]
    insertion_point: tuple[float, float, float]
    handle: str
    provenance: Provenance


@dataclass
class OleSchedule:
    ole_type: str
    data: Any
    source_entity: str | None
    provenance: Provenance


def parse_table_entity(entity_data: dict[str, Any]) -> TableEntity:
    layer = entity_data.get("layer", "")
    handle = entity_data.get("handle", "")
    entity_type = entity_data.get("type", entity_data.get("entity_type", ""))
    owner_handle = entity_data.get("owner_handle", "")
    data = entity_data.get("data", {})

    method = "dwg_table_entity"
    confidence = 0.95 if handle else 0.70

    provenance = Provenance(
        sheet_id="",
        revision="",
        method=method,
        confidence=confidence,
        note="",
    )
    return TableEntity(
        layer=layer,
        handle=handle,
        entity_type=entity_type,
        owner_handle=owner_handle,
        data=data,
        provenance=provenance,
    )


def parse_block_schedule(block_ref: dict[str, Any], block_def: dict[str, Any]) -> BlockSchedule:
    block_name = block_def.get("name", block_ref.get("name", ""))
    handle = block_ref.get("handle", "")

    x = block_ref.get("x", 0.0)
    y = block_ref.get("y", 0.0)
    z = block_ref.get("z", 0.0)
    insertion_point = (float(x), float(y), float(z))

    attrs = block_ref.get("attributes", [])
    attribute_tags = [a.get("tag", "") for a in attrs]
    attribute_values = [a.get("value", "") for a in attrs]

    method = "dwg_block_schedule"
    confidence = 0.90 if attribute_values else 0.75

    provenance = Provenance(
        sheet_id="",
        revision="",
        method=method,
        confidence=confidence,
        note="",
    )
    return BlockSchedule(
        block_name=block_name,
        attribute_tags=attribute_tags,
        attribute_values=attribute_values,
        insertion_point=insertion_point,
        handle=handle,
        provenance=provenance,
    )


def parse_ole_schedule(ole_data: dict[str, Any]) -> OleSchedule | None:
    ole_type = ole_data.get("type", "")
    if not ole_type:
        return None

    data = ole_data.get("data")
    source_entity = ole_data.get("source_entity")

    method = "ole_spreadsheet"
    confidence = 0.60

    provenance = Provenance(
        sheet_id="",
        revision="",
        method=method,
        confidence=confidence,
        note="OLE parsing not fully available",
    )
    return OleSchedule(
        ole_type=ole_type,
        data=data,
        source_entity=source_entity,
        provenance=provenance,
    )


def _cell_provenance(
    sheet_id: str, revision: str, method: str, base_confidence: float
) -> Provenance:
    return Provenance(
        sheet_id=sheet_id,
        revision=revision,
        method=method,
        confidence=base_confidence,
        note="",
    )


def build_schedule_table(
    entities: list[TableEntity],
    blocks: list[BlockSchedule],
    ole_schedules: list[OleSchedule | None],
    sheet_id: str,
    revision: str,
) -> ScheduleTable:
    headers: list[str] = []
    rows: list[ScheduleRow] = []

    for entity in entities:
        if entity.entity_type == "TABLE":
            table_data = entity.data
            headers = table_data.get("headers", [])
            raw_rows = table_data.get("rows", [])

            row_offset = len(rows)
            for ri, raw_row in enumerate(raw_rows):
                row_index = row_offset + ri
                cells = []
                for ci, cell_value in enumerate(raw_row):
                    prov = _cell_provenance(
                        sheet_id, revision, "dwg_table_entity", entity.provenance.confidence
                    )
                    cells.append(
                        ScheduleCell(
                            value=str(cell_value),
                            row=row_index,
                            col=ci,
                            provenance=prov,
                        )
                    )
                row_prov = _cell_provenance(
                    sheet_id, revision, "dwg_table_entity", entity.provenance.confidence
                )
                rows.append(ScheduleRow(cells=cells, row_index=row_index, provenance=row_prov))

    for block in blocks:
        if "SCHEDULE" in block.block_name.upper() or block.attribute_tags:
            if not headers and block.attribute_tags:
                headers = block.attribute_tags

            row_index = len(rows)
            cells = []
            for ci, val in enumerate(block.attribute_values):
                prov = _cell_provenance(
                    sheet_id, revision, "dwg_block_schedule", block.provenance.confidence
                )
                cells.append(
                    ScheduleCell(
                        value=val,
                        row=row_index,
                        col=ci,
                        provenance=prov,
                    )
                )
            row_prov = _cell_provenance(
                sheet_id, revision, "dwg_block_schedule", block.provenance.confidence
            )
            rows.append(ScheduleRow(cells=cells, row_index=row_index, provenance=row_prov))

    for ole in ole_schedules:
        if ole is None:
            continue
        if ole.data and isinstance(ole.data, dict):
            table_data = ole.data
            headers = table_data.get("headers", headers)
            raw_rows = table_data.get("rows", [])

            row_offset = len(rows)
            for ri, raw_row in enumerate(raw_rows):
                row_index = row_offset + ri
                cells = []
                for ci, cell_value in enumerate(raw_row):
                    prov = _cell_provenance(
                        sheet_id, revision, "ole_spreadsheet", ole.provenance.confidence
                    )
                    cells.append(
                        ScheduleCell(
                            value=str(cell_value),
                            row=row_index,
                            col=ci,
                            provenance=prov,
                        )
                    )
                row_prov = _cell_provenance(
                    sheet_id, revision, "ole_spreadsheet", ole.provenance.confidence
                )
                rows.append(ScheduleRow(cells=cells, row_index=row_index, provenance=row_prov))

    table_name = ""
    for entity in entities:
        if entity.entity_type == "TABLE":
            table_name = entity.data.get("name", "")
            break

    method = "dwg_table_entity"
    confidence = 0.95 if entities else (0.90 if blocks else 0.60)
    provenance = Provenance(
        sheet_id=sheet_id,
        revision=revision,
        method=method,
        confidence=confidence,
        note="",
    )

    return ScheduleTable(
        name=table_name,
        headers=headers,
        rows=rows,
        source_entity=None,
        provenance=provenance,
    )


_GENERIC_NAME_TOKENS = {"SCHEDULE", "TABLE", "LEGEND", "TAG", "BLOCK"}


def _name_tokens(name: str) -> set[str]:
    """Split a schedule/table name into comparable uppercase tokens."""
    parts = re.split(r"[^A-Za-z0-9]+", name or "")
    return {p.upper() for p in parts if p}


def _distinctive_tokens(name: str) -> set[str]:
    """Tokens of a name that actually identify the schedule (not generic words)."""
    return _name_tokens(name) - _GENERIC_NAME_TOKENS


def _assign_source(
    table_entities: list[TableEntity],
    name: str,
    handles: tuple[str, ...] = (),
    tags: list[str] | None = None,
) -> int:
    """Return the index of the TABLE entity a secondary source belongs to.

    Matching order: explicit handle (owner) reference, then distinctive name
    token overlap, then attribute-tag overlap with the table headers.
    Returns -1 when no table matches.
    """
    if not table_entities:
        return -1

    for handle in handles:
        if not handle:
            continue
        for idx, entity in enumerate(table_entities):
            if entity.handle == handle or entity.owner_handle == handle:
                return idx

    tokens = _distinctive_tokens(name)
    if tokens:
        best_idx = -1
        best_score = 0
        for idx, entity in enumerate(table_entities):
            table_name = str(entity.data.get("name", ""))
            overlap = tokens & _distinctive_tokens(table_name)
            if len(overlap) > best_score:
                best_score = len(overlap)
                best_idx = idx
        if best_idx >= 0:
            return best_idx

    if tags:
        tag_set = {t.strip().lower() for t in tags if t.strip()}
        if tag_set:
            best_idx = -1
            best_score = 0
            for idx, entity in enumerate(table_entities):
                headers = {
                    str(h).strip().lower() for h in entity.data.get("headers", []) if str(h).strip()
                }
                score = len(tag_set & headers)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            if best_idx >= 0:
                return best_idx

    return -1


def parse_schedules(
    drawing: dict[str, Any],
    sheet_id: str,
    revision: str,
) -> list[ScheduleTable]:
    entities_data = drawing.get("table_entities", [])
    blocks_data = drawing.get("block_refs", [])
    ole_data = drawing.get("ole_entities", [])

    entities = [parse_table_entity(e) for e in entities_data]

    blocks = []
    for b in blocks_data:
        block_def = drawing.get("block_defs", {}).get(b.get("block_name", ""), {})
        blocks.append(parse_block_schedule(b, block_def))

    ole_schedules = [parse_ole_schedule(o) for o in ole_data]

    tables: list[ScheduleTable] = []

    table_entities = [e for e in entities if e.entity_type == "TABLE"]

    if not table_entities:
        # No TABLE entity on the sheet: keep the legacy single combined table
        # built from blocks and OLE schedules only.
        if blocks or ole_schedules:
            table = build_schedule_table([], blocks, ole_schedules, sheet_id, revision)
            tables.append(table)
    else:
        # One ScheduleTable per TABLE entity.  Blocks / OLE schedules that
        # belong to a table (its attribute blocks) are combined with that
        # table; unmatched secondary sources are kept with the first table so
        # no data is dropped.
        block_groups: list[list[BlockSchedule]] = [[] for _ in table_entities]
        ole_groups: list[list[OleSchedule | None]] = [[] for _ in table_entities]

        for block in blocks:
            idx = _assign_source(
                table_entities,
                block.block_name,
                handles=(block.handle,),
                tags=block.attribute_tags,
            )
            if idx < 0:
                idx = 0
            block_groups[idx].append(block)

        for ole in ole_schedules:
            if ole is None:
                continue
            ole_name = ""
            if isinstance(ole.data, dict):
                ole_name = str(ole.data.get("name", ""))
            idx = _assign_source(
                table_entities,
                ole_name,
                handles=(ole.source_entity or "",),
            )
            if idx < 0:
                idx = 0
            ole_groups[idx].append(ole)

        for idx, entity in enumerate(table_entities):
            table = build_schedule_table(
                [entity], block_groups[idx], ole_groups[idx], sheet_id, revision
            )
            entity_headers = entity.data.get("headers") or []
            ole_has_headers = any(
                o is not None and isinstance(o.data, dict) and (o.data.get("headers") or [])
                for o in ole_groups[idx]
            )
            if len(table_entities) > 1 and entity_headers and not ole_has_headers:
                # Each table keeps its own headers; never inherit another
                # table's header set.
                table.headers = list(entity_headers)
            tables.append(table)

    for entity in entities:
        if entity.entity_type != "TABLE":
            table = build_schedule_table([entity], [], [], sheet_id, revision)
            tables.append(table)

    return tables


__all__ = [
    "ScheduleCell",
    "ScheduleRow",
    "ScheduleTable",
    "TableEntity",
    "BlockSchedule",
    "OleSchedule",
    "parse_table_entity",
    "parse_block_schedule",
    "parse_ole_schedule",
    "build_schedule_table",
    "parse_schedules",
]
