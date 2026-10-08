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

Sheet overlays (#749): every floor plan the run read for walls and rooms is
drawn below the queue with its rooms, walls, plan openings and symbol
detections on top of the sheet raster (embedded, downsized). Selecting an item
shows the room, opening or detection box it is about. Links between sheets
(an elevation window to its plan room) are not drawn yet.
"""

from __future__ import annotations

import base64
import hashlib
import html
import io
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
  var sel = 0, openOnly = false, svgs = {};
  (data.sheets || []).forEach(function (sh) {
    var host = document.querySelector(".sheet[data-sheet='" + sh.file + "']");
    if (host) svgs[sh.file] = drawSheet(sh, host);
  });
  document.querySelectorAll(".layers input").forEach(function (cb) {
    cb.onchange = function () {
      var svg = svgs[cb.closest("figure").id.slice(6)];
      var g = svg && svg.querySelector("g[data-layer='" + cb.dataset.layer + "']");
      if (g) g.style.display = cb.checked ? "" : "none";
    };
  });
  function show(id) {
    var ln = (data.links || {})[id], svg = ln && svgs[ln.sheet];
    document.querySelectorAll(".hit").forEach(function (e) {
      if (e.dataset.box) e.remove(); else e.classList.remove("hit"); });
    if (!svg) return false;
    if (ln.el) {
      var t = svg.querySelector("[data-el='" + ln.el + "']");
      if (t) t.classList.add("hit");
    }
    if (ln.box) {
      var b = ln.box;
      el("rect", {x: b[0], y: b[1], width: b[2] - b[0], height: b[3] - b[1],
        fill: "none", "class": "hit", "data-box": 1}, svg);
    }
    document.getElementById("sheet-" + ln.sheet)
      .scrollIntoView({behavior: "smooth", block: "start"});
    return true;
  }
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
      if ((data.links || {})[it.id]) {
        var sb = document.createElement("button"); sb.textContent = "sheet";
        sb.onclick = function () { sel = k; render(); show(it.id); };
        tr.lastChild.appendChild(sb);
      }
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
    else if (e.key === "s" && it) { show(it.id); return; }
    else return;
    render();
  });
  render();
})();
"""


def _json_script(obj) -> str:
    # keep "</script>" and friends out of the embedded JSON
    return json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


# ---------------------------------------------------------------- overlays

OVERLAY_MAX_PX = 1800  # longest side of the embedded sheet image
OVERLAY_JPEG_QUALITY = 60


def _sheet_image(png: Path) -> str:
    """The sheet raster as a downsized JPEG data URI ("" when unreadable)."""
    try:
        from PIL import Image

        with Image.open(png) as im:
            im = im.convert("L")
            im.thumbnail((OVERLAY_MAX_PX, OVERLAY_MAX_PX))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=OVERLAY_JPEG_QUALITY, optimize=True)
    except Exception:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def sheet_overlays(out_dir, model) -> List[dict]:
    """Rooms, walls, plan openings and detections per plan sheet, in sheet points.

    Reads what the drawing-set run left in ``out_dir/sheets`` (``sheet_NNN``
    json/png, ``walls_NNN.json``, ``detections_NNN.json``, ``sheet_index.json``).
    Room ids become the model's space ids and plan gaps the model's opening ids
    (``{level}-OP{k}``), so review items can point at them. Empty for runs that
    did not start from a drawing set.
    """
    d = Path(out_dir) / "sheets"
    if not d.is_dir():
        return []
    index = _load_json(d / "sheet_index.json") or {}
    number = {}
    for e in index.get("sheets", []):
        v = e.get("number")
        number[e.get("file")] = (v.get("value") if isinstance(v, dict) else v) or ""
    room_space, level_of = {}, {}
    for sp in model.spaces.values():
        note = (sp.core_provenance.note if sp.core_provenance else "") or ""
        parts = note.split(" ", 2)
        if len(parts) >= 2 and parts[0].startswith("sheet_"):
            room_space[(parts[0], parts[1].rstrip(";"))] = sp.id
            level_of.setdefault(parts[0], sp.level_id)
    modelled = {op.id for sp in model.spaces.values() for op in sp.openings}
    queued = {i.id for i in model.review_queue}
    out = []
    for wp in sorted(d.glob("walls_[0-9][0-9][0-9].json")):
        f = wp.name.replace("walls_", "sheet_")
        sh, res = _load_json(d / f), _load_json(wp)
        mpp = (res or {}).get("m_per_pt")
        if not sh or not mpp:
            continue
        w_pt, h_pt = float(sh["width_pt"]), float(sh["height_pt"])

        def pt(p, h=h_pt, k=mpp):  # metres, y up -> sheet points, y down
            return [round(p[0] / k, 1), round(h - p[1] / k, 1)]

        lid = level_of.get(f, f.replace(".json", ""))
        rooms = []
        for r in res.get("rooms", []):
            lab = r.get("label") or {}
            sid = room_space.get((f, r["id"]))
            rooms.append({
                "id": sid or r["id"], "space": bool(sid),
                "label": " ".join(x for x in (lab.get("number"), lab.get("name")) if x),
                "pts": [pt(q) for q in r["polygon_m"]], "review": bool(r.get("needs_review")),
            })  # fmt: skip
        walls = [
            {"id": w["id"], "a": pt(w["a_m"]), "b": pt(w["b_m"]),
             "t": round(w.get("thickness_m", 0.0) / mpp, 1)}
            for w in res.get("walls", [])
        ]  # fmt: skip
        openings = []
        for k, o in enumerate(res.get("openings", [])):
            oid = f"{lid}-OP{k + 1}"
            kind = o.get("kind") or ("air_wall" if o.get("air_wall") else "gap")
            state = (
                "modelled" if oid in modelled
                else "review" if f"rq-{oid}" in queued
                else "not modelled"
            )  # fmt: skip
            openings.append({"id": oid, "kind": kind, "state": state, "width_m": o.get("width_m"),
                             "a": pt(o["a_m"]), "b": pt(o["b_m"])})  # fmt: skip
        px = float(sh.get("px_per_pt") or 0) or None
        dets = []
        for k, x in enumerate(_load_json(d / f.replace("sheet_", "detections_")) or []):
            b = x.get("bbox") or []
            if px and len(b) == 4:
                dets.append({"id": f"{f}#{k}", "label": x.get("label", ""),
                             "tag": x.get("tag", ""), "score": float(x.get("score") or 0),
                             "box": [round(v / px, 1) for v in b]})  # fmt: skip
        raster = sh.get("raster_file")
        out.append({
            "file": f, "number": number.get(f, ""), "level": level_of.get(f, ""),
            "w": w_pt, "h": h_pt, "px_per_pt": px,
            "img": _sheet_image(d / raster) if raster and (d / raster).exists() else "",
            "rooms": rooms, "walls": walls, "openings": openings, "dets": dets,
        })  # fmt: skip
    return out


