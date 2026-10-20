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
- ground truth is independent of matchline's IFC reader (#1165): rooms and
  areas (``Qto_SpaceBaseQuantities``), exterior wall length
  (``Pset_WallCommon.IsExternal`` walls) and windows/doors are read with
  ifcopenshell directly by ``scripts/bench_truth.py`` and frozen as
  ``truth.json`` next to the IFC, pinned to the IFC's sha256;
- the IFC path is matchline importing that IFC (``ifc_import.import_ifc``);
  its rooms are paired with truth rooms by IfcSpace GlobalId, and its room
  areas, exterior wall length and window area are scored against
  ``truth.json``. A metric with no truth (e.g. no wall states IsExternal) is
  left blank, never reported as 0;
- the HVAC path, when an HVAC IFC is configured, reuses
  ``scripts/validate_clinic_hvac``: terminals assigned to the correct
  IfcSpace (optionally with jitter);
- cross-frontend consistency, when a drawing-set PDF is configured, runs the
  unified pipeline (``run_pipeline`` as a subprocess, ``--set``) and compares
  the drawing-path model with the IFC-path model within ``CONSIST_TOL``;
- the gbXML path writes the IFC-path model's gbXML on every run and reports
  the simplifier's surface counts (#972): envelope segments in/out, roof
  facets in/out, exported surfaces. ``--simplify-tol`` sets the envelope area
  budget (default 0.02, matching ``run_pipeline``). With ``--openstudio`` and
  the OpenStudio CLI on PATH it also checks the export imports cleanly
  (``openstudio gbxml --translate``); without it that column reads ``skipped``.

Writes a markdown scoreboard plus the raw JSON. ``DATASETS.md`` records the
candidates, licenses and source URLs; the benchmark itself never commits data.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from building_model import BuildingModel  # noqa: E402
from geometry.geometry_simplify import footprint_from_regions, simplify_ring  # noqa: E402
from ifc.ifc_import import import_ifc  # noqa: E402
from scripts.bench_truth import TruthMismatch, load_or_freeze_truth  # noqa: E402

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
    schema: str | None = None  # "IFC4", or "IFC2X3 (upgraded in memory)"
    rooms_truth: int | None = None
    rooms_model: int | None = None
    area_err: float | None = None  # mean paired-room area error, fraction
    wall_len_err: float | None = None  # fraction
    windows_truth: int | None = None
    windows_model: int | None = None
    window_area_err: float | None = None
    doors_truth: int | None = None
    doors_model: int | None = None
    lpd_median_err: float | None = None  # fraction, spaces with stated LPD
    terminals_scored: int | None = None
    terminals_correct: int | None = None
    cross_frontend: str | None = None  # "ok" | "<n> metrics out of tol" | None
    openstudio: str | None = None  # "pass" | "fail" | "skipped"
    walls_in: int | None = None  # envelope segments before simplification (#972)
    walls_out: int | None = None  # envelope segments after simplification
    roof_in: int | None = None  # roof facets before simplification
    roof_out: int | None = None  # roof facets after simplification
    surfaces_out: int | None = None  # <Surface> elements in the exported gbXML
    extras: dict = field(default_factory=dict)


def _wall_openings(model: BuildingModel) -> "Counter[str]":
    """Opening counts by category from the wall elements the IFC path carries.

    On the IFC path, openings attach to the imported wall elements (BimElements
    whose class is IfcWall*), not to spaces; counting from spaces reads an
    always-empty list there.
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for be in getattr(model, "bim_elements", None) or []:
        if not str(getattr(be, "ifc_class", "")).startswith("IfcWall"):
            continue
        for o in getattr(be, "openings", None) or []:
            counts[str(o.category)] += 1
    return counts


_ENVELOPE_WALL_METHODS = ("ifc_import:tier0:envelope", "ifc_import:tier1:facade")


def _space_global_id(space) -> str | None:
    """IfcSpace GlobalId recorded in a model space's provenance note, if any."""
    prov = getattr(space, "core_provenance", None)
    note = getattr(prov, "note", None) or ""
    m = re.search(r"GlobalId=(\S+)", note)
    return m.group(1) if m else None


