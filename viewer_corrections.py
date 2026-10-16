"""In-viewer corrections (#1002): the ops file the viewer page writes out.

Finding a mistake in the review viewer (#1001) is half the loop; this module
is the other half. The viewer stays read-only in the strict sense the
acceptance demands: it never forks a private copy of the model, and it never
applies an edit itself. Correct mode collects ops (rename a space, reclassify
a door as a window or the reverse, merge or split spaces, revert one field to
the automatic output) into one JSON file the reviewer saves; the CLI applies
it to the canonical model through the EXISTING decisions workflow:

``matchline viewer-apply model.json viewer_corrections.json`` maps every op to
a review item and writes its decision through ``review_report.apply_decisions``
(the same path ``--confirm``/``--reject``/``--apply`` use), so each accepted
correction lands in ``review_report``'s state machine and in the correction
history (#997) with before/after values, source ``human`` and a timestamp, and
surfaces in the review queue (#996). ``revert_to_auto`` reuses the
``matchline review --revert-to-auto`` path in ``corrections`` verbatim.

Ops apply in listed order; a replay of the same file changes nothing and logs
nothing, because every item is already in the decided state it names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List

SCHEMA = "matchline.viewer_corrections/1"

OPS = ("rename_space", "reclassify_opening", "merge_spaces", "split_space", "revert_to_auto")

# The only reclasses the acceptance names. A skylight is a roof opening with
# its own geometry; a door/window reclass does not apply to it.
RECLASS_TARGETS = ("window", "door")
RENAME_FIELDS = ("name", "number")


def read(path) -> dict:
    """Load an ops file by its own path."""
    p = Path(path)
    doc = json.loads(p.read_text())
    check(doc)
    return doc


def check(doc: dict) -> None:
    """Raise ValueError on a malformed ops file (schema, op names, fields)."""
    if doc.get("schema") != SCHEMA:
        raise ValueError(
            f"not a viewer corrections file (schema {doc.get('schema')!r}, want {SCHEMA})"
        )
    ops = doc.get("ops")
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    bad: List[str] = []
    for n, op in enumerate(ops):
        kind = op.get("op")
        if kind not in OPS:
            bad.append(f"op {n}: unknown op {kind!r}")
            continue
        eid = op.get("id")
        if kind == "revert_to_auto":
            if not isinstance(op.get("key"), str) or len(op.get("key", "").split(":")) != 3:
                bad.append(f"op {n}: revert_to_auto needs a kind:id:field key")
            continue
        if not isinstance(eid, str) or not eid:
            bad.append(f"op {n}: {kind} needs an id")
        if kind == "rename_space":
            if op.get("field") not in RENAME_FIELDS:
                bad.append(f"op {n}: rename_space field must be one of {', '.join(RENAME_FIELDS)}")
            if not str(op.get("value", "")).strip():
                bad.append(f"op {n}: rename_space has no value")
        if kind == "reclassify_opening" and op.get("value") not in RECLASS_TARGETS:
            bad.append(
                f"op {n}: reclassify_opening value must be one of {', '.join(RECLASS_TARGETS)}"
            )
        if kind == "merge_spaces" and not str(op.get("value", "")).strip():
            bad.append(f"op {n}: merge_spaces has no target space")
        if kind == "split_space":
            for side in ("a", "b"):
                pt = op.get(side)
                if not (isinstance(pt, (list, tuple)) and len(pt) == 2):
                    bad.append(f"op {n}: split_space needs {side} as [x, y]")
    if bad:
        raise ValueError("; ".join(bad))


def apply_ops(
    model,
    doc: dict,
    model_path,
    source: str = "human",
) -> dict:
    """Apply a viewer ops file to ``model`` in place; returns a summary.

    Every edit op becomes one decision on a review item the model carries
    (an existing one when the pipeline already made it, a new
    ``viewer_correction`` item otherwise), then the whole set replays through
    ``review_report.apply_decisions``: one path, one history, one queue.
    Corrections write beside the model into the correction history (#997).
    A malformed op raises before anything changes.
    """
    import corrections as corrections_mod
    from review_report import apply_decisions

    check(doc)
    # every revert key must resolve against the standing correction history
    # BEFORE anything applies: nothing changes unless every op would (#1002)
    if any(op.get("op") == "revert_to_auto" for op in doc.get("ops", [])):
        cdoc = corrections_mod.load(model_path)
        for n, op in enumerate(doc.get("ops", [])):
            if op.get("op") != "revert_to_auto":
                continue
            key = op["key"]
            active = [
                c
                for c in cdoc["corrections"]
                if not c.get("undone")
                and c.get("field")
                and corrections_mod.field_key(c.get("target") or {}, c["field"]) == key
            ]
            if not active:
                raise ValueError(f"op {n}: no accepted correction for field {key!r}")
    items: List[dict] = []
    for n, op in enumerate(doc.get("ops", [])):
        kind = op["op"]
        if kind == "revert_to_auto":
            continue
        try:
            items.append(_decision_for(model, op))
        except ValueError as e:
            raise ValueError(f"op {n}: {e}") from None
    decisions = {
        "schema": "matchline.review_decisions/1",
        "decisions": items,
    }
    summary = apply_decisions(model, decisions, source=source, corrections_path=model_path)
    reverts = []
    for op in doc.get("ops", []):
        if op.get("op") == "revert_to_auto":
            reverts.append(corrections_mod.revert_to_automatic(model, model_path, op["key"]))
    summary["revert_to_auto"] = reverts
    summary["ops"] = len(doc.get("ops", []))
    return summary


def _decision_for(model, op: dict) -> dict:
    """The decisions-file entry for one viewer op: find or make its item."""
    kind, eid = op["op"], op["id"]
    if kind == "rename_space":
        field = op.get("field", "name")
        _require_space(model, eid)
        target = {"kind": "space", "id": eid, "field": field}
        value = str(op["value"]).strip()
        desc = f"viewer correction: {field} of space {eid} is {value!r}"
        item = _ensure_item(model, kind="viewer_correction", target=target, desc=desc)
        return {"id": item.id, "action": "edit", "value": value}
    if kind == "reclassify_opening":
        val = op["value"]
        _require_opening(model, eid)
        target = {"kind": "opening", "id": eid, "field": "category"}
        desc = f"viewer correction: opening {eid} is a {val}"
        item = _ensure_item(model, kind="viewer_correction", target=target, desc=desc)
        return {"id": item.id, "action": "edit", "value": val}
    if kind == "merge_spaces":
        dst = str(op["value"]).strip()
        _require_space(model, eid)
        target = {"kind": "space", "id": eid, "field": "merge_into"}
        desc = f"viewer correction: merge space {eid} into {dst}"
        item = _ensure_item(model, kind="space_merge", target=target, desc=desc)
        return {"id": item.id, "action": "edit", "value": f"merge into {dst}"}
    if kind == "split_space":
        a, b = op["a"], op["b"]
        _require_space(model, eid)
        target = {"kind": "space", "id": eid, "field": "split"}
        desc = f"viewer correction: split space {eid} along a={a} b={b}"
        item = _ensure_item(model, kind="viewer_correction", target=target, desc=desc)
        return {"id": item.id, "action": "edit", "value": f"a={a[0]},{a[1]};b={b[0]},{b[1]}"}
    raise ValueError(f"unknown op {kind!r}")


def _ensure_item(model, kind: str, target: dict, desc: str):
    """The review item this op writes through: existing match or a new one."""
    from building_model import ReviewItem

    field = target.get("field", "")
    for it in model.review_queue:
        t = it.target or {}
        if (
            it.kind == kind
            and t.get("kind") == target["kind"]
            and t.get("id") == target["id"]
            and (field == "merge_into" or t.get("field", "") == field)
        ):
            return it
    base = f"vc-{kind.split('_')[0]}-{target['id']}"
    item_id, k = base, 1
    taken = {it.id for it in model.review_queue}
    while item_id in taken:
        k += 1
        item_id = f"{base}-{k}"
    item = ReviewItem(
        id=item_id,
        kind=kind,
        description=desc,
        confidence=0.95,
        urgency=1,
    )
    item.target = dict(target)
    model.review_queue.append(item)
    return item


def _require_space(model, sid: str) -> None:
    if sid not in model.spaces:
        raise ValueError(f"room {sid!r} is not a space id in the model")


def _require_opening(model, oid: str) -> None:
    from review_report import _find_opening

    if _find_opening(model, oid)[1] is None:
        raise ValueError(f"opening {oid!r} is not in the model")