def item_links(model, sheets: List[dict]) -> Dict[str, dict]:
    """Where each review item sits on the sheets: ``{sheet, el}`` and/or a ``box``."""
    where = {}
    for s in sheets:
        for r in s["rooms"]:
            where[("space", r["id"])] = (s["file"], "room:" + r["id"])
        for o in s["openings"]:
            where[("opening", o["id"])] = (s["file"], "op:" + o["id"])
    by_number = {s["number"]: s for s in sheets if s["number"]}
    by_number.update({s["file"]: s for s in sheets})
    out = {}
    for i in model.review_queue:
        t = i.target or {}
        hit = where.get((t.get("kind"), t.get("id")))
        if hit is None and i.id.startswith("rq-"):
            hit = where.get(("opening", i.id[3:])) or where.get(("space", i.id[3:]))
        link = {"sheet": hit[0], "el": hit[1]} if hit else {}
        p = i.provenance
        s = by_number.get(p.sheet_id) if p else None
        if s and p.bbox and len(p.bbox) == 4 and s["px_per_pt"]:
            link.setdefault("sheet", s["file"])
            if link["sheet"] == s["file"]:
                link["box"] = [round(float(v) / s["px_per_pt"], 1) for v in p.bbox]
        if link:
            out[i.id] = link
    return out


