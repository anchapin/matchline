"""Static HTML review report with replayable decisions (#749, first slice).

``write_review(out_dir, model)`` writes ``out/review/``:

- ``model.json``: the BuildingModel the decisions apply to;
- ``decisions.json``: an empty decisions file keyed to that model's SHA-256,
  with the automatic state of every review item (what ``revert`` restores);
- ``review.html``: the review queue worst-first, one page, offline. Inline
  CSS and inline script only, no external assets. Confirm, reject, edit and
  revert are kept in the browser and exported as ``decisions.json``.

``matchline review model.json --apply decisions.json`` replays the file:
decisions run in order, the last one per item wins, ``revert`` returns the
item to its automatic state, and every change the replay makes is written to
the model's revision log. The decisions file itself is the correction
history; applying it never rewrites it.

Sheet overlays (rooms, walls, detections drawn on the sheet) are the next
slice and are not drawn here yet.
"""

from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA = "matchline.review_decisions/1"
ACTIONS = ("confirm", "reject", "edit", "revert")
_STATE = {
    "confirm": {"status": "confirmed", "resolution": "accept"},
    "reject": {"status": "rejected", "resolution": "drop"},
    "edit": {"status": "confirmed", "resolution": "reassign"},
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def auto_state(item) -> dict:
    """The automatic output for one item, as the pipeline left it."""
    return {
        "status": item.status,
        "needs_review": item.needs_review,
        "acknowledged": item.acknowledged,
        "resolution": item.resolution,
    }


# Fields a review edit can write, per target kind (#796). Anything else keeps
# the #749 behaviour: the value is recorded and the item closed.
EDITABLE = {"opening": ("width_m", "space_id", "construction_id")}
MAX_OPENING_WIDTH_M = 30.0


def _find_opening(model, oid):
    for sp in model.spaces.values():
        for op in sp.openings:
            if op.id == oid:
                return sp, op
    return None, None


def edit_field(model, item) -> str:
    """The model field an edit of ``item`` writes, or "" when it only records."""
    t = item.target or {}
    f = t.get("field", "")
    if f not in EDITABLE.get(t.get("kind", ""), ()):
        return ""
    if t["kind"] == "opening" and _find_opening(model, t.get("id"))[1] is None:
        return ""
    return f


def _read(model, kind: str, eid: str, f: str):
    if kind == "opening":
        sp, op = _find_opening(model, eid)
        if op is None:
            return None
        return sp.id if f == "space_id" else getattr(op, f)
    return None


def _split(model, item, value):
    """(field, raw value) for an edit; ``width_m=0.9`` picks another field."""
    t = item.target or {}
    f = edit_field(model, item)
    raw = str(value).strip()
    if f and "=" in raw:
        k, v = (x.strip() for x in raw.split("=", 1))
        if k in EDITABLE.get(t["kind"], ()):
            return k, v
    return f, raw


def _coerce(model, kind: str, f: str, raw: str):
    """Validated value for ``f`` or ValueError saying what is wrong."""
    if f == "width_m":
        try:
            w = float(raw)
        except ValueError:
            raise ValueError(f"width_m {raw!r} is not a number (metres)") from None
        if not 0 < w <= MAX_OPENING_WIDTH_M:
            raise ValueError(f"width_m {w:g} is outside (0, {MAX_OPENING_WIDTH_M:g}] m")
        return w
    if f == "space_id":
        if raw in model.spaces:
            return raw
        hits = [sid for sid, sp in model.spaces.items() if sp.number == raw]
        if len(hits) == 1:
            return hits[0]
        raise ValueError(
            f"room {raw!r} is "
            + (
                "ambiguous (" + ", ".join(sorted(hits)) + ")"
                if hits
                else "not a space id or room number"
            )
        )
    if f == "construction_id":
        if raw in model.constructions:
            return raw
        raise ValueError(f"construction {raw!r} is not in the model")
    return raw


def _write(model, kind: str, eid: str, f: str, value) -> None:
    sp, op = _find_opening(model, eid)
    if f == "space_id":
        if sp.id != value:
            sp.openings.remove(op)
            model.spaces[value].openings.append(op)
        return
    setattr(op, f, value)
    if f == "width_m":
        if op.height_m:
            op.area_m2 = value * op.height_m
        if op.host_interval_m and len(op.host_interval_m) == 2:
            c = op.s_center_m if op.s_center_m is not None else sum(op.host_interval_m) / 2
            op.host_interval_m = [round(c - value / 2, 4), round(c + value / 2, 4)]


def original_value(model, item):
    """What the pipeline wrote to the edited field (what revert restores)."""
    t = item.target or {}
    if "original" in t:
        return t["original"]
    f = edit_field(model, item)
    return _read(model, t.get("kind", ""), t.get("id"), f) if f else None


def worst_first(items) -> list:
    """Open items first, then higher urgency, then lower confidence."""
    return sorted(
        items, key=lambda i: (i.status != "open", -int(i.urgency), float(i.confidence), i.id)
    )


def template(model, model_sha: str) -> dict:
    return {
        "schema": SCHEMA,
        "model_sha256": model_sha,
        "auto": {i.id: auto_state(i) for i in model.review_queue},
        "decisions": [],
    }


def check(doc: dict, ids) -> None:
    """Raise ValueError on a malformed decisions file or unknown item ids."""
    if doc.get("schema") != SCHEMA:
        raise ValueError(f"not a decisions file (schema {doc.get('schema')!r}, want {SCHEMA})")
    known = set(ids)
    bad = []
    for n, d in enumerate(doc.get("decisions", [])):
        if d.get("action") not in ACTIONS:
            bad.append(f"decision {n}: unknown action {d.get('action')!r}")
        if d.get("id") not in known:
            bad.append(f"decision {n}: no review item {d.get('id')!r} in this model")
        if d.get("action") == "edit" and not str(d.get("value", "")).strip():
            bad.append(f"decision {n}: edit of {d.get('id')!r} has no value")
        if d.get("action") == "revert" and d.get("id") not in doc.get("auto", {}):
            bad.append(f"decision {n}: no automatic state recorded for {d.get('id')!r}")
    if bad:
        raise ValueError("; ".join(bad))


def final_states(doc: dict) -> Dict[str, Optional[dict]]:
    """Replay: the last decision per item wins; ``None`` means automatic output.

    Mirrored by ``finalStates`` in the report's script, so the page shows the
    same result the CLI writes.
    """
    out: Dict[str, Optional[dict]] = {}
    for d in doc.get("decisions", []):
        if d["action"] == "revert":
            out[d["id"]] = None
        else:
            out[d["id"]] = {"action": d["action"], "value": d.get("value")}
    return out


def apply_decisions(model, doc: dict, model_sha: Optional[str] = None) -> dict:
    """Apply a decisions file to ``model`` in place; returns a summary.

    Idempotent: replaying the same file again changes nothing and logs
    nothing. A model hash that differs from the file's is reported, not
    refused, because applying decisions changes the model itself.
    """
    items = {i.id: i for i in model.review_queue}
    check(doc, items)
    summary = {a: 0 for a in ACTIONS}
    summary["unchanged"] = 0
    summary["warnings"] = []
    if model_sha and doc.get("model_sha256") and model_sha != doc["model_sha256"]:
        summary["warnings"].append(
            "model has changed since the decisions file was made (SHA-256 differs); "
            "decisions were matched by review item id"
        )
    plan, bad = [], []
    for iid, st in final_states(doc).items():
        item = items[iid]
        act = "revert" if st is None else st["action"]
        try:
            v, f = _target_value(model, item, act, st and st.get("value"))
        except ValueError as e:
            bad.append(f"{iid}: {e}")
            continue
        plan.append((item, act, st, v, f))
    if bad:  # nothing changes unless every edit can be applied
        raise ValueError("; ".join(bad))
    for item, act, st, v, f in plan:
        if st is None:
            want, note = dict(doc["auto"][item.id]), "reverted to automatic output"
        else:
            want = _decided(act)
            note = act + (f": {st['value']}" if act == "edit" else "")
        if _set(model, item, want, note, v, f):
            summary[act] += 1
        else:
            summary["unchanged"] += 1
    return summary


def _decided(action: str) -> dict:
    return dict(_STATE[action], needs_review=False, acknowledged=True)


_KEEP = object()


def _set(model, item, want: dict, note: str, value=_KEEP, f: str = "") -> bool:
    """Put ``item`` in state ``want`` (and its field at ``value``) and log it.

    False when it already was. ``value`` is only written for an item whose
    target field an edit can change; the log keeps old and new values.
    """
    t = item.target or {}
    f = f or edit_field(model, item)
    old = _read(model, t.get("kind", ""), t.get("id"), f) if f and value is not _KEEP else None
    same_state = auto_state(item) == {**auto_state(item), **want}
    same_value = value is _KEEP or old == value
    if same_state and same_value:
        return False
    for k, v in want.items():
        setattr(item, k, v)
    if not same_value:
        t.setdefault("original", original_value(model, item))
        t.setdefault("field", f)
        item.target = t
        _write(model, t["kind"], t["id"], f, value)
        note = f"{note} ({f} {old!r} -> {value!r})"
    sheet = item.provenance.sheet_id if item.provenance else ""
    rev = int(getattr(item.provenance, "revision", 0) or 0) if item.provenance else 0
    model.log_revision(sheet, rev, "review", f"{item.id} {note}")
    return True


def _target_value(model, item, action: str, value):
    """(value to write or _KEEP, field). Confirm/reject/revert restore the original."""
    t = item.target or {}
    if action == "edit":
        f, raw = _split(model, item, value)
        if not f:
            return _KEEP, ""
        return _coerce(model, t["kind"], f, raw), f
    if "original" not in t:
        return _KEEP, ""
    return t["original"], t.get("field", "")


def decide(model, item_id: str, action: str, value: Optional[str] = None) -> bool:
    """One decision, exactly as a replayed decisions file would apply it.

    Used by ``matchline review --confirm/--reject`` so the flags and the HTML
    report leave an item in the same state with the same revision log entry.
    Returns False when the item was already in that state (nothing logged).
    """
    item = next((i for i in model.review_queue if i.id == item_id), None)
    if item is None:
        raise ValueError(f"Item {item_id} not found in review queue.")
    if action not in _STATE:
        raise ValueError(f"unknown action {action!r}")
    if action == "edit" and not str(value or "").strip():
        raise ValueError(f"edit of {item_id!r} has no value")
    note = action + (f": {value}" if action == "edit" else "")
    v, f = _target_value(model, item, action, value)
    return _set(model, item, _decided(action), note, v, f)


def _item_row(i) -> dict:
    p = i.provenance
    return {
        "id": i.id,
        "kind": i.kind,
        "description": i.description,
        "confidence": i.confidence,
        "urgency": i.urgency,
        "sheet": p.sheet_id if p else "",
        "method": p.method if p else "",
        "auto": auto_state(i),
        "target": {k: v for k, v in (i.target or {}).items() if k in ("kind", "id")},
    }


_CSS = """
body{font:13px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111;margin:20px}
h1{font-size:18px;margin:0 0 6px}.bar{display:flex;gap:10px;align-items:center;margin:8px 0}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:4px 6px;
border-bottom:1px solid #eee;vertical-align:top}th{color:#555;font-weight:600}
tr.sel{outline:2px solid #3367d6}tr.done td{color:#888}.hist{font-size:11px;color:#666}
.st-confirmed{color:#16794a}.st-rejected{color:#b00}.st-edited{color:#8a5a00}
button{font:inherit;padding:1px 6px}.mono{font-family:Menlo,Consolas,monospace;font-size:11px}
.keys{color:#666;font-size:11px}
"""

# Pure replay, mirrored from final_states(); runnable under node for tests.
_REPLAY_JS = """
function finalStates(doc) {
  var out = {};
  (doc.decisions || []).forEach(function (d) {
    out[d.id] = d.action === "revert" ? null
      : {action: d.action, value: d.value === undefined ? null : d.value};
  });
  return out;
}
if (typeof module !== "undefined") { module.exports = {finalStates: finalStates}; }
"""

_UI_JS = """
(function () {
  var data = JSON.parse(document.getElementById("data").textContent);
  var key = "matchline-review:" + data.doc.model_sha256;
  var doc = JSON.parse(JSON.stringify(data.doc));
  try { var saved = localStorage.getItem(key); if (saved) doc = JSON.parse(saved); }
  catch (e) {}
  var sel = 0, openOnly = false;
  function save() { try { localStorage.setItem(key, JSON.stringify(doc)); } catch (e) {} }
  function decide(id, action, it) {
    var d = {id: id, action: action, at: new Date().toISOString()};
    if (action === "edit") {
      var ask = it && it.field
        ? "New " + it.field + " for " + id + " (automatic: " + it.value + "). Changes the model:"
        : "Correction note for " + id + " (recorded only; nothing in the model to change):";
      var v = prompt(ask, "");
      if (v === null || !v.trim()) return;
      d.value = v.trim();
    }
    doc.decisions.push(d); save(); render();
  }
  function label(st, item) {
    if (!st) return item.auto.status + " (automatic)";
    return {confirm: "confirmed", reject: "rejected", edit: "edited"}[st.action];
  }
  function render() {
    var fs = finalStates(doc), tb = document.getElementById("rows"), n = 0;
    tb.innerHTML = "";
    data.items.forEach(function (it, k) {
      var st = fs.hasOwnProperty(it.id) ? fs[it.id] : null;
      var done = !!st || it.auto.status !== "open";
      if (openOnly && done) return;
      if (!done) n++;
      var tr = document.createElement("tr");
      tr.className = (k === sel ? "sel " : "") + (done ? "done" : "");
      var hist = doc.decisions.filter(function (d) { return d.id === it.id; })
        .map(function (d) { return d.action + (d.value ? " \\u201c" + d.value + "\\u201d" : "")
          + " " + (d.at || "").slice(0, 16).replace("T", " "); }).join("<br>");
      var cls = st ? "st-" + label(st, it) : "";
      tr.innerHTML = "<td>" + it.confidence.toFixed(2) + "</td><td>" + it.urgency + "</td>"
        + "<td class=mono>" + esc(it.kind) + (it.field ? "<div class=hist>edits " + esc(it.field)
          + "</div>" : "") + "</td><td>" + esc(it.description)
        + "<div class=hist>" + esc(it.sheet) + (it.method ? " \\u00b7 " + esc(it.method) : "")
        + "</div></td><td class='" + cls + "'>" + esc(label(st, it))
        + (st && st.value ? "<div class=hist>" + esc(st.value) + "</div>" : "")
        + "<div class=hist>" + hist.replace(/[<>&](?!br>)/g, "") + "</div></td><td></td>";
      ["confirm", "reject", "edit", "revert"].forEach(function (a) {
        var b = document.createElement("button"); b.textContent = a;
        b.onclick = function () { sel = k; decide(it.id, a, it); };
        tr.lastChild.appendChild(b);
      });
      tb.appendChild(tr);
    });
    document.getElementById("left").textContent = n + " still open";
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]; }); }
  document.getElementById("export").onclick = function () {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)],
      {type: "application/json"}));
    a.download = "decisions.json"; a.click();
  };
  document.getElementById("import").onchange = function (e) {
    var f = e.target.files[0]; if (!f) return;
    f.text().then(function (t) {
      var d = JSON.parse(t);
      if (d.schema !== data.doc.schema) { alert("Not a matchline decisions file."); return; }
      if (d.model_sha256 !== data.doc.model_sha256)
        alert("This file was made for a different model; decisions are matched by item id.");
      doc = d; save(); render();
    });
  };
  document.getElementById("open").onchange = function (e) {
    openOnly = e.target.checked; render();
  };
  document.addEventListener("keydown", function (e) {
    if (e.target.tagName === "INPUT") return;
    var it = data.items[sel], m = {c: "confirm", r: "reject", e: "edit", u: "revert"};
    if (e.key === "j") sel = Math.min(sel + 1, data.items.length - 1);
    else if (e.key === "k") sel = Math.max(sel - 1, 0);
    else if (m[e.key] && it) { decide(it.id, m[e.key], it); return; }
    else return;
    render();
  });
  render();
})();
"""


def _json_script(obj) -> str:
    # keep "</script>" and friends out of the embedded JSON
    return json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_html(model, doc: dict) -> str:
    items = []
    for i in worst_first(model.review_queue):
        row, f = _item_row(i), edit_field(model, i)
        row["field"] = f
        row["value"] = original_value(model, i) if f else None
        items.append(row)
    data = {"doc": doc, "items": items}
    name = html.escape(str(model.name))
    return (
        "\n".join(
            [
                "<!doctype html><html><head><meta charset=utf-8>",
                f"<title>matchline review: {name}</title><style>{_CSS}</style></head><body>",
                f"<h1>Review queue: {name}</h1>",
                f"<div class=mono>model SHA-256 {doc['model_sha256']}</div>",
                "<div class=bar><span id=left></span>"
                "<label><input type=checkbox id=open> open only</label>"
                "<button id=export>Export decisions.json</button>"
                "<label>Load decisions <input type=file id=import accept='.json'></label></div>",
                "<div class=keys>j/k move, c confirm, r reject, e edit, u revert to automatic. "
                "Then run: matchline review model.json --apply decisions.json</div>",
                "<table><thead><tr><th>confidence</th><th>urgency</th><th>kind</th>"
                "<th>item</th><th>decision and history</th><th></th></tr></thead>"
                "<tbody id=rows></tbody></table>",
                f'<script type="application/json" id="data">{_json_script(data)}</script>',
                f"<script id=replay>{_REPLAY_JS}</script>",
                f"<script id=ui>{_UI_JS}</script>",
                "</body></html>",
            ]
        )
        + "\n"
    )


def write_review(out_dir: Path, model) -> Path:
    """Write ``out_dir/review`` (model.json, decisions.json, review.html)."""
    d = Path(out_dir) / "review"
    d.mkdir(parents=True, exist_ok=True)
    text = model.to_json()
    (d / "model.json").write_text(text)
    doc = template(model, sha256_text(text))
    (d / "decisions.json").write_text(json.dumps(doc, indent=2))
    (d / "review.html").write_text(render_html(model, doc))
    return d


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize(summary: dict) -> str:
    parts = [f"{summary[a]} {w}" for a, w in
             (("confirm", "confirmed"), ("reject", "rejected"), ("edit", "edited"),
              ("revert", "reverted"))]  # fmt: skip
    return f"Applied decisions: {', '.join(parts)}, {summary['unchanged']} already as decided."


__all__: List[str] = [
    "SCHEMA", "ACTIONS", "apply_decisions", "check", "decide", "final_states", "render_html",
    "template", "worst_first", "write_review", "summarize",
]  # fmt: skip
