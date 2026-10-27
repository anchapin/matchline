from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from building_model import Provenance

# Confidence ceiling applied to any fact that carries a malformed-input note.
_DEFECT_CONFIDENCE = 0.50


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


def _coerce_float(value: Any) -> float | None:
    """Return float(value), or None when the value is not a usable number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None


def _defect_note(defects: list[str]) -> str:
    """Join defect descriptions into a single provenance note."""
    return "; ".join(defects)


def parse_table_entity(entity_data: dict[str, Any]) -> TableEntity:
    defects: list[str] = []
    if not isinstance(entity_data, dict):
        defects.append("entity payload is not a mapping")
        entity_data = {}

    layer = entity_data.get("layer", "")
    handle = entity_data.get("handle", "")
    entity_type = entity_data.get("type", entity_data.get("entity_type", ""))
    owner_handle = entity_data.get("owner_handle", "")
    data = entity_data.get("data", {})
    if not isinstance(data, dict):
        defects.append("table data is not a mapping")
        data = {}

    method = "dwg_table_entity"
    confidence = 0.95 if handle else 0.70
    if defects:
        confidence = min(confidence, _DEFECT_CONFIDENCE)

    provenance = Provenance(
        sheet_id="",
        revision="",
        method=method,
        confidence=confidence,
        note=_defect_note(defects),
    )
    return TableEntity(
        layer=layer,
        handle=handle,
        entity_type=entity_type,
        owner_handle=owner_handle,
        data=data,
        provenance=provenance,
    )


def parse_block_schedule(
    block_ref: dict[str, Any],
    block_def: dict[str, Any],
    extra_defects: list[str] | None = None,
) -> BlockSchedule:
    defects: list[str] = list(extra_defects or [])
    if not isinstance(block_ref, dict):
        defects.append("block reference is not a mapping")
        block_ref = {}
    if not isinstance(block_def, dict):
        defects.append("block definition is not a mapping")
        block_def = {}

    block_name = block_def.get("name", block_ref.get("name", ""))
    handle = block_ref.get("handle", "")

    coordinates: list[float] = []
    for axis in ("x", "y", "z"):
        raw = block_ref.get(axis, 0.0)
        value = _coerce_float(raw)
        if value is None:
            defects.append(f"non-numeric {axis} coordinate")
            value = 0.0
        coordinates.append(value)
    insertion_point = (coordinates[0], coordinates[1], coordinates[2])

    attrs = block_ref.get("attributes", [])
    if not isinstance(attrs, list):
        defects.append("attributes is not a list")
        attrs = []
    attribute_tags: list[Any] = []
    attribute_values: list[Any] = []
    for attribute in attrs:
        if not isinstance(attribute, dict):
            defects.append("non-mapping attribute entry skipped")
            continue
        attribute_tags.append(attribute.get("tag", ""))
        attribute_values.append(attribute.get("value", ""))

    method = "dwg_block_schedule"
    confidence = 0.90 if attribute_values else 0.75
    if defects:
        confidence = min(confidence, _DEFECT_CONFIDENCE)

    provenance = Provenance(
        sheet_id="",
        revision="",
        method=method,
        confidence=confidence,
        note=_defect_note(defects),
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
    defects: list[str] = []
    if not isinstance(ole_data, dict):
        return None

    ole_type = ole_data.get("type", "")
    if not ole_type:
        return None
    if not isinstance(ole_type, str):
        defects.append("ole type is not a string")
        ole_type = str(ole_type)

    data = ole_data.get("data")
    source_entity = ole_data.get("source_entity")

    method = "ole_spreadsheet"
    confidence = 0.60
    if defects:
        confidence = min(confidence, _DEFECT_CONFIDENCE)

    note = "OLE parsing not fully available"
    if defects:
        note = f"{note}; {_defect_note(defects)}"
    provenance = Provenance(
        sheet_id="",
        revision="",
        method=method,
        confidence=confidence,
        note=note,
    )
    return OleSchedule(
        ole_type=ole_type,
        data=data,
        source_entity=source_entity,
        provenance=provenance,
    )


def _cell_provenance(
    sheet_id: str,
    revision: str,
    method: str,
    base_confidence: float,
    note: str = "",
) -> Provenance:
    return Provenance(
        sheet_id=sheet_id,
        revision=revision,
        method=method,
        confidence=base_confidence,
        note=note,
    )


def _build_row_cells(
    raw_row: Any,
    ri: int,
    sheet_id: str,
    revision: str,
    method: str,
    base_confidence: float,
) -> tuple[list[ScheduleCell], list[str]]:
    """Turn one raw row into cells, degrading malformed rows instead of raising."""
    defects: list[str] = []
    if isinstance(raw_row, (list, tuple)):
        values: list[Any] = list(raw_row)
    elif isinstance(raw_row, str):
        defects.append(f"row {ri} is a string; kept as a single cell")
        values = [raw_row]
    elif raw_row is None:
        defects.append(f"row {ri} is empty; skipped")
        values = []
    else:
        defects.append(f"row {ri} is not a sequence; kept as a single cell")
        values = [raw_row]

    confidence = min(base_confidence, _DEFECT_CONFIDENCE) if defects else base_confidence
    note = _defect_note(defects)

    cells: list[ScheduleCell] = []
    for ci, cell_value in enumerate(values):
        prov = _cell_provenance(sheet_id, revision, method, confidence, note)
        cells.append(
            ScheduleCell(
                value=str(cell_value),
                row=ri,
                col=ci,
                provenance=prov,
            )
        )
    return cells, defects


def build_schedule_table(
    entities: list[TableEntity],
    blocks: list[BlockSchedule],
    ole_schedules: list[OleSchedule | None],
    sheet_id: str,
    revision: str,
) -> ScheduleTable:
    headers: list[str] = []
    rows: list[ScheduleRow] = []
    defects: list[str] = []

    if not isinstance(entities, (list, tuple)):
        entities = []
    if not isinstance(blocks, (list, tuple)):
        blocks = []
    if not isinstance(ole_schedules, (list, tuple)):
        ole_schedules = []

    for entity in entities:
        entity_defects: list[str] = []
        entity_note = getattr(getattr(entity, "provenance", None), "note", "") or ""
        if entity_note:
            entity_defects.append(entity_note)
        if getattr(entity, "entity_type", "") != "TABLE":
            defects.extend(entity_defects)
            continue
        entity_data = getattr(entity, "data", None)
        table_data = entity_data if isinstance(entity_data, dict) else {}
        if not isinstance(entity_data, dict):
            entity_defects.append("table entity data is not a mapping")
        raw_headers = table_data.get("headers", [])
        if not isinstance(raw_headers, list):
            entity_defects.append("table headers is not a list")
            raw_headers = []
        headers = list(raw_headers)
        raw_rows = table_data.get("rows", [])
        if not isinstance(raw_rows, list):
            entity_defects.append("table rows is not a list")
            raw_rows = []

        base_confidence = getattr(getattr(entity, "provenance", None), "confidence", 0.70)
        for ri, raw_row in enumerate(raw_rows):
            cells, row_defects = _build_row_cells(
                raw_row,
                ri,
                sheet_id,
                revision,
                "dwg_table_entity",
                base_confidence,
            )
            row_prov = _cell_provenance(
                sheet_id,
                revision,
                "dwg_table_entity",
                min(base_confidence, _DEFECT_CONFIDENCE) if row_defects else base_confidence,
                _defect_note(row_defects),
            )
            rows.append(ScheduleRow(cells=cells, row_index=ri, provenance=row_prov))
            defects.extend(row_defects)
        defects.extend(entity_defects)

    for block in blocks:
        block_name = getattr(block, "block_name", "") or ""
        attribute_tags = getattr(block, "attribute_tags", []) or []
        attribute_values = getattr(block, "attribute_values", []) or []
        if "SCHEDULE" in str(block_name).upper() or attribute_tags:
            if not headers and attribute_tags:
                headers = list(attribute_tags)

            block_provenance = getattr(block, "provenance", None)
            block_note = getattr(block_provenance, "note", "") or ""
            block_confidence = getattr(block_provenance, "confidence", 0.75)
            if block_note:
                defects.append(block_note)

            cells = []
            for ci, val in enumerate(attribute_values):
                prov = _cell_provenance(
                    sheet_id,
                    revision,
                    "dwg_block_schedule",
                    block_confidence,
                    block_note,
                )
                cells.append(
                    ScheduleCell(
                        value=val,
                        row=0,
                        col=ci,
                        provenance=prov,
                    )
                )
            row_prov = _cell_provenance(
                sheet_id,
                revision,
                "dwg_block_schedule",
                block_confidence,
                block_note,
            )
            rows.append(ScheduleRow(cells=cells, row_index=len(rows), provenance=row_prov))

    for ole in ole_schedules:
        if ole is None:
            continue
        if ole.data and isinstance(ole.data, dict):
            table_data = ole.data
            raw_headers = table_data.get("headers", headers)
            if not isinstance(raw_headers, list):
                defects.append("ole headers is not a list")
                raw_headers = []
            headers = list(raw_headers)
            raw_rows = table_data.get("rows", [])
            if not isinstance(raw_rows, list):
                defects.append("ole rows is not a list")
                raw_rows = []

            base_confidence = getattr(getattr(ole, "provenance", None), "confidence", 0.60)
            for ri, raw_row in enumerate(raw_rows):
                cells, row_defects = _build_row_cells(
                    raw_row,
                    ri,
                    sheet_id,
                    revision,
                    "ole_spreadsheet",
                    base_confidence,
                )
                row_prov = _cell_provenance(
                    sheet_id,
                    revision,
                    "ole_spreadsheet",
                    min(base_confidence, _DEFECT_CONFIDENCE) if row_defects else base_confidence,
                    _defect_note(row_defects),
                )
                rows.append(ScheduleRow(cells=cells, row_index=len(rows), provenance=row_prov))
                defects.extend(row_defects)

    table_name = ""
    for entity in entities:
        if getattr(entity, "entity_type", "") == "TABLE":
            entity_data = getattr(entity, "data", None)
            table_data = entity_data if isinstance(entity_data, dict) else {}
            table_name = table_data.get("name", "")
            break

    method = "dwg_table_entity"
    confidence = 0.95 if entities else (0.90 if blocks else 0.60)
    if defects:
        confidence = min(confidence, _DEFECT_CONFIDENCE)

    provenance = Provenance(
        sheet_id=sheet_id,
        revision=revision,
        method=method,
        confidence=confidence,
        note=_defect_note(defects),
    )

    return ScheduleTable(
        name=table_name,
        headers=headers,
        rows=rows,
        source_entity=None,
        provenance=provenance,
    )


def parse_schedules(
    drawing: dict[str, Any],
    sheet_id: str,
    revision: str,
) -> list[ScheduleTable]:
    if not isinstance(drawing, dict):
        return []

    entities_data = drawing.get("table_entities", [])
    blocks_data = drawing.get("block_refs", [])
    ole_data = drawing.get("ole_entities", [])
    if not isinstance(entities_data, (list, tuple)):
        entities_data = []
    if not isinstance(blocks_data, (list, tuple)):
        blocks_data = []
    if not isinstance(ole_data, (list, tuple)):
        ole_data = []

    entities = [parse_table_entity(e) for e in entities_data]

    block_defs = drawing.get("block_defs")
    defs_missing = not isinstance(block_defs, dict)
    defs = block_defs if isinstance(block_defs, dict) else {}

    blocks = []
    for b in blocks_data:
        block_name = b.get("block_name", "") if isinstance(b, dict) else ""
        if not isinstance(block_name, str):
            block_name = ""
        block_def = defs.get(block_name, {})
        extra_defects = ["block definitions unavailable"] if defs_missing else []
        blocks.append(parse_block_schedule(b, block_def, extra_defects))

    ole_schedules = [parse_ole_schedule(o) for o in ole_data]

    tables: list[ScheduleTable] = []

    table_entities = [e for e in entities if e.entity_type == "TABLE"]
    if table_entities or blocks or ole_schedules:
        table = build_schedule_table(table_entities, blocks, ole_schedules, sheet_id, revision)
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