def _model_quantities(model: BuildingModel) -> dict:
    """What matchline's IFC path produced: rooms by GlobalId, walls, windows, LPD."""
    rooms = [s for s in model.spaces.values() if s.poly_type == "room"]
    areas_by_gid = {}
    for s in rooms:
        gid = _space_global_id(s)
        if gid is not None and s.area_m2 is not None:
            areas_by_gid[gid] = s.area_m2
    walls = [
        w
        for w in (getattr(model, "envelope", []) or [])
        if getattr(getattr(w, "provenance", None), "method", None) in _ENVELOPE_WALL_METHODS
    ]
    wall_len = sum(getattr(w, "length_m", 0.0) or 0.0 for w in walls)
    wins = [
        o
        for be in (getattr(model, "bim_elements", None) or [])
        if str(getattr(be, "ifc_class", "")).startswith("IfcWall")
        for o in (getattr(be, "openings", None) or [])
        if o.category == "window"
    ]
    win_area = sum(getattr(o, "area_m2", None) or (o.width_m * o.height_m) for o in wins)
    return {
        "rooms": len(rooms),
        "areas_by_gid": areas_by_gid,
        "wall_len_m": wall_len,
        "windows": len(wins),
        "window_area_m2": win_area,
        "doors": _wall_openings(model)["door"],
        "lpds": [s.lighting.lpd_w_m2 for s in rooms if s.lighting.lpd_w_m2 is not None],
    }


def _rel_err(model_val: float | None, truth_val: float | None) -> float | None:
    """|model - truth| / truth, None when truth is missing or zero."""
    if truth_val in (None, 0) or model_val is None:
        return None
    return abs(model_val - truth_val) / abs(truth_val)


def score_against_truth(row: "Row", model_q: dict, truth: dict) -> None:
    """Fill the accuracy columns of ``row`` from model quantities vs frozen truth."""
    truth_areas = {
        gid: r["area_m2"] for gid, r in (truth.get("rooms") or {}).items() if r.get("area_m2")
    }
    row.rooms_truth = len(truth.get("rooms") or {})
    row.rooms_model = model_q["rooms"]
    row.area_err = _mape(model_q["areas_by_gid"], truth_areas)
    matched = [g for g in truth_areas if g in model_q["areas_by_gid"]]
    row.extras["rooms_matched"] = len(matched)
    if matched:
        t_total = sum(truth_areas[g] for g in matched)
        m_total = sum(model_q["areas_by_gid"][g] for g in matched)
        row.extras["matched_floor_area_err"] = _rel_err(m_total, t_total)
    sources = sorted({r.get("area_source") or "none" for r in (truth.get("rooms") or {}).values()})
    row.extras["truth_area_source"] = "+".join(sources) if sources else ""
    t_wall = truth.get("exterior_wall_length_m")
    row.wall_len_err = _rel_err(model_q["wall_len_m"], t_wall)
    if t_wall is None:
        row.extras["wall_len"] = truth.get("exterior_wall_reason") or "no exterior wall truth"
    else:
        row.extras["wall_len_m"] = {"truth": t_wall, "model": model_q["wall_len_m"]}
    row.windows_truth = truth.get("windows")
    row.windows_model = model_q["windows"]
    row.doors_truth = truth.get("doors")
    row.doors_model = model_q["doors"]
    row.window_area_err = _rel_err(model_q["window_area_m2"], truth.get("window_area_m2"))


def _raw_entity_counts(path: Path) -> dict:
    """Windows/doors straight from the IFC file's entity table.

    Independent of the canonical model: the truth columns measure the
    building, not our own import. Openings folded during wall/host
    processing (#578, #587) then show up as truth > model instead of
    vanishing into a tautological tie.
    """
    import ifcopenshell

    f = ifcopenshell.open(str(path))
    return {
        "windows": len(f.by_type("IfcWindow")),
        "doors": len(f.by_type("IfcDoor")),
    }


