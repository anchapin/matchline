"""Export package with a one-page trust report (#750, first slice).

``out/package/`` holds what a modeler hands to a reviewer:

- the gbXML and IFC the run exported, and its convention report;
- ``trust_report.json``: every number the report shows, each with the file and
  field (or model provenance) it was read from;
- ``trust_report.html``: one page, rendered from that JSON, offline (inline
  CSS, no external assets); ``trust_report.pdf``, the same content on
  one page, drawn with reportlab from the same JSON;
- ``DISCLAIMER.txt`` and ``manifest.json`` (file names and SHA-256);
- ``share_back.html``: a self-contained preview of the whole package (#999);
- ``package.zip``: the one zip a reviewer receives (#999);
- a dated sign-off block (#976): when the report was generated, the tool
  version, and blank preparer/reviewer/stamp lines for the professional
  to fill and stamp.

The report only reads what the run already wrote: inputs and their hashes,
what was extracted, the convention biases, the validation summary, the open
review items and every default the model used with its source. The editable
decisions file comes from the HTML review report (#749): when the run wrote
one, ``review/`` (page, decisions template, model) is copied into the package
and the report says how to replay it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from review.feedback_capture import summarize
from html_page import page_head

SCHEMA = "matchline.trust_report/1"
DISCLAIMER = (
    "matchline produces building energy modeling inputs for review by a qualified "
    "professional. It is not an engineering stamp, a code compliance determination or a "
    "substitute for professional judgement. Check every value against the drawings before "
    "using it."
)
# provenance methods that mean "not drawn": 90.1 envelope fills and the storey
# height a drawing set falls back to when no section or elevation was read
DEFAULT_METHODS = ("construction_default", "storey_height_default")
TOP_REVIEW = 15
SHARE_BACK_BANNER = (
    "Share-back preview: this one page previews the whole export package "
    "(model, trust report, review decisions and history); manifest.json "
    "carries the SHA-256 of every file, report included."
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _provenances(obj, path: str = "model", seen=None):
    """Yield ``(path, Provenance)`` for every provenance in the model."""
    from building_model import Provenance

    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return
    if isinstance(obj, Provenance):
        yield path, obj
        return
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        seen.add(id(obj))
        for f in dataclasses.fields(obj):
            yield from _provenances(getattr(obj, f.name), f"{path}.{f.name}", seen)
    elif isinstance(obj, dict):
        seen.add(id(obj))
        for k, v in obj.items():
            yield from _provenances(v, f"{path}[{k!r}]", seen)
    elif isinstance(obj, (list, tuple)):
        seen.add(id(obj))
        for i, v in enumerate(obj):
            yield from _provenances(v, f"{path}[{i}]", seen)


def _num(label: str, value, source: str) -> dict:
    return {"label": label, "value": value, "source": source}


def _inputs(inputs: Iterable[dict]) -> List[dict]:
    out = []
    for item in inputs:
        p = item.get("path")
        row = dict(item)
        if p and Path(p).is_file():
            row["path"] = str(p)
            row["sha256"] = sha256(Path(p))
            row["bytes"] = Path(p).stat().st_size
        elif p:
            row["path"] = str(p)
            row["sha256"] = None
            row["missing"] = True
        out.append(row)
    return out


def _corrections(out_dir) -> dict:
    """The correction history beside the model (#997), or why there is none."""
    p = Path(out_dir) / "corrections.json"
    if not p.exists():
        return {"recorded": False, "reason": "no accepted corrections beside the model yet"}
    import review.corrections as corrections_mod
    try:
        doc = corrections_mod.read(p)
    except (ValueError, OSError) as e:
        return {"recorded": False, "reason": f"corrections.json could not be read ({e})"}
    return {"recorded": True, "file": "corrections.json", "summary": corrections_mod.summary(doc)}


def _feedback(out_dir) -> dict:
    """The local friction-capture log beside the model (#1080), or why there is none."""
    p = Path(out_dir) / "feedback.jsonl"
    if not p.exists():
        return {"present": False, "reason": "no feedback log for this run"}
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return {"present": False, "reason": f"feedback.jsonl could not be read ({e})"}
    events: list = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            skipped += 1
    out = {"present": True, "file": "feedback.jsonl", "summary": summarize(events)}
    if skipped:
        out["skipped_lines"] = skipped
    return out


def _decisions(review_dir, n_open: int) -> dict:
    """Where the reviewer records decisions (#749), or why there is nothing yet."""
    if not review_dir or not (Path(review_dir) / "decisions.json").exists():
        return {"available": False, "reason": "no review report was written for this run"}
    return {
        "available": True,
        "page": "review/review.html",
        "file": "review/decisions.json",
        "note": (
            f"{n_open} open item(s): review them in review/review.html, export "
            "decisions.json, then run: matchline review review/model.json "
            "--apply decisions.json"
        ),
    }


def build_trust_report(
    model,
    report,
    convention: dict,
    inputs: Iterable[dict] = (),
    review_dir=None,
    corrections: Optional[dict] = None,
    feedback: Optional[dict] = None,
) -> dict:
    """The trust report as data; every number names where it was read from."""
    spaces = list(model.spaces.values())
    openings = [o for s in spaces for o in s.openings]
    extracted = [
        _num("levels", len(model.levels), "stage_02_model.json model.levels"),
        _num("spaces", len(spaces), "stage_02_model.json model.spaces"),
        _num(
            "floor area m2",
            round(sum(float(getattr(s, "area_m2", 0) or 0) for s in spaces), 2),
            "stage_02_model.json model.spaces[].area_m2",
        ),
        _num("exterior walls", len(model.envelope), "stage_02_model.json model.envelope"),
        _num("openings", len(openings), "stage_02_model.json model.spaces[].openings"),
        _num("constructions", len(model.constructions), "stage_02_model.json model.constructions"),
    ]
    sev = {}
    for r in report.results:
        sev[r.severity] = sev.get(r.severity, 0) + 1
    validation = {
        "ok": bool(getattr(report, "ok", not report.errors)),
        "counts": [
            _num(k, sev.get(k, 0), "stage_04_validation.json results[].severity")
            for k in ("pass", "warn", "error", "skip")
        ],
        "not_passed": [
            {"check": r.check_id, "severity": r.severity, "message": r.message}
            for r in report.results
            if r.severity in ("warn", "error")
        ],
    }
    open_items = [r for r in model.review_queue if getattr(r, "needs_review", True)]
    open_items.sort(key=lambda r: float(getattr(r, "confidence", 0) or 0))
    n_open = len(open_items)
    review = {
        "open": _num(
            "open review items", len(open_items), "stage_02_model.json model.review_queue"
        ),
        "worst": [
            {
                "id": r.id,
                "kind": r.kind,
                "confidence": getattr(r, "confidence", None),
                "description": r.description,
            }
            for r in open_items[:TOP_REVIEW]
        ],
    }
    defaults = []
    for path, p in _provenances(model):
        if p.method in DEFAULT_METHODS:
            defaults.append(
                {
                    "where": path,
                    "method": p.method,
                    "confidence": p.confidence,
                    "source": p.note or p.sheet_id or "(no source recorded)",
                }
            )
    vb = convention.get("volume_bias", {})
    nra = convention.get("non_room_area", {})
    ab = convention.get("area_budget", {})
    biases = []
    for label, block, key in (
        ("volume bias", vb, "volume_bias"),
        ("non-room area", nra, "non_room_area"),
        ("envelope area budget", ab, "area_budget"),
    ):
        if block.get("available") is False:
            biases.append(
                {
                    "label": label,
                    "value": None,
                    "reason": block.get("reason", ""),
                    "source": f"convention_report.json {key}",
                }
            )
            continue
        for k, v in block.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                biases.append(_num(f"{label}: {k}", v, f"convention_report.json {key}.{k}"))
    signoff = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_version": _tool_version(),
        # left blank on purpose: the professional fills these in and stamps the
        # report; matchline never signs anything (#976).
        "prepared_by": "",
        "prepared_date": "",
        "reviewed_by": "",
        "reviewed_date": "",
        "stamp": "",
    }
    tr = {
        "schema": SCHEMA,
        "building": model.name,
        "disclaimer": DISCLAIMER,
        "signoff": signoff,
        "inputs": _inputs(inputs),
        "extracted": extracted,
        "convention_biases": biases,
        "validation": validation,
        "review": review,
        "defaults": defaults,
        "decisions_file": _decisions(review_dir, n_open),
        "corrections": corrections or {"recorded": False, "reason": "no corrections recorded"},
    }
    if feedback and feedback.get("present"):
        tr["feedback"] = feedback
    return tr