_SHEET_CSS = """
figure{margin:18px 0}figcaption{font-weight:600;margin:4px 0}.sheet{position:relative;
border:1px solid #ccc;background:#fff}.sheet img,.sheet svg{position:absolute;left:0;top:0;
width:100%;height:100%}.layers{font-size:11px;color:#555}.layers label{margin-right:10px}
.hit{stroke:#e0007a!important;stroke-width:6px!important;fill:rgba(224,0,122,.18)!important}
.legend span{display:inline-block;margin-right:12px;font-size:11px}
.legend i{display:inline-block;width:12px;height:4px;margin-right:4px;vertical-align:middle}
"""

_SHEET_JS = """
var SVGNS = "http://www.w3.org/2000/svg";
var OP_COLOR = {door: "#1565c0", window: "#00838f", gap: "#6d4c41", air_wall: "#9e9e9e"};
function el(tag, attrs, parent) {
  var e = document.createElementNS(SVGNS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e); return e;
}
function hue(s) {
  var h = 0;
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
  return "hsl(" + h + ",70%,40%)";
}
function drawSheet(sh, host) {
  var svg = el("svg", {viewBox: "0 0 " + sh.w + " " + sh.h, preserveAspectRatio: "none"});
  var g = {};
  ["rooms", "walls", "openings", "dets"].forEach(function (n) {
    g[n] = el("g", {"data-layer": n}, svg);
  });
  sh.rooms.forEach(function (r) {
    var p = el("polygon", {points: r.pts.map(function (q) { return q.join(","); }).join(" "),
      fill: r.review ? "rgba(239,108,0,.18)" : "rgba(46,125,50,.12)",
      stroke: r.review ? "#ef6c00" : "#2e7d32", "stroke-width": 1.5,
      "data-el": "room:" + r.id}, g.rooms);
    el("title", {}, p).textContent = r.id + (r.label ? " " + r.label : "")
      + (r.review ? " (review)" : "");
  });
  sh.walls.forEach(function (w) {
    el("line", {x1: w.a[0], y1: w.a[1], x2: w.b[0], y2: w.b[1], stroke: "rgba(33,33,33,.55)",
      "stroke-width": Math.max(w.t, 1), "data-el": "wall:" + w.id}, g.walls);
  });
  sh.openings.forEach(function (o) {
    var l = el("line", {x1: o.a[0], y1: o.a[1], x2: o.b[0], y2: o.b[1],
      stroke: o.state === "review" ? "#d32f2f" : (OP_COLOR[o.kind] || "#6d4c41"),
      "stroke-width": 5, "stroke-dasharray": o.state === "modelled" ? "" : "6 4",
      "data-el": "op:" + o.id}, g.openings);
    el("title", {}, l).textContent = o.id + " " + o.kind + ", " + o.state
      + (o.width_m ? ", " + o.width_m + " m" : "");
  });
  sh.dets.forEach(function (x) {
    var r = el("rect", {x: x.box[0], y: x.box[1],
      width: x.box[2] - x.box[0], height: x.box[3] - x.box[1],
      fill: "none", stroke: hue(x.label), "stroke-width": 1.5, opacity: 0.25 + 0.75 * x.score,
      "data-el": "det:" + x.id}, g.dets);
    el("title", {}, r).textContent = x.label + (x.tag ? " " + x.tag : "") + " "
      + x.score.toFixed(2);
  });
  host.appendChild(svg); return svg;
}
"""