def _hvac_columns(hvac_ifc: Path) -> tuple[int, int]:
    """(terminals_scored, terminals_correct) from the HVAC-terminal check.

    ``validate_clinic_hvac.evaluate`` returns the per-storey ``summarize()``
    shape (terminals/correct/wrong/review keyed by storey), not the CLI's
    flat report; the scored count is the terminal list length and the
    correct count sums across storeys. (#964 regression: the harness had
    only ever run with no hvac_ifc on disk, so the mismatch was never hit.)
    """
    from scripts.validate_clinic_hvac import evaluate, load

    rooms, terminals = load(str(hvac_ifc))
    per_storey = evaluate(rooms, terminals)
    return len(terminals), sum(v["correct"] for v in per_storey.values())


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


def evaluate_building(
    b: Building, out_dir: Path, openstudio: bool = False, simplify_tol: float = 0.02
) -> Row:
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

    row.schema = getattr(truth_model, "source_schema", None) or "IFC4"
    # Truth is read from the IFC with ifcopenshell alone and frozen next to
    # it (#1165), so the IFC path is no longer scored against itself.
    try:
        truth, how = load_or_freeze_truth(b.arch_ifc)
    except TruthMismatch as e:
        row.status = "error"
        row.detail = str(e)
        return row
    except Exception as e:  # truth that cannot be read is a finding
        row.status = "error"
        row.detail = f"ground truth failed: {type(e).__name__}: {e}"
        return row
    row.extras["truth"] = how
    model_q = _model_quantities(truth_model)
    score_against_truth(row, model_q, truth)
    model_rooms = [s for s in truth_model.spaces.values() if s.poly_type == "room"]
    # LPD has no independent truth yet: both sides come from the import, so
    # this column stays a self-consistency check (labelled in DATASETS.md).
    model_lpd = [s.lighting.lpd_w_m2 for s in model_rooms if s.lighting.lpd_w_m2 is not None]
    tm, mm = _median(model_q["lpds"]), _median(model_lpd)
    row.lpd_median_err = (
        abs(mm - tm) / abs(tm) if (tm not in (None, 0) and mm is not None) else None
    )

    if b.hvac_ifc is not None and b.hvac_ifc.exists():
        row.terminals_scored, row.terminals_correct = _hvac_columns(b.hvac_ifc)
    elif b.hvac_ifc is not None:
        row.extras["hvac"] = "missing"

    if b.drawing_set is not None and b.drawing_set.exists():
        row.cross_frontend = _cross_frontend(b, out_dir)
    elif b.drawing_set is not None:
        row.extras["drawing_set"] = "missing"

    simp = _simplify_model(truth_model, simplify_tol)
    row.walls_in = simp["walls_in"]
    row.walls_out = simp["walls_out"]
    row.roof_in = simp["roof_in"]
    row.roof_out = simp["roof_out"]
    gbxml = _write_gbxml(b, out_dir, truth_model, simp["sres"], simp["wall_height"], simplify_tol)
    if gbxml is not None:
        row.surfaces_out = _gbxml_surface_counts(gbxml)
    row.openstudio = _openstudio_check(b, out_dir, gbxml) if openstudio else "skipped"
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


