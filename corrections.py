"""Correction history for accepted review decisions (#997).

Every accepted correction (a review item confirmed, rejected, edited or
reverted) is recorded once, with before/after values, the source that
accepted it (auto or human) and a UTC timestamp, in ``corrections.json``
beside the model: the project dir. The history is written once by the
review workflow and read by the export, so it survives re-runs of later
stages. ``matchline review --undo N`` takes back the last N corrections
and ``--revert-to-auto KIND:ID:FIELD`` puts one field back to the
automatic output; both are recorded in the same file as undone entries.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA = "matchline.corrections/1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_for(model_path) -> Path:
    """Where the history lives: beside the model, in the project dir."""
    return Path(model_path).parent / "corrections.json"


def empty_doc() -> dict:
    return {"schema": SCHEMA, "corrections": []}


def read(path) -> dict:
    """Load a corrections file by its own path."""
    p = Path(path)
    if not p.exists():
        return empty_doc()
    doc = json.loads(p.read_text())
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"{p.name}: unknown schema {doc.get('schema')!r}, want {SCHEMA}")
    if not isinstance(doc.get("corrections"), list):
        raise ValueError(f"{p.name}: corrections must be a list")
    return doc


def load(model_path) -> dict:
    """The history for the model beside ``model_path``, or an empty one."""
    return read(path_for(model_path))


def save(model_path, doc: dict) -> Path:
    p = path_for(model_path)
    p.write_text(json.dumps(doc, indent=2) + "\n")
    return p


def field_key(target: Optional[dict], field: str) -> str:
    """The undo/revert key for one target field: ``kind:id:field``."""
    t = target or {}
    return f"{t.get('kind', '')}:{t.get('id', '')}:{field}"


def record(
    model,
    item,
    action: str,
    source: str,
    model_path,
    field: str = "",
    before=None,
    after=None,
    state_before: Optional[dict] = None,
    doc: Optional[dict] = None,
) -> dict:
    """Append one accepted correction to the history and save it.

    ``before``/``after`` carry the field values when the decision wrote
    one; ``state_before`` is the review item's own state before the
    decision, so an undo can put it back.
    """
    if doc is None:
        doc = load(model_path)
    t = dict(item.target or {})
    entry = {
        "seq": len(doc["corrections"]) + 1,
        "ts": _now(),
        "item_id": item.id,
        "kind": getattr(item, "kind", ""),
        "target": t,
        "field": field,
        "before": before,
        "after": after,
        "action": action,
        "source": source,
        "state_before": dict(state_before or {}),
        "undone": False,
    }
    doc["corrections"].append(entry)
    save(model_path, doc)
    return entry


def _item_of(model, item_id: str):
    return next((i for i in model.review_queue if i.id == item_id), None)


def _sheet_rev(model, item) -> tuple:
    if item is not None and item.provenance:
        return item.provenance.sheet_id, int(getattr(item.provenance, "revision", 0) or 0)
    return "", 0


def undo(model, model_path, n: int = 1, doc: Optional[dict] = None) -> List[dict]:
    """Take back the last ``n`` corrections, newest first; returns them.

    Each undone correction gets its field value and the review item's
    state put back to what the entry recorded, and is marked ``undone``
    in the history (nothing is deleted). Fewer than ``n`` corrections
    simply undoes what is there.
    """
    from review_report import _write

    if doc is None:
        doc = load(model_path)
    active = [c for c in doc["corrections"] if not c.get("undone")]
    batch = active[-n:] if n > 0 else []
    undone: List[dict] = []
    for c in reversed(batch):
        t = dict(c.get("target") or {})
        f = c.get("field") or ""
        if f and "before" in c:
            _write(model, t, f, c.get("before"))
        item = _item_of(model, c.get("item_id", ""))
        if item is not None:
            for k, v in (c.get("state_before") or {}).items():
                if hasattr(item, k):
                    setattr(item, k, v)
        sheet, rev = _sheet_rev(model, item)
        note = f"undo {c.get('item_id', '')} {c.get('action', '')}"
        model.log_revision(sheet, rev, "review", note)
        c["undone"] = True
        c["undone_ts"] = _now()
        undone.append(c)
    if undone:
        save(model_path, doc)
    return undone


def revert_to_automatic(model, model_path, key: str, doc: Optional[dict] = None) -> dict:
    """Put one field back to the automatic output; returns what changed.

    The automatic value is what the earliest still-standing correction on
    that field read before it wrote: the pipeline's output. Every
    correction on the field is marked undone with the reason, so the
    history keeps the audit trail.
    """
    from review_report import _read, _write

    if doc is None:
        doc = load(model_path)
    active = [
        c
        for c in doc["corrections"]
        if not c.get("undone")
        and c.get("field")
        and field_key(c.get("target") or {}, c["field"]) == key
    ]
    if not active:
        raise ValueError(f"no accepted correction for field {key!r}")
    first = active[0]
    t = dict(first.get("target") or {})
    f = first["field"]
    auto_value = first.get("before")
    current = _read(model, t, f)
    if current != auto_value:
        _write(model, t, f, auto_value)
    for c in active:
        c["undone"] = True
        c["undone_reason"] = "revert-to-automatic"
        c["undone_ts"] = _now()
    item = _item_of(model, active[-1].get("item_id", ""))
    sheet, rev = _sheet_rev(model, item)
    model.log_revision(
        sheet, rev, "review", f"revert-to-automatic {key} ({current!r} -> {auto_value!r})"
    )
    save(model_path, doc)
    return {"field": key, "from": current, "to": auto_value, "reverted": len(active)}


def summary(doc: dict) -> Dict[str, int]:
    """Counts the trust report and the CLI listing show."""
    cs = doc.get("corrections", [])
    return {
        "total": len(cs),
        "undone": sum(1 for c in cs if c.get("undone")),
        "human": sum(1 for c in cs if c.get("source") == "human"),
        "auto": sum(1 for c in cs if c.get("source") == "auto"),
    }