def _sheets_html(sheets: List[dict]) -> str:
    if not sheets:
        return (
            "<h2>Sheets</h2><p>No plan sheets in this run "
            "(it did not start from a drawing set).</p>"
        )
    parts = [
        "<h2>Sheets</h2><div class=legend>"
        "<span><i style='background:#2e7d32'></i>room</span>"
        "<span><i style='background:#ef6c00'></i>room needing review</span>"
        "<span><i style='background:#1565c0'></i>door</span>"
        "<span><i style='background:#00838f'></i>window</span>"
        "<span><i style='background:#6d4c41'></i>gap</span>"
        "<span><i style='background:#d32f2f'></i>opening in review</span>"
        "<span>solid: modelled, dashed: not modelled; detection boxes fade with lower score</span>"
        "</div>"
    ]
    for s in sheets:
        cap = html.escape(
            " ".join(x for x in (s["number"], s["file"], s["level"] and f"level {s['level']}") if x)
        )
        img = f"<img alt='' src='{s['img']}'>" if s["img"] else ""
        parts.append(
            f"<figure id='sheet-{html.escape(s['file'])}'><figcaption>{cap}</figcaption>"
            "<div class=layers>"
            + "".join(
                f"<label><input type=checkbox checked data-layer={n}> {n}</label>"
                for n in ("rooms", "walls", "openings", "dets")
            )
            + f"</div><div class=sheet data-sheet='{html.escape(s['file'])}' "
            f"style='aspect-ratio:{s['w']:.1f}/{s['h']:.1f}'>{img}</div></figure>"
        )
    return "\n".join(parts)


def render_html(model, doc: dict, sheets: Optional[List[dict]] = None) -> str:
    items = []
    for i in worst_first(model.review_queue):
        row, f = _item_row(i), edit_field(model, i)
        row["field"] = f
        row["value"] = original_value(model, i) if f else None
        items.append(row)
    sheets = sheets or []
    data = {"doc": doc, "items": items, "sheets": sheets, "links": item_links(model, sheets)}
    name = html.escape(str(model.name))
    return (
        "\n".join(
            [
                "<!doctype html><html><head><meta charset=utf-8>",
                f"<title>matchline review: {name}</title>",
                f"<style>{_CSS}{_SHEET_CSS}</style></head><body>",
                f"<h1>Review queue: {name}</h1>",
                f"<div class=mono>model SHA-256 {doc['model_sha256']}</div>",
                "<div class=bar><span id=left></span>"
                "<label><input type=checkbox id=open> open only</label>"
                "<button id=export>Export decisions.json</button>"
                "<label>Load decisions <input type=file id=import accept='.json'></label></div>",
                "<div class=keys>j/k move, c confirm, r reject, e edit, u revert to automatic, "
                "s show on sheet. "
                "Then run: matchline review model.json --apply decisions.json</div>",
                "<table><thead><tr><th>confidence</th><th>urgency</th><th>kind</th>"
                "<th>item</th><th>decision and history</th><th></th></tr></thead>"
                "<tbody id=rows></tbody></table>",
                _sheets_html(sheets),
                f'<script type="application/json" id="data">{_json_script(data)}</script>',
                f"<script id=replay>{_REPLAY_JS}</script>",
                f"<script id=sheets>{_SHEET_JS}</script>",
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
    (d / "review.html").write_text(render_html(model, doc, sheet_overlays(out_dir, model)))
    return d


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summarize(summary: dict) -> str:
    parts = [f"{summary[a]} {w}" for a, w in
             (("confirm", "confirmed"), ("reject", "rejected"), ("edit", "edited"),
              ("revert", "reverted"))]  # fmt: skip
    return f"Applied decisions: {', '.join(parts)}, {summary['unchanged']} already as decided."


__all__: List[str] = [
    "SCHEMA", "ACTIONS", "apply_decisions", "check", "decide", "final_states", "item_links",
    "render_html", "sheet_overlays",
    "template", "worst_first", "write_review", "summarize",
]  # fmt: skip