# ------------------------------------------------------------------- render

_CSS = """
body{font:11px/1.35 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#111;
margin:24px;max-width:780px}
h1{font-size:18px;margin:0 0 4px}h2{font-size:13px;margin:14px 0 4px;border-bottom:1px solid #ccc}
.disc{border:1px solid #b00;background:#fff4f4;padding:6px 8px;margin:8px 0}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:2px 6px;
border-bottom:1px solid #eee;vertical-align:top}th{color:#555;font-weight:600}
.src{color:#777;font-size:9px}.mono{font-family:Menlo,Consolas,monospace;font-size:9px}
"""


def _tool_version() -> str:
    """This matchline's version, for the sign-off block's traceability."""
    try:
        from importlib.metadata import version

        return version("matchline")
    except Exception:  # noqa: BLE001  (not installed; fall through)
        return "(unknown)"


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def fmt(v) -> str:
    """Display form of a number; trust_report.json keeps the full value.

    Floating-point noise (|v| < 1e-9) shows as 0, whole numbers without a
    decimal point, values of 1 or more to two decimals (thousands separated
    from 1,000), and smaller values to three significant figures.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return "" if v is None else str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if not math.isfinite(v):
        return str(v)
    if abs(v) < 1e-9:
        return "0"
    if v == int(v) and abs(v) < 1e15:
        return f"{int(v):,}"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 1:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{v:.3g}"


def _val(n: dict) -> str:
    if n.get("value") is None:
        return "unavailable: " + n.get("reason", "")
    return fmt(n["value"])


def _rows(nums) -> str:
    return "".join(
        f"<tr><td>{_e(n['label'])}</td><td>{_e(_val(n))}</td>"
        f"<td class=src>{_e(n['source'])}</td></tr>"
        for n in nums
    )


def _table(head: List[str], body: str) -> str:
    if not body:
        return ""
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    return f"<table><tr>{th}</tr>{body}</table>"


def render_html(tr: dict, banner: str = "") -> str:
    ins = "".join(
        f"<tr><td>{_e(i.get('role'))}</td>"
        f"<td class=mono>{_e(i.get('path') or i.get('value'))}</td>"
        f"<td class=mono>{_e((i.get('sha256') or '')[:16])}</td></tr>"
        for i in tr["inputs"]
    )
    val = tr["validation"]
    bad = "".join(
        f"<tr><td>{_e(x['severity'])}</td><td>{_e(x['check'])}</td><td>{_e(x['message'])}</td></tr>"
        for x in val["not_passed"]
    )
    rv = tr["review"]
    worst = "".join(
        f"<tr><td>{_e(fmt(x['confidence']))}</td><td>{_e(x['kind'])}</td>"
        f"<td>{_e(x['description'])}</td></tr>"
        for x in rv["worst"]
    )
    dfl = "".join(
        f"<tr><td class=mono>{_e(d['where'])}</td><td>{_e(d['source'])}</td></tr>"
        for d in tr["defaults"]
    )
    more = rv["open"]["value"] - len(rv["worst"])
    parts = [
        page_head(
            f"matchline trust report: {tr['building']}", _CSS, f"Trust report: {tr['building']}"
        ),
        f"<p>{_e(banner)}</p>" if banner else "",
        f"<div class=disc>{_e(tr['disclaimer'])}</div>",
        "<h2>Inputs</h2>",
        _table(["role", "file or value", "sha256"], ins) or "<p>no input files recorded</p>",
        "<h2>Extracted</h2>",
        _table(["", "value", "read from"], _rows(tr["extracted"])),
        "<h2>Convention biases</h2>",
        _table(["", "value", "read from"], _rows(tr["convention_biases"])) or "<p>none</p>",
        f"<h2>Validation: {'ok' if val['ok'] else 'errors'}</h2>",
        _table(["", "checks", "read from"], _rows(val["counts"])),
        _table(["severity", "check", "message"], bad),
        f"<h2>Open review items: {rv['open']['value']}</h2>",
        _table(["confidence", "kind", "description"], worst),
        f"<p>{more} more in stage_02_model.json review_queue.</p>" if more > 0 else "",
        f"<h2>Defaults used: {len(tr['defaults'])}</h2>",
        _table(["where", "source"], dfl) or "<p>no defaults used</p>",
        "<h2>Sign-off</h2>",
        "<p>Report generated "
        + _e(tr["signoff"]["generated_at"])
        + " by matchline "
        + _e(tr["signoff"]["tool_version"])
        + ". Source/revision traceability: the "
        "Inputs table above carries each file's SHA-256; the extracted and defaults "
        "tables name the field each number was read from.</p>",
        "<table><tr><th></th><th>name</th><th>date</th><th>stamp / seal</th></tr>"
        "<tr><td>Prepared by</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>"
        "<tr><td>Reviewed by</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr></table>",
        "<h2>Review decisions</h2>",
        f"<p>{_e(_dnote(tr))}</p>",
        _corrections_html(tr),
        _feedback_html(tr),
        _contents_html(tr),
        "</body></html>",
    ]
    return "\n".join(p for p in parts if p) + "\n"


def _feedback_html(tr: dict) -> str:
    """The feedback-capture summary section (#1080); nothing at all when the log is absent."""
    fb = tr.get("feedback") or {}
    if not fb.get("present"):
        return ""
    s = fb.get("summary") or {}
    by_kind = ", ".join(f"{k} {v}" for k, v in (s.get("by_kind") or {}).items()) or "none"
    parts = [
        f"{s.get('total', 0)} captured events this run, summarized from "
        f"{fb.get('file', 'feedback.jsonl')}: {by_kind}."
    ]
    fric = ", ".join(f"{key} ({v})" for key, v in (s.get("top_friction") or []))
    if fric:
        parts.append(f"Top friction: {fric}.")
    th = ", ".join(f"{w} ({v})" for w, v in (s.get("themes") or []))
    if th:
        parts.append(f"Recurring words in open responses: {th}.")
    if fb.get("skipped_lines"):
        parts.append(f"{fb['skipped_lines']} malformed log line(s) skipped.")
    parts.append("Counts and generic labels only; the log itself stays local.")
    return "<h2>Feedback log</h2><p>" + " ".join(parts) + "</p>"


def _contents_html(tr: dict) -> str:
    """The package contents section (#999): every file, with its hash or the
    note that the manifest carries it."""
    rows = "".join(
        f"<tr><td class=mono>{_e(c['path'])}</td>"
        f"<td class=mono>{c['bytes'] if c.get('bytes') is not None else ''}</td>"
        f"<td class=mono>"
        + (_e(c["sha256"][:16]) if c.get("sha256") else "hashed in manifest.json")
        + "</td></tr>"
        for c in tr.get("package_contents", [])
    )
    if not rows:
        return ""
    return (
        "<h2>Package contents</h2>"
        f"<table><tr><th>file</th><th>bytes</th><th>sha256</th></tr>{rows}</table>"
        "<p>manifest.json carries the SHA-256 of every file in the package, "
        "the report included.</p>"
    )


def _corrections_html(tr: dict) -> str:
    """The corrections section of the HTML report (#997)."""
    cor = tr.get("corrections") or {}
    if not cor.get("recorded"):
        return "<h2>Corrections</h2><p>no accepted corrections recorded</p>"
    s = cor.get("summary") or {}
    return (
        "<h2>Corrections</h2><p>"
        f"{s.get('total', 0)} accepted ({s.get('human', 0)} human, {s.get('auto', 0)} auto), "
        f"{s.get('undone', 0)} undone; history in corrections.json.</p>"
    )


def _corrections_note(tr: dict) -> str:
    cor = tr.get("corrections") or {}
    if not cor.get("recorded"):
        return "No accepted corrections recorded."
    s = cor.get("summary") or {}
    return (
        f"{s.get('total', 0)} accepted ({s.get('human', 0)} human, {s.get('auto', 0)} auto), "
        f"{s.get('undone', 0)} undone; history in corrections.json."
    )


def _dnote(tr: dict) -> str:
    d = tr["decisions_file"]
    return d.get("note") or d.get("reason", "")


def _cells(rows: List[List[str]]):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    st = ParagraphStyle("c", fontName="Helvetica", fontSize=7, leading=8.5)
    return [[Paragraph(html.escape(str(c)), st) for c in r] for r in rows]


def write_pdf(tr: dict, path: Path) -> None:
    """One-page PDF of the trust report, drawn from the same JSON as the HTML.

    Pure Python (reportlab, BSD), so it needs no system packages and CI checks
    it too. Content that would run past one page is scaled down to fit.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table

    m = 36
    w, h = letter
    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=14, leading=17)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=9.5, leading=12, spaceBefore=6)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=7.5, leading=9)
    disc = ParagraphStyle(
        "d", parent=body, borderColor=colors.HexColor("#bb0000"), borderWidth=0.75,
        borderPadding=4, backColor=colors.HexColor("#fff4f4"), spaceBefore=4, spaceAfter=6,
    )  # fmt: skip

    def table(head, rows, widths):
        if not rows:
            return None
        t = Table(_cells([head] + rows), colWidths=[f * (w - 2 * m) for f in widths])
        t.setStyle([
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#555555")),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ])  # fmt: skip
        return t

    def nums(ns):
        return [[n["label"], _val(n), n["source"]] for n in ns]

    val, rv = tr["validation"], tr["review"]
    more = rv["open"]["value"] - len(rv["worst"])
    parts = [
        Paragraph(f"Trust report: {html.escape(str(tr['building']))}", h1),
        Paragraph(html.escape(tr["disclaimer"]), disc),
        Paragraph("Inputs", h2),
        table(["role", "file or value", "sha256"],
              [[i.get("role"), i.get("path") or i.get("value"), (i.get("sha256") or "")[:16]]
               for i in tr["inputs"]], (0.18, 0.62, 0.20))
        or Paragraph("no input files recorded", body),
        Paragraph("Extracted", h2),
        table(["", "value", "read from"], nums(tr["extracted"]), (0.30, 0.20, 0.50)),
        Paragraph("Convention biases", h2),
        table(["", "value", "read from"], nums(tr["convention_biases"]), (0.30, 0.30, 0.40))
        or Paragraph("none", body),
        Paragraph(f"Validation: {'ok' if val['ok'] else 'errors'}", h2),
        table(["", "checks", "read from"], nums(val["counts"]), (0.30, 0.20, 0.50)),
        table(["severity", "check", "message"],
              [[x["severity"], x["check"], x["message"]] for x in val["not_passed"]],
              (0.12, 0.28, 0.60)),
        Paragraph(f"Open review items: {rv['open']['value']}", h2),
        table(["confidence", "kind", "description"],
              [[fmt(x["confidence"]), x["kind"], x["description"]] for x in rv["worst"]],
              (0.12, 0.22, 0.66)),
        Paragraph(f"{more} more in stage_02_model.json review_queue.", body) if more > 0 else None,
        Paragraph(f"Defaults used: {len(tr['defaults'])}", h2),
        table(["where", "source"], [[d["where"], d["source"]] for d in tr["defaults"]],
              (0.45, 0.55))
        or Paragraph("no defaults used", body),
        Paragraph("Sign-off", h2),
        Paragraph(
            f"Report generated {tr['signoff']['generated_at']} by matchline "
            f"{tr['signoff']['tool_version']}. Source/revision traceability: the "
            "Inputs table carries each file's SHA-256; the extracted and defaults "
            "tables name the field each number was read from.", body),
        table(["", "name", "date", "stamp / seal"],
              [["Prepared by", "", "", ""], ["Reviewed by", "", "", ""]],
              (0.18, 0.30, 0.22, 0.30)),
        Paragraph("Review decisions", h2),
        Paragraph(html.escape(_dnote(tr)), body),
        Paragraph("Corrections", h2),
        Paragraph(html.escape(_corrections_note(tr)), body),
        Paragraph("Package contents", h2),
        table(["file", "bytes", "sha256"],
              [[c["path"],
                c["bytes"] if c.get("bytes") is not None else "",
                (c["sha256"] or "hashed in manifest.json")[:16]
                if c.get("sha256") else "hashed in manifest.json"]
               for c in tr.get("package_contents", [])],
              (0.52, 0.13, 0.35)),
        Spacer(1, 2),
    ]  # fmt: skip
    frame = KeepInFrame(w - 2 * m, h - 2 * m, [p for p in parts if p is not None], mode="shrink")
    doc = SimpleDocTemplate(
        str(path), pagesize=letter, leftMargin=m, rightMargin=m, topMargin=m, bottomMargin=m,
        title=f"matchline trust report: {tr['building']}", author="matchline",
    )  # fmt: skip
    doc.build([frame])


def _pdf(tr: dict, path: Path) -> Optional[str]:
    """Write the PDF; returns why not when it can't."""
    try:
        write_pdf(tr, path)
    except Exception as e:  # noqa: BLE001  (report it in the manifest, keep the package)
        return f"PDF rendering failed: {e}"
    return None


def _viewer_fragment(scene: dict, banner: str = "") -> str:
    """The 3D review viewer (#1001) as an embeddable fragment: one render
    core shared with the ``matchline viewer`` page, embedded here in the
    share-back preview (#999)."""
    import review.review_viewer as review_viewer
    return review_viewer.fragment(scene, banner=banner)


def review_viewer_scene(model, corrections_doc: Optional[dict] = None) -> dict:
    """The viewer scene built from the model the package is built around
    (#1001); read verbatim, nothing re-derived."""
    import review.review_viewer as review_viewer
    return review_viewer.build_scene(model, corrections_doc=corrections_doc)


def _role_for(name: str) -> str:
    if name.startswith("review/"):
        return "review report, decisions template and model (#749)"
    if name.endswith((".xml", ".gbxml")):
        return "exported gbXML"
    if name.endswith(".ifc"):
        return "exported IFC4"
    if name == "convention_report.json":
        return "measured convention biases"
    if name == "corrections.json":
        return "correction history (#997)"
    if name == "DISCLAIMER.txt":
        return "professional-use disclaimer"
    return "package file"


def _contents(pkg: Path, generated: List[str]) -> List[dict]:
    """Every file the package will hold: the ones on disk with their hash,
    the ones written with the report itself (the manifest then carries the
    SHA-256 of those too, report included)."""
    rows = []
    for p in sorted(pkg.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(pkg).as_posix()
        rows.append(
            {
                "path": rel,
                "bytes": p.stat().st_size,
                "sha256": sha256(p),
                "role": _role_for(rel),
            }
        )
    for rel in generated:
        rows.append(
            {"path": rel, "bytes": None, "sha256": None, "role": _role_for(rel), "generated": True}
        )
    return rows


def zip_package(pkg: Path, zip_path: Path) -> Path:
    """One zip of the package (#999); entries sorted, names package-relative."""
    import zipfile

    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(p for p in Path(pkg).rglob("*") if p.is_file()):
            zf.write(p, p.relative_to(pkg).as_posix())
    return zip_path


def export_zip(out_dir: Path) -> Path:
    """Verify an existing package's manifest and build ``out_dir/package.zip``."""
    out_dir = Path(out_dir)
    pkg = out_dir / "package"
    manifest = pkg / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError(f"no export package under {out_dir} (run `matchline run` first)")
    man = json.loads(manifest.read_text())
    for name, digest in man["files"].items():
        f = pkg / name
        if not f.exists() or sha256(f) != digest:
            raise ValueError(f"manifest hash mismatch for {name}")
    return zip_package(pkg, out_dir / "package.zip")


def build_package(
    out_dir: Path,
    model,
    report,
    convention: dict,
    files: Iterable[Path],
    inputs: Iterable[dict] = (),
    pdf: bool = True,
    review_dir: Optional[Path] = None,
    require_gate: bool = False,
) -> Path:
    """Write ``out_dir/package`` and ``out_dir/package.zip``; returns the dir."""
    if require_gate:
        from validate import export_gate

        if not export_gate(report, model):
            raise ValueError(
                "the export gate has not acknowledged the review items; no package is built"
            )
    pkg = Path(out_dir) / "package"
    pkg.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f and Path(f).exists():
            shutil.copy2(f, pkg / Path(f).name)
    (pkg / "convention_report.json").write_text(
        json.dumps(convention, indent=2, default=str), encoding="utf-8"
    )
    if review_dir and Path(review_dir).is_dir():
        rv = pkg / "review"
        rv.mkdir(exist_ok=True)
        for name in ("review.html", "decisions.json", "model.json"):
            if (Path(review_dir) / name).exists():
                shutil.copy2(Path(review_dir) / name, rv / name)
        review_dir = rv
    corr = _corrections(out_dir)
    if corr.get("recorded"):
        shutil.copy2(Path(out_dir) / "corrections.json", pkg / "corrections.json")
    tr = build_trust_report(model, report, convention, inputs, review_dir, corr, _feedback(out_dir))
    (pkg / "DISCLAIMER.txt").write_text(DISCLAIMER + "\n", encoding="utf-8")
    generated = ["trust_report.json", "trust_report.html"]
    if pdf:
        generated.append("trust_report.pdf")
    generated += ["share_back.html", "manifest.json"]
    tr["package_contents"] = _contents(pkg, generated)
    (pkg / "trust_report.json").write_text(json.dumps(tr, indent=2, default=str), encoding="utf-8")
    html_path = pkg / "trust_report.html"
    html_path.write_text(render_html(tr), encoding="utf-8")
    share_html = render_html(tr, banner=SHARE_BACK_BANNER)
    # the 3D preview rides on the same page, from the same render core the
    # CLI viewer uses (#1001): the canonical model is the only geometry
    corr_doc = None
    cp = Path(out_dir) / "corrections.json"
    if cp.exists():
        try:
            corr_doc = json.loads(cp.read_text())
        except (OSError, ValueError):
            corr_doc = None
    viewer_frag = _viewer_fragment(review_viewer_scene(model, corrections_doc=corr_doc), banner="")
    share_html = share_html.replace("</body>", viewer_frag + "\n</body>", 1)
    (pkg / "share_back.html").write_text(share_html, encoding="utf-8")
    why = _pdf(tr, pkg / "trust_report.pdf") if pdf else "PDF not requested"
    manifest = {
        "schema": "matchline.package/1",
        "files": {
            p.relative_to(pkg).as_posix(): sha256(p)
            for p in sorted(pkg.rglob("*"))
            if p.is_file() and p.name != "manifest.json"
        },
        "pdf": {"produced": why is None, **({"reason": why} if why else {})},
    }
    (pkg / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    zip_package(pkg, Path(out_dir) / "package.zip")
    return pkg