def _simplify_model(model: BuildingModel, simplify_tol: float) -> dict:
    """Envelope + roof simplification on an imported model (#972).

    Returns counts for the scoreboard's surface columns plus the
    SimplifyResult and wall height the gbXML export reuses. Counts are None
    when the model has no usable footprint or no roof facets.
    """
    out: dict = {
        "walls_in": None,
        "walls_out": None,
        "roof_in": None,
        "roof_out": None,
        "sres": None,
        "wall_height": None,
    }
    try:
        polys = [sp.polygon_m for sp in model.spaces.values() if getattr(sp, "polygon_m", None)]
        wall_h = getattr(model.levels[0], "wall_height_m", None) if model.levels else None
        ring = footprint_from_regions(polys)
        if ring and len(ring) >= 3:
            sres = simplify_ring(ring, tol=simplify_tol, wall_height=wall_h)
            out.update(
                walls_in=sres.original_count,
                walls_out=sres.simplified_count,
                sres=sres,
                wall_height=wall_h,
            )
    except Exception as e:  # a building that cannot simplify is a finding
        print(f"simplification failed: {type(e).__name__}: {e}", file=sys.stderr)
    try:
        from geometry.roof_simplify import apply_roof_simplification

        rres = apply_roof_simplification(model)
        if rres is not None and rres.n_before:
            out["roof_in"], out["roof_out"] = rres.n_before, rres.n_after
    except Exception as e:
        print(f"roof simplification unavailable: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def _gbxml_surface_counts(path: Path) -> int | None:
    """Total <Surface> elements in an exported gbXML file (#972)."""
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(str(path))
    except Exception:
        return None
    return sum(1 for el in tree.iter() if el.tag.split("}")[-1] == "Surface")


def _openstudio_check(b: Building, out_dir: Path, gbxml: Path | None) -> str:
    """Import the IFC-path model's gbXML with the OpenStudio CLI, pass/fail."""
    cli = shutil.which("openstudio")
    if cli is None:
        return "skipped"
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


def _write_gbxml(
    b: Building,
    out_dir: Path,
    model: BuildingModel,
    sres=None,
    wall_height: float | None = None,
    simplify_tol: float = 0.02,
) -> Path | None:
    try:
        from bem.bem_export import write_gbxml
        from run_pipeline import model_from_linked_model

        # #972: the export exercises the real stage-3 simplification (the
        # caller's already-imported model, no second IFC read); the old
        # hardcoded simplified_ring=None / tolerance=0.1 bypassed it.
        bem = model_from_linked_model(
            model=model,
            simplified_ring=sres,
            wall_height_m=wall_height,
            simplify_tolerance=simplify_tol,
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
        "| building | schema | status | rooms (truth / model) | area err | wall len err"
        " | windows (t / m) | doors (t / m) | win area err | LPD med err | terminals |"
        " envelope (in / out) | roof (in / out) | surfaces | cross-frontend | openstudio |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r.status != "ok":
            lines.append(f"| {r.name} | {r.schema or ''} | {r.status} | | | | | | | | | | | | | |")
            continue

        def frac(v):
            return "" if v is None else f"{v:.1%}"

        rooms = f"{r.rooms_truth} / {r.rooms_model}" if r.rooms_truth is not None else ""
        wins = f"{r.windows_truth} / {r.windows_model}" if r.windows_truth is not None else ""
        doors = f"{r.doors_truth} / {r.doors_model}" if r.doors_truth is not None else ""
        terminals = (
            f"{r.terminals_correct}/{r.terminals_scored}" if r.terminals_scored is not None else ""
        )
        env = f"{r.walls_in} / {r.walls_out}" if r.walls_in is not None else ""
        roof = f"{r.roof_in} / {r.roof_out}" if r.roof_in is not None else ""
        surf = str(r.surfaces_out) if r.surfaces_out is not None else ""
        cf = r.cross_frontend or ""
        os_col = r.openstudio or ""
        lines.append(
            f"| {r.name} | {r.schema or ''} | ok | {rooms} | {frac(r.area_err)} |"
            f" {frac(r.wall_len_err)} | {wins} | {doors} | {frac(r.window_area_err)} |"
            f" {frac(r.lpd_median_err)} | {terminals} | {env} | {roof} | {surf}"
            f" | {cf} | {os_col} |"
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
    ap.add_argument(
        "--simplify-tol",
        dest="simplify_tol",
        type=float,
        default=0.02,
        help="envelope simplification area budget, fraction (default 0.02, matches run_pipeline)",
    )
    args = ap.parse_args(argv)

    buildings = load_config(Path(args.config))
    if not buildings:
        print("config lists no buildings", file=sys.stderr)
        return 2
    out_dir = Path(args.out).resolve().parent
    rows = [evaluate_building(b, out_dir, args.openstudio, args.simplify_tol) for b in buildings]
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
