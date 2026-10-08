"""Export package with a one-page trust report (#750, first slice).

``out/package/`` holds what a modeler hands to a reviewer:

- the gbXML and IFC the run exported, and its convention report;
- ``trust_report.json``: every number the report shows, each with the file and
  field (or model provenance) it was read from;
- ``trust_report.html``: one page, rendered from that JSON, offline (inline
  CSS, no external assets); ``trust_report.pdf`` when LibreOffice is
  installed, otherwise the manifest says why there is none;
- ``DISCLAIMER.txt`` and ``manifest.json`` (file names and SHA-256).

The report only reads what the run already wrote: inputs and their hashes,
what was extracted, the convention biases, the validation summary, the open
review items and every default the model used with its source. The editable
decisions file belongs to the review report (#749) and is listed as not yet
available rather than invented.
"""

from __future__ import annotations

import dataclasses
import hashlib
import html
import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional

SCHEMA = "matchline.trust_report/1"
DISCLAIMER = (
    "matchline produces building energy modeling inputs for review by a qualified "
    "professional. It is not an engineering stamp, a code compliance determination or a "
    "substitute for professional judgement. Check every value against the drawings before "
    "using it."
)
DEFAULT_METHODS = ("construction_default",)  # provenance methods that mean "not drawn"
TOP_REVIEW = 15


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


def build_trust_report(model, report, convention: dict, inputs: Iterable[dict] = ()) -> dict:
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
    return {
        "schema": SCHEMA,
        "building": model.name,
        "disclaimer": DISCLAIMER,
        "inputs": _inputs(inputs),
        "extracted": extracted,
        "convention_biases": biases,
        "validation": validation,
        "review": review,
        "defaults": defaults,
        "decisions_file": {
            "available": False,
            "reason": "the editable decisions file comes with the review report (#749)",
        },
    }


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


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _val(n: dict) -> str:
    if n.get("value") is None:
        return "unavailable: " + n.get("reason", "")
    return str(n["value"])


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


def render_html(tr: dict) -> str:
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
        f"<tr><td>{_e(x['confidence'])}</td><td>{_e(x['kind'])}</td>"
        f"<td>{_e(x['description'])}</td></tr>"
        for x in rv["worst"]
    )
    dfl = "".join(
        f"<tr><td class=mono>{_e(d['where'])}</td><td>{_e(d['source'])}</td></tr>"
        for d in tr["defaults"]
    )
    more = rv["open"]["value"] - len(rv["worst"])
    parts = [
        "<!doctype html><html><head><meta charset=utf-8>",
        f"<title>matchline trust report: {_e(tr['building'])}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>Trust report: {_e(tr['building'])}</h1>",
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
        "<h2>Review decisions</h2>",
        f"<p>{_e(tr['decisions_file']['reason'])}</p>",
        "</body></html>",
    ]
    return "\n".join(p for p in parts if p) + "\n"


def _pdf(html_path: Path) -> Optional[str]:
    """Render the PDF with LibreOffice; returns why not when it can't."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return "LibreOffice (soffice) not installed"
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(html_path.parent),
             str(html_path)],
            check=True, capture_output=True, timeout=180,
        )  # fmt: skip
    except (subprocess.SubprocessError, OSError) as e:
        return f"LibreOffice failed: {e}"
    if not html_path.with_suffix(".pdf").exists():
        return "LibreOffice produced no PDF"
    return None


def build_package(
    out_dir: Path,
    model,
    report,
    convention: dict,
    files: Iterable[Path],
    inputs: Iterable[dict] = (),
    pdf: bool = True,
) -> Path:
    """Write ``out_dir/package``; returns its path."""
    pkg = Path(out_dir) / "package"
    pkg.mkdir(parents=True, exist_ok=True)
    for f in files:
        if f and Path(f).exists():
            shutil.copy2(f, pkg / Path(f).name)
    (pkg / "convention_report.json").write_text(json.dumps(convention, indent=2, default=str))
    tr = build_trust_report(model, report, convention, inputs)
    (pkg / "trust_report.json").write_text(json.dumps(tr, indent=2, default=str))
    html_path = pkg / "trust_report.html"
    html_path.write_text(render_html(tr))
    (pkg / "DISCLAIMER.txt").write_text(DISCLAIMER + "\n")
    why = _pdf(html_path) if pdf else "PDF not requested"
    manifest = {
        "schema": "matchline.package/1",
        "files": {
            p.name: sha256(p)
            for p in sorted(pkg.iterdir())
            if p.is_file() and p.name != "manifest.json"
        },
        "pdf": {"produced": why is None, **({"reason": why} if why else {})},
    }
    (pkg / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return pkg
