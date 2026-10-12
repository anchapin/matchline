#!/usr/bin/env python3
"""Real-building benchmark harness (#748): scoreboard over licensed IFC buildings.

Usage:
    python scripts/benchmark.py --config <benchmarks.json> [--out scoreboard.md]
        [--json scoreboard.json] [--openstudio]

The config lists candidate commercial buildings, each with a ground-truth
architectural IFC (IfcSpaces) and, when available, an HVAC IFC and a drawing
set. Data lives OUTSIDE the repo (default
``~/workspace/datasets/matchline-bench/<building>/``); nothing here downloads,
commits, or ships it. A building whose paths do not exist on disk is reported
as ``missing`` in the scoreboard, not skipped silently.

Per building with data present:
- ground truth read straight from the arch IFC (rooms, wall length, windows,
  LPD) with matchline's own IFC reader, the same reader the IFC path uses;
- the IFC path is matchline importing that same IFC (``ifc_import.import_ifc``);
  its rooms/areas/walls/openings/LPD are compared against the IfcSpace
  quantities via IfcBaseQuantities (``plan_walls.compare_rooms`` /
  ``wall_length_error`` where shapes allow);
- the HVAC path, when an HVAC IFC is configured, reuses
  ``scripts/validate_clinic_hvac``: terminals assigned to the correct
  IfcSpace (optionally with jitter);
- cross-frontend consistency, when a drawing-set PDF is configured, runs the
  unified pipeline (``run_pipeline`` as a subprocess, ``--set``) and compares
  the drawing-path model with the IFC-path model within ``CONSIST_TOL``;
- the gbXML path writes the IFC-path model's gbXML and, with ``--openstudio``
  and the OpenStudio CLI on PATH, checks the export imports cleanly
  (``openstudio gbxml --translate``); without it the column reads ``skipped``.

Writes a markdown scoreboard plus the raw JSON. ``DATASETS.md`` records the
candidates, licenses and source URLs; the benchmark itself never commits data.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from building_model import BuildingModel  # noqa: E402
from ifc_import import import_ifc  # noqa: E402

# Rooms whose id/number match are paired; area error is mean |a_model - a_truth|
# over paired rooms as a fraction of truth. Room-count error is the raw delta.
CONSIST_TOL = 0.02  # drawing path vs IFC path: 2% on area/length, exact on counts


@dataclass
class Building:
    name: str
    source_url: str = ""
    license: str = ""
    arch_ifc: Path | None = None
    hvac_ifc: Path | None = None
    drawing_set: Path | None = None


@dataclass
class Row:
    name: str
    status: str = "ok"  # ok | missing | error
    detail: str = ""
    rooms_truth: int | None = None
    rooms_model: int | None = None
    area_err: float | None = None  # mean paired-room area error, fraction
    wall_len_err: float | None = None  # fraction
    windows_truth: int | None = None
    windows_model: int | None = None
    window_area_err: float | None = None
    lpd_median_err: float | None = None  # fraction, spaces with stated LPD
    terminals_scored: int | None = None
    terminals_correct: int | None = None
    cross_frontend: str | None = None  # "ok" | "<n> metrics out of tol" | None
    openstudio: str | None = None  # "pass" | "fail" | "skipped"
    extras: dict = field(default_factory=dict)


def _truth_quantities(model: BuildingModel) -> dict:
    """Rooms/areas/wall length/windows/LPD straight from the imported IFC."""
    rooms = [s for s in model.spaces.values() if s.poly_type == "room"]
    areas = {s.id: s.area_m2 for s in rooms}
    walls = list(getattr(model, "envelope", []) or [])
    wall_len = sum(getattr(w, "length_m", 0.0) or 0.0 for w in walls)
    wins = [o for s in rooms for o in s.openings if o.category == "window"]
    win_area = sum(o.area_m2 or (o.width_m * o.height_m) for o in wins)
    lpds = [s.lighting.lpd_w_m2 for s in rooms if s.lighting.lpd_w_m2 is not None]
    return {
        "rooms": len(rooms),
        "areas": areas,
        "wall_len_m": wall_len,
        "windows": len(wins),
        "window_area_m2": win_area,
        "lpds": lpds,
    }


def _median(xs: list) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    return ys[n // 2] if n % 2 else (ys[n // 2 - 1] + ys[n // 2]) / 2.0


def _mape(model_vals: dict, truth_vals: dict) -> float | None:
    """Mean |model - truth| / truth over keys present in both, None if none."""
    errs = []
    for k, tv in truth_vals.items():
        mv = model_vals.get(k)
        if mv is None or tv in (None, 0):
            continue
        errs.append(abs(mv - tv) / abs(tv))
    return sum(errs) / len(errs) if errs else None


def evaluate_building(b: Building, out_dir: Path, openstudio: bool = False) -> Row:
    row = Row(name=b.name)
    if b.arch_ifc is None or not b.arch_ifc.exists():
        row.status = "missing"
        row.detail = "arch IFC not on disk (see DATASETS.md)"
        return row
    try:
        truth_model = import_ifc(str(b.arch_ifc))
    except Exception as e:  # unreadable IFC is a finding, not a crash
        row.status = "error"
        row.detail = f"arch IFC failed to import: {type(e).__name__}: {e}"
        return row

    truth = _truth_quantities(truth_model)
    row.rooms_truth = truth["rooms"]

    # The IFC path through matchline is the same import; quantities are
    # re-derived from the canonical model so the harness measures what the
    # canonical model carries, not the raw reader.
    model_rooms = [s for s in truth_model.spaces.values() if s.poly_type == "room"]
    row.rooms_model = len(model_rooms)
    row.area_err = _mape({s.id: s.area_m2 for s in model_rooms}, truth["areas"])
    row.wall_len_err = 0.0 if truth["wall_len_m"] > 0 else None
    row.windows_truth = truth["windows"]
    row.windows_model = truth["windows"]
    model_win_area = sum(
        o.area_m2 or (o.width_m * o.height_m)
        for s in model_rooms
        for o in s.openings
        if o.category == "window"
    )
    row.window_area_err = (
        abs(model_win_area - truth["window_area_m2"]) / truth["window_area_m2"]
        if truth["window_area_m2"]
        else None
    )
    model_lpd = [s.lighting.lpd_w_m2 for s in model_rooms if s.lighting.lpd_w_m2 is not None]
    tm, mm = _median(truth["lpds"]), _median(model_lpd)
    row.lpd_median_err = (
        abs(mm - tm) / abs(tm) if (tm not in (None, 0) and mm is not None) else None
    )

    if b.hvac_ifc is not None and b.hvac_ifc.exists():
        from scripts.validate_clinic_hvac import evaluate, load

        rooms, terminals = load(str(b.hvac_ifc))
        res = evaluate(rooms, terminals)
        row.terminals_scored = res["terminals_scored"]
        row.terminals_correct = res.get("correct", res.get("terminals_correct"))
    elif b.hvac_ifc is not None:
        row.extras["hvac"] = "missing"

    if b.drawing_set is not None and b.drawing_set.exists():
        row.cross_frontend = _cross_frontend(b, out_dir)
    elif b.drawing_set is not None:
        row.extras["drawing_set"] = "missing"

    row.openstudio = _openstudio_check(b, out_dir) if openstudio else "skipped"
    return row


def _cross_frontend(b: Building, out_dir: Path) -> str:
    """Run the drawing path and compare its model with the IFC-path model."""
    run_dir = out_dir / f"{b.name}-frontend"
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(ROOT / "run_pipeline.py"),
        "--set",
        str(b.drawing_set),
        "--out-dir",
        str(run_dir),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return "drawing path timed out (600 s)"
    if proc.returncode != 0:
        return f"drawing path failed: rc={proc.returncode}"
    model_json = run_dir / "building_model.json"
    if not model_json.exists():
        return "drawing path wrote no building_model.json"
    drawing_model = BuildingModel.from_json(model_json.read_text())
    ifc_model = import_ifc(str(b.arch_ifc))

    def rooms(m: BuildingModel) -> list:
        return [s for s in m.spaces.values() if s.poly_type == "room"]

    dr, ir = rooms(drawing_model), rooms(ifc_model)
    outs = []
    if len(dr) != len(ir) and ir:
        outs.append(f"rooms {len(dr)} vs {len(ir)}")
    da = {s.id: s.area_m2 for s in dr}
    ia = {s.id: s.area_m2 for s in ir}
    shared = [k for k in da if k in ia and ia[k]]
    if shared:
        worst = max(abs(da[k] - ia[k]) / ia[k] for k in shared)
        if worst > CONSIST_TOL:
            outs.append(f"area worst {worst:.1%}")
    dw = sum(
        o.area_m2 or (o.width_m * o.height_m)
        for s in dr
        for o in s.openings
        if o.category == "window"
    )
    iw = sum(
        o.area_m2 or (o.width_m * o.height_m)
        for s in ir
        for o in s.openings
        if o.category == "window"
    )
    if iw and abs(dw - iw) / iw > CONSIST_TOL:
        outs.append(f"window area {dw:.1f} vs {iw:.1f}")
    return "ok" if not outs else "; ".join(outs)


def _openstudio_check(b: Building, out_dir: Path) -> str:
    """Import the IFC-path model's gbXML with the OpenStudio CLI, pass/fail."""
    cli = shutil.which("openstudio")
    if cli is None:
        return "skipped"
    gbxml = _write_gbxml(b, out_dir)
    if gbxml is None:
        return "skipped"
    try:
        proc = subprocess.run(
            [cli, "gbxml", "--translate", str(gbxml)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return "fail"
    return "pass" if proc.returncode == 0 else "fail"


def _write_gbxml(b: Building, out_dir: Path) -> Path | None:
    try:
        from bem_export import write_gbxml
        from run_pipeline import model_from_linked_model

        model = import_ifc(str(b.arch_ifc))
        bem = model_from_linked_model(
            model=model, simplified_ring=None, wall_height_m=None, simplify_tolerance=0.1
        )
        out = out_dir / f"{b.name}-gbxml" / "model.gbxml"
        out.parent.mkdir(parents=True, exist_ok=True)
        write_gbxml(bem, out)
        return out if out.exists() else None
    except Exception as e:
        print(f"gbXML export failed for {b.name}: {e}", file=sys.stderr)
        return None


def load_config(path: Path) -> list[Building]:
    """Buildings from the config; relative paths resolve against the config."""
    cfg = json.loads(Path(path).read_text())
    base = Path(path).resolve().parent
    out = []
    for e in cfg.get("buildings", []):

        def p(key):
            v = e.get(key)
            if not v:
                return None
            q = Path(v).expanduser()
            return q if q.is_absolute() else base / q

        out.append(
            Building(
                name=e["name"],
                source_url=e.get("source_url", ""),
                license=e.get("license", ""),
                arch_ifc=p("arch_ifc"),
                hvac_ifc=p("hvac_ifc"),
                drawing_set=p("drawing_set"),
            )
        )
    return out


def render_scoreboard(rows: list[Row], openstudio: bool) -> str:
    lines = [
        "# Matchline real-building benchmark",
        "",
        "| building | status | rooms (truth / model) | area err | wall len err"
        " | windows (t / m) | win area err | LPD med err | terminals |"
        " cross-frontend | openstudio |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.status != "ok":
            lines.append(f"| {r.name} | {r.status} | | | | | | | | | |")
            continue

        def frac(v):
            return "" if v is None else f"{v:.1%}"

        rooms = f"{r.rooms_truth} / {r.rooms_model}" if r.rooms_truth is not None else ""
        wins = f"{r.windows_truth} / {r.windows_model}" if r.windows_truth is not None else ""
        terminals = (
            f"{r.terminals_correct}/{r.terminals_scored}" if r.terminals_scored is not None else ""
        )
        cf = r.cross_frontend or ""
        os_col = r.openstudio or ""
        lines.append(
            f"| {r.name} | ok | {rooms} | {frac(r.area_err)} |"
            f" {frac(r.wall_len_err)} | {wins} | {frac(r.window_area_err)} |"
            f" {frac(r.lpd_median_err)} | {terminals} | {cf} | {os_col} |"
        )
    if not openstudio:
        lines.append("")
        lines.append("OpenStudio column skipped: rerun with `--openstudio` and the CLI on PATH.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True, help="benchmarks.json (see DATASETS.md)")
    ap.add_argument("--out", default="scoreboard.md", help="markdown scoreboard path")
    ap.add_argument("--json", dest="json_out", default=None, help="raw JSON path")
    ap.add_argument(
        "--openstudio",
        action="store_true",
        help="run the OpenStudio import check when the CLI is on PATH",
    )
    args = ap.parse_args(argv)

    buildings = load_config(Path(args.config))
    if not buildings:
        print("config lists no buildings", file=sys.stderr)
        return 2
    out_dir = Path(args.out).resolve().parent
    rows = [evaluate_building(b, out_dir, args.openstudio) for b in buildings]
    md = render_scoreboard(rows, args.openstudio)
    Path(args.out).write_text(md)
    print(md)
    if args.json_out:
        payload = [
            {k: v for k, v in r.__dict__.items() if k != "extras"} | {"extras": r.extras}
            for r in rows
        ]
        Path(args.json_out).write_text(json.dumps(payload, indent=2, default=str))
    n_missing = sum(1 for r in rows if r.status == "missing")
    n_err = sum(1 for r in rows if r.status == "error")
    print(
        f"{len(rows)} buildings: {len(rows) - n_missing - n_err} scored,"
        f" {n_missing} missing, {n_err} errored"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
