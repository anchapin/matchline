"""matchline: unified command-line interface.

Every demo/evaluation script in this repo is reachable as a subcommand::

    matchline validate
    matchline bem-export --sheet-id sheet_007
    matchline facade-takeoff --n 5 --full
    matchline ifc-import building.ifc --out model.json
    matchline ifc-export model.json out.ifc

The historical ``python3 run_*.py`` scripts still work; they are thin
wrappers around the same functions.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_CONFIDENCE_THRESHOLD = 0.75


def check_range_0_1(value: str) -> float:
    """Parse and validate a float in range [0.0, 1.0] for argparse."""
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float value: {value!r}")
    if not (0.0 <= f <= 1.0):
        raise argparse.ArgumentTypeError(f"{value} is out of range [0.0, 1.0]")
    return f


def _check_wall_height(v) -> str:
    """Why a config ``wall_height`` is unusable, or "" (a positive number, or a map of
    level id to positive number, #821)."""
    if v is None:
        return ""

    def pos(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0

    if isinstance(v, dict):
        if not v:
            return "is an empty map"
        bad = [str(k) for k, x in v.items() if not pos(x)]
        return f"must be positive metres for each level (check {', '.join(bad)})" if bad else ""
    return "" if pos(v) else "must be a positive number of metres or a map of level to metres"


def cmd_run(args: argparse.Namespace) -> None:
    """Unified pipeline: generate + link + validate + BEM export."""
    import run_pipeline

    inputs = {
        "--seed": args.seed is not None,
        "--aec-bench": args.aec_bench is not None,
        "--set": getattr(args, "set_pdf", None) is not None,
        "--ifc": getattr(args, "ifc_path", None) is not None,
    }
    named = ", ".join(name for name, given in inputs.items() if given)
    if sum(inputs.values()) != 1:
        print(
            f"Error: exactly one of --seed, --aec-bench, --set or --ifc is required"
            f" (got: {named or 'none'})",
            file=sys.stderr,
        )
        sys.exit(1)
    config = None
    if args.config_path:
        from pathlib import Path

        import yaml

        config_path = Path(args.config_path)
        if not config_path.exists():
            print(f"Error: config file not found: {config_path}", file=sys.stderr)
            sys.exit(1)
        with config_path.open() as f:
            config = yaml.safe_load(f) or {}
        known_keys = {
            "simplify_tolerance",
            "wall_height",
            "review_confidence",
            "datasets",
            "telemetry_consent",
            "telemetry_local_only",
        }
        unknown = set(config.keys()) - known_keys
        if unknown:
            print(f"Warning: unknown config keys ignored: {sorted(unknown)}", file=sys.stderr)
        bad = _check_wall_height(config.get("wall_height"))
        if bad:
            print(f"Error: config wall_height {bad}", file=sys.stderr)
            sys.exit(1)

    confidence_threshold = args.confidence_threshold
    if confidence_threshold is None:
        env_val = os.environ.get("MATCHLINE_CONFIDENCE_THRESHOLD")
        if env_val is not None:
            try:
                confidence_threshold = float(env_val)
            except ValueError:
                print(
                    f"Error: MATCHLINE_CONFIDENCE_THRESHOLD is not a valid float: {env_val}",
                    file=sys.stderr,
                )
                sys.exit(1)
    if confidence_threshold is None:
        confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD

    if config is None:
        config = {}
    config["review_confidence"] = confidence_threshold
    run_pipeline.main(args, config=config)
    if getattr(args, "baseline", None):
        out_dir = Path(args.out_dir)
        try:
            res = _baseline_diff(out_dir, Path(args.baseline))
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        rep = res["report"]
        rooms, windows, doors = rep["rooms"], rep["windows"], rep["doors"]
        print(
            f"takeoff-diff vs baseline {args.baseline}: "
            f"rooms +{rooms['added']}/-{rooms['removed']}, "
            f"windows +{windows['added']}/-{windows['removed']} ({windows['moved']} moved), "
            f"doors +{doors['added']}/-{doors['removed']} ({doors['moved']} moved); "
            f"report: {out_dir / 'takeoff_diff.json'}; review items attached: {res['attached']}"
        )


def cmd_validate(args: argparse.Namespace) -> None:
    """Run the full validation battery (``validate.N_CHECKS`` checks) on synthetic buildings.

    Builds two synthetic buildings (3-room and 2-level), links them, then runs
    ``validate.run_checks()``. Any check with severity="error" (a conservation law
    violated) causes a non-zero exit. Results with needs_review=True block export
    via ``validate.export_gate``.
    """
    import run_validation

    run_validation.main()


def cmd_bem_export(args: argparse.Namespace) -> None:
    """Synthetic sheet GT -> takeoffs -> gbXML + IFC4 export (validated)."""
    import run_bem_export

    ok = run_bem_export.main(args.sheet_id)
    if not ok:
        raise SystemExit(1)


def cmd_elevation_windows(args: argparse.Namespace) -> None:
    """Generates SYNTHETIC window placement data for testing.

    Real extraction requires detector/YOLO pipeline.
    """
    import run_elevation_windows

    run_elevation_windows.main()


def cmd_facade_takeoff(args: argparse.Namespace) -> None:
    """Facade area takeoffs on the CMP Facade dataset (needs external data)."""
    import run_facade_takeoff as rf

    rf.demo_facades(n=args.n, data_root=args.data_root)
    if args.full:
        rf.full_sweep(data_root=args.data_root, out_json=args.out_json)


def cmd_room_labels(args: argparse.Namespace) -> None:
    """OCR room labeling on a synthetic sheet."""
    import run_room_labels

    run_room_labels.main()


def cmd_multidiscipline(args: argparse.Namespace) -> None:
    """End-to-end: link 3 synthetic buildings across disciplines."""
    import run_multidiscipline

    run_multidiscipline.main()


def cmd_mnist(args: argparse.Namespace) -> None:
    """WiSARD MNIST evaluation (paper Section 3; needs data/mnist_*.npy)."""
    import run_mnist

    run_mnist.main(data_dir=args.data_dir, out_path=args.out_path)


def cmd_symbols(args: argparse.Namespace) -> None:
    """Synthetic symbol evaluation + GD&T topological invariant check."""
    import run_symbols

    run_symbols.main(out_path=args.out_path)


def cmd_ifc_import(args: argparse.Namespace) -> None:
    """Import an IFC file (Tier 0: no space boundaries needed) into the
    canonical building model; print a summary, optionally write JSON."""
    from pathlib import Path

    from ifc.ifc_import import import_ifc

    inp = Path(args.path)
    if not inp.is_absolute():
        inp_resolved = inp.resolve()
        cwd_resolved = Path.cwd().resolve()
        try:
            inp_resolved.relative_to(cwd_resolved)
        except ValueError:
            raise ValueError(
                f"Input path '{inp}' resolves to '{inp_resolved}' which escapes "
                f"the working directory '{cwd_resolved}'. "
                "Rejecting to prevent path traversal."
            )
    if not inp.is_file():
        raise ValueError(f"Input path '{inp}' is not a file or does not exist.")

    model = import_ifc(args.path)
    n_open = sum(len(sp.openings) for sp in model.spaces.values())
    n_bim = len(getattr(model, "bim_elements", []) or [])
    print(f"levels: {len(model.levels)}  spaces: {len(model.spaces)}  zones: {len(model.zones)}")
    print(f"space openings: {n_open}  BIM elements: {n_bim}")
    area = sum(sp.area_m2 for sp in model.spaces.values())
    print(f"total space area: {area:.1f} m2")
    if model.review_queue:
        print(f"review queue: {len(model.review_queue)} items")
        for item in list(model.review_queue)[:10]:
            print(f"  - {item.kind}: {item.description}")
    if args.out:
        outp = Path(args.out)
        if not outp.is_absolute():
            outp_resolved = outp.resolve()
            cwd_resolved = Path.cwd().resolve()
            try:
                outp_resolved.relative_to(cwd_resolved)
            except ValueError:
                raise ValueError(
                    f"Output path '{outp}' resolves to '{outp_resolved}' which escapes "
                    f"the working directory '{cwd_resolved}'. "
                    "Rejecting to prevent path traversal."
                )
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(model.to_json(), encoding="utf-8")
        print(f"wrote {args.out}")


def cmd_ingest(args: argparse.Namespace) -> None:
    """Read a PDF drawing set into per-page sheet records (#737)."""
    from pathlib import Path

    from ingest.pdf_ingest import ingest_pdf

    res = ingest_pdf(Path(args.path), out_dir=args.out, dpi=args.dpi, rasterize=not args.no_raster)
    kinds: dict = {}
    for s in res.sheets:
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    print(f"pages: {res.n_pages}  " + "  ".join(f"{k}: {v}" for k, v in sorted(kinds.items())))
    for s in res.sheets:
        print(
            f"  page {s.page_number:3d}  {s.kind:11s}  {s.width_pt:.0f}x{s.height_pt:.0f} pt"
            f"  rot {s.rotation:3d}  paths {len(s.primitives):6d}  text {len(s.text):5d}"
        )
    for w in res.warnings:
        print(f"WARNING {w}")
    print(f"wrote {args.out}/ingest.json")


def cmd_export_package(args: argparse.Namespace) -> None:
    """Verify an export package's manifest and build its share zip (#999)."""
    import export_package as E

    try:
        z = E.export_zip(Path(args.out_dir))
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  zip: {z}")
    return z


def cmd_scale(args: argparse.Namespace) -> None:
    """Read each ingested sheet's drawing scale (#739); writes scale.json."""
    from ingest.drawing_scale import scale_sheets

    res = scale_sheets(args.sheets)
    for name, sc in res.items():
        if sc["ratio"]:
            what = f"1:{sc['ratio']:.4g} from {sc['source']}  conf {sc['confidence']:.2f}"
        else:
            what = sc["reason"]
        flag = "  REVIEW" if sc["needs_review"] else ""
        print(f"  {name}  {what}{flag}")
        for w in sc["warnings"]:
            print(f"    WARNING {w}")
    print(f"wrote {args.sheets}/scale.json")


def cmd_index(args: argparse.Namespace) -> None:
    """Index an ingested drawing set by discipline, type and level (#738)."""
    from ingest.sheet_index import index_sheets

    idx = index_sheets(args.sheets)
    for e in idx.sheets:
        num = e.value("number") or "?"
        kind = e.value("type") or "?"
        lvl = e.value("level") or "-"
        use = "takeoff" if e.use_for_takeoff else ""
        flag = "  REVIEW" if e.needs_review else ""
        print(f"  {e.file}  {num:<8} {kind:<14} {lvl:<5} {use:<7} {e.value('title') or ''}{flag}")
    for lvl, by in idx.levels.items():
        pairs = "  ".join(f"{d}: {', '.join(n)}" for d, n in by.items())
        print(f"  {lvl}: {pairs}")
    for w in idx.warnings:
        print(f"  WARNING {w}")
    print(f"wrote {args.sheets}/sheet_index.json")


def cmd_schedules(args: argparse.Namespace) -> None:
    """Schedule tables (door, window, lighting, mechanical) from ingested sheets."""
    from ingest.pdf_schedules import schedules_for_sheets

    if not Path(args.sheets).is_dir():
        print(f"Error: sheets folder not found: {args.sheets}", file=sys.stderr)
        sys.exit(1)
    res = schedules_for_sheets(args.sheets)
    for name, r in res.items():
        for s in r["schedules"]:
            n = len(s["rows"])
            why = f"  NOT READ: {s['reason']}" if s["status"] != "ok" else ""
            print(f"  {name}  {s['kind']:<10} {s['title']}  rows {n}{why}")
    print(f"wrote schedules_NNN.json in {args.sheets}")


def cmd_walls(args: argparse.Namespace) -> None:
    """Walls and rooms from each takeoff floor plan in an ingest folder (#740)."""
    from plan_walls import walls_for_sheets

    res = walls_for_sheets(args.sheets)
    if not res:
        print("  no sheets to read (run `matchline scale` and `matchline index` first?)")
    for name, r in res.items():
        st = r["stats"]
        n_rev = len(r["review"])
        print(
            f"  {name}  walls {st.get('walls', 0)}  openings {st.get('openings', 0)}  "
            f"rooms {st.get('rooms', 0)}  review {n_rev}"
        )
        for item in r["review"][:5]:
            print(f"    REVIEW {item['reason']}")
    print(f"wrote walls_NNN.json in {args.sheets}")


def cmd_ifc_export(args: argparse.Namespace) -> None:
    """Export a BuildingModel JSON file to an IFC4 file."""
    from ifc.ifc_export import export_ifc

    export_ifc(args.model, args.out)
    print(f"wrote {args.out}")


def cmd_demo(args: argparse.Namespace) -> None:
    """One-command demo (#753): sample project in, package with trust report out.

    Runs the same unified pipeline as ``matchline run --seed`` on a fixed
    sample seed, then points at the finished package: trust report, review
    report and gbXML/IFC, all in one directory.
    """
    import run_pipeline

    out = Path(args.out)
    run_args = build_parser().parse_args(["run", "--seed", str(args.seed), "--out-dir", str(out)])
    run_pipeline.main(run_args)

    pkg = out / "package"
    trust = pkg / "trust_report.html"
    review = out / "review" / "review.html"
    exports = sorted(p.name for pat in ("*.xml", "*.ifc") for p in pkg.glob(pat))
    print()
    print(f"Demo finished. Everything is in {out}/")
    print(f"  trust report:  {trust}")
    print(f"  review report: {review}")
    print(f"  model files:   {', '.join(exports) if exports else '(none)'}")
    if not args.no_open and sys.stdout.isatty() and not os.environ.get("CI"):
        import webbrowser

        webbrowser.open(trust.resolve().as_uri())
        print("Opened the trust report in your browser.")


def cmd_diff(args: argparse.Namespace) -> None:
    """Compare two model revisions and report changed/unchanged zones."""
    import json

    from building_model import BuildingModel
    from diff import diff_models

    try:
        with open(args.old) as f:
            old_model = BuildingModel.from_json(f.read())
        with open(args.new) as f:
            new_model = BuildingModel.from_json(f.read())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    summary = diff_models(old_model, new_model)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary.to_dict(), f, indent=2)
    else:
        print(json.dumps(summary.to_dict(), indent=2))


def _baseline_diff(out_dir: Path, baseline_dir: Path) -> dict:
    """Diff a finished run against a baseline run's out-dir (#1077).

    Reads stage_02_model.json from both out-dirs and stage_00_set_report.json
    when present (it feeds the symbols section), writes takeoff_diff.json and
    takeoff_diff_review_items.json into out_dir, and attaches stable-id review
    items to the run's model queue: rerunning the same diff appends nothing.
    """
    import json
    from dataclasses import asdict

    import takeoff_diff
    from atomic_io import write_text_atomic
    from building_model import BuildingModel
    from review.review_report import attach_takeoff_diff_items

    old_path = baseline_dir / "stage_02_model.json"
    new_path = out_dir / "stage_02_model.json"
    missing = [str(p) for p in (old_path, new_path) if not p.exists()]
    if missing:
        raise FileNotFoundError(f"baseline diff needs {', '.join(missing)}")
    old = json.loads(old_path.read_text())
    new = json.loads(new_path.read_text())

    def _report(d: Path):
        p = d / "stage_00_set_report.json"
        return json.loads(p.read_text()) if p.exists() else None

    rep = takeoff_diff.diff_takeoffs(
        old, new, old_report=_report(baseline_dir), new_report=_report(out_dir)
    )
    write_text_atomic(out_dir / "takeoff_diff.json", json.dumps(rep, indent=2) + "\n")
    items = takeoff_diff.review_items(rep)
    write_text_atomic(
        out_dir / "takeoff_diff_review_items.json", json.dumps(items, indent=2) + "\n"
    )
    model = BuildingModel.from_dict(new)
    attached = attach_takeoff_diff_items(model, rep)
    if attached:
        # persist only the queue back into the run's own stage file, so
        # `matchline review <out-dir>/stage_02_model.json` sees the items and
        # every other serialized key stays exactly as the pipeline wrote it
        new["model"]["review_queue"] = [asdict(i) for i in model.review_queue]
        write_text_atomic(new_path, json.dumps(new, indent=2) + "\n")
    return {"report": rep, "attached": len(attached)}


def cmd_takeoff_diff(args: argparse.Namespace) -> None:
    """Diff two revisions' takeoffs (rooms, windows, doors, symbols) (#993)."""
    import json

    import takeoff_diff

    old = json.loads(Path(args.old).read_text())
    new = json.loads(Path(args.new).read_text())
    old_report = json.loads(Path(args.old_report).read_text()) if args.old_report else None
    new_report = json.loads(Path(args.new_report).read_text()) if args.new_report else None
    rep = takeoff_diff.diff_takeoffs(old, new, old_report=old_report, new_report=new_report)
    text = json.dumps(rep, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.review_items:
        items = json.dumps(takeoff_diff.review_items(rep), indent=2)
        Path(args.review_items).write_text(items + "\n", encoding="utf-8")
    print(text)


def cmd_feedback(args: argparse.Namespace) -> None:
    """Open-ended feedback and feedback-log summaries (#1005, #1038)."""
    import json as json_mod

    from review.feedback_capture import FeedbackCaptureError, FeedbackLog, summarize

    log = FeedbackLog(args.log)
    if args.text is not None:
        try:
            log.log_open_response(args.text, topic=args.topic)
        except FeedbackCaptureError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Feedback recorded: {log.path}")
        return
    if args.summary:
        events = log.read()
        if not events:
            print("No feedback recorded yet.")
            return
        print(json_mod.dumps(summarize(events), indent=2))
        return
    print("Error: give --text to record feedback, or --summary to read the log.", file=sys.stderr)
    sys.exit(2)


def cmd_review(args: argparse.Namespace) -> None:
    """Review queue: list open review items, confirm or reject decisions."""
    import os

    import run_review

    if args.enable_auto_triage or os.environ.get("ENABLE_AUTO_TRIAGE", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        args.auto_triage = True
    else:
        args.auto_triage = False

    run_review.main(args)


def cmd_viewer(args: argparse.Namespace) -> None:
    """Write the read-only 3D review viewer page for a model (#1001)."""
    import review.corrections as corrections_mod
    import review.review_viewer as review_viewer
    from building_model import BuildingModel

    model = BuildingModel.from_json(Path(args.model).read_text())
    cpath = corrections_mod.path_for(args.model)
    cdoc = corrections_mod.read(cpath) if cpath.exists() else None
    scene = review_viewer.build_scene(model, corrections_doc=cdoc)
    page = review_viewer.page_html(scene, title=f"matchline review viewer: {model.name}")
    Path(args.out).write_text(page, encoding="utf-8")
    print(f"wrote {args.out}")


def cmd_viewer_apply(args: argparse.Namespace) -> None:
    """Apply a viewer corrections ops file through the decisions workflow (#1002)."""
    import json

    import review.viewer_corrections as viewer_corrections
    from building_model import BuildingModel

    model_path = Path(args.model)
    try:
        model = BuildingModel.from_json(model_path.read_text())
        doc = viewer_corrections.read(args.ops)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    summary = viewer_corrections.apply_ops(model, doc, model_path)
    dest = Path(args.out) if args.out else model_path
    dest.write_text(model.to_json(), encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


def cmd_learn_legend(args: argparse.Namespace) -> None:
    """Learn a project's legend tags from a stage set report (#995)."""
    import json

    from legend_learning import (
        SIDECAR_NAME,
        LegendTagsError,
        add_tags,
        learn_from_set_report,
        save_tags,
        suggest_tags,
    )

    path = Path(args.report)
    if path.is_dir():
        path = path / "stage_00_set_report.json"
    report = json.loads(path.read_text())
    tags, conflicts, unmapped = learn_from_set_report(report)
    try:
        tags = add_tags(tags, args.tag)
    except LegendTagsError as e:
        sys.exit(f"error: {e}")
    out = Path(args.out) if args.out else path.parent / SIDECAR_NAME
    save_tags(out, tags)
    print(f"learned {len(tags)} legend tag(s) -> {out}")
    for c in conflicts:
        print(f"  conflict skipped: {c}")
    if unmapped:
        print("unmapped descriptions (map them with --tag):")
        for d in unmapped:
            print(f"  {d}")
        suggestions = suggest_tags(report)
        if suggestions:
            print("suggested --tag mappings (best token match in this report):")
            for s in suggestions:
                print(
                    f"  --tag {s['description']}={s['class']}"
                    f"  # matches {s['via']!r} ({s['score']:.0%})"
                )


def cmd_legend_eval(args: argparse.Namespace) -> None:
    """Before/after eval of a learned-tags run (#995): exits 1 on regression."""
    import json

    from legend_learning import eval_before_after

    before = json.loads(Path(args.before).read_text())
    after = json.loads(Path(args.after).read_text())
    r = eval_before_after(before, after)
    print(
        f"rows {r['rows_before']} -> {r['rows_after']}, "
        f"mapped {r['mapped_before']} -> {r['mapped_after']}, "
        f"newly mapped {r['newly_mapped']}"
    )
    for e in r["lost"]:
        print(f"  LOST {e['sheet']}: {e['description']} (was {e['was']})")
    for e in r["flipped"]:
        print(f"  FLIPPED {e['sheet']}: {e['description']} {e['was']} -> {e['now']}")
    if r["regressions"]:
        sys.exit(f"error: {r['regressions']} regression(s)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="matchline",
        description="Deterministic, auditable drawing/BIM -> BEM quantity takeoffs.",
    )
    sub = ap.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser(
        "validate",
        help="run validate.py battery of checks on a BEM model",
    )
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser(
        "run",
        help="unified pipeline: generate + link + validate + export",
    )
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--aec-bench",
        type=str,
        default=None,
        help="Path to AEC-Bench dataset root (contains annotations_15.xml)",
    )
    p.add_argument(
        "--set",
        type=Path,
        default=None,
        dest="set_pdf",
        help="real PDF drawing set, or a zip of PDFs (#988): ingest, index, scale, "
        "walls/rooms, then the model (mutually exclusive with --seed, --aec-bench and --ifc)",
    )
    p.add_argument(
        "--ifc",
        type=Path,
        default=None,
        dest="ifc_path",
        help="IFC model (#988): import, then the same simplify/validate/export stages "
        "(mutually exclusive with --seed, --aec-bench and --set)",
    )
    p.add_argument(
        "--detector-config",
        type=Path,
        default=None,
        dest="detector_config",
        help="JSON naming the door/window detection provider for --set (#743); "
        "see docs/detection_provider.md",
    )
    p.add_argument(
        "--storey-height",
        type=float,
        default=None,
        dest="storey_height",
        help="storey height in metres for --set when the set states none (default 3.0, flagged)",
    )
    p.add_argument(
        "--climate-zone",
        default=None,
        dest="climate_zone",
        help="ASHRAE climate zone (e.g. 4A) for the construction library (#747); "
        "unset constructions stay unset without one",
    )
    p.add_argument(
        "--building-category",
        default="Nonresidential",
        choices=["Nonresidential", "Residential", "Semiheated"],
        dest="building_category",
        help="ASHRAE 90.1 Table 5.5 building category (default Nonresidential)",
    )
    p.add_argument(
        "--telemetry",
        action="store_true",
        help="grant consent for this run's anonymous telemetry payload (#1003); "
        "off by default, and config telemetry_local_only wins over it. "
        "The payload lands in <out-dir>/telemetry.json; nothing is transmitted",
    )
    p.add_argument(
        "--baseline",
        default=None,
        metavar="DIR",
        help="previous run's out-dir; after this run succeeds, diff its "
        "stage_02_model against the baseline's, write <out-dir>/takeoff_diff.json, "
        "and attach stable-id review items to the model's review queue (#1077)",
    )
    p.add_argument("--out-dir", default="bem_out")

    p.add_argument("--open-office-span", action="store_true")
    p.add_argument(
        "--elevation-key",
        default="elev_grid",
        choices=["elev_grid", "elev_nogrid"],
    )
    p.add_argument("--simplify-tol", type=float, default=0.02)
    p.add_argument(
        "--mode",
        default="full",
        choices=["full", "progressive"],
        help="pipeline mode (#998): 'full' runs everything through BEM export (default); "
        "'progressive' stops after the takeoff (symbols, areas, counts) without BEM "
        "construction, review-queue gate or export",
    )
    p.add_argument(
        "--config",
        type=str,
        default=None,
        dest="config_path",
        help="YAML config file. Keys: review_confidence (float, default 0.0=no filter), "
        "simplify_tolerance (float, default 0.02=2%%), wall_height (metres for every "
        "level, or a map of level id to metres such as {L1: 4.5, L2: 3.6}; default from "
        "the drawings). "
        "Example: review_confidence: 0.85  simplify_tolerance: 0.01  wall_height: 3.5",
    )
    p.add_argument(
        "--legend-tags",
        default=None,
        dest="legend_tags",
        metavar="PATH",
        help="per-project legend sidecar (#995); default legend_tags.json beside "
        "the drawing set when present",
    )
    p.add_argument(
        "--confidence-threshold",
        type=check_range_0_1,
        default=None,
        dest="confidence_threshold",
        help=f"Minimum confidence for review queue (0.0-1.0). Overrides config file. "
        f"Default: {DEFAULT_CONFIDENCE_THRESHOLD}. "
        f"Can also be set via MATCHLINE_CONFIDENCE_THRESHOLD env var.",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser(
        "demo",
        help="sample project end to end: model, gbXML, review and trust report (#753)",
    )
    p.add_argument(
        "--seed", type=int, default=7, help="synthetic seed for the sample set (default: 7)"
    )
    p.add_argument(
        "--out",
        default="matchline-demo",
        help="output directory (default: matchline-demo/)",
    )
    p.add_argument(
        "--no-open",
        action="store_true",
        help="do not open the trust report in a browser",
    )
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("bem-export", help="gbXML + IFC4 export demo (synthetic)")
    p.add_argument("--sheet-id", default="sheet_007")
    p.set_defaults(func=cmd_bem_export)

    p = sub.add_parser("elevation-windows", help="exact window placement + daylight demo")
    p.set_defaults(func=cmd_elevation_windows)

    p = sub.add_parser(
        "facade-takeoff",
        help=(
            "facade area takeoffs on CMP Facade "
            "(see DATASETS.md for setup; use --data-root to specify)"
        ),
    )
    p.add_argument("--n", type=int, default=5, help="facades for the detailed demo")
    p.add_argument("--data-root", default=None, help="CMP Facade dataset root")
    p.add_argument(
        "--full",
        action="store_true",
        help="also run the full 606-facade sweep -> configs/facade_priors.json",
    )
    p.add_argument("--out-json", default=None, help="where the sweep writes priors")
    p.set_defaults(func=cmd_facade_takeoff)

    p = sub.add_parser("room-labels", help="OCR room labeling demo (synthetic)")
    p.set_defaults(func=cmd_room_labels)

    p = sub.add_parser("multidiscipline", help="cross-discipline linking demo (synthetic)")
    p.set_defaults(func=cmd_multidiscipline)

    p = sub.add_parser("mnist", help="WiSARD MNIST eval (needs data/mnist_X.npy + mnist_y.npy)")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--out-path", default="mnist_results.json")
    p.set_defaults(func=cmd_mnist)

    p = sub.add_parser("symbols", help="symbol eval + GD&T invariant check")
    p.add_argument("--out-path", default="symbols_results.json")
    p.set_defaults(func=cmd_symbols)

    p = sub.add_parser(
        "ifc-import",
        help="import an IFC file into the canonical model (Tier 0, no space boundaries)",
    )
    p.add_argument("path", help="input .ifc file")
    p.add_argument("--out", default=None, help="write canonical model JSON here")
    p.set_defaults(func=cmd_ifc_import)

    p = sub.add_parser(
        "ingest",
        help="read a PDF drawing set into per-page sheet records (raster + vectors + text)",
    )
    p.add_argument("path", help="input .pdf drawing set")
    p.add_argument("--out", required=True, help="output folder for sheet_NNN.{png,json}")
    p.add_argument("--dpi", type=float, default=150.0, help="raster resolution (default 150)")
    p.add_argument("--no-raster", action="store_true", help="skip rendering page images")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("scale", help="read each ingested sheet's drawing scale")
    p.add_argument("sheets", help="folder written by `matchline ingest`")
    p.set_defaults(func=cmd_scale)

    p = sub.add_parser(
        "export-package",
        help="verify an export package's manifest and build its share zip",
    )
    p.add_argument("out_dir", help="run output folder holding package/")
    p.set_defaults(func=cmd_export_package)

    p = sub.add_parser("index", help="index sheets by discipline, type and level")
    p.add_argument("sheets", help="folder written by `matchline ingest`")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("walls", help="walls and rooms from vector floor plans")
    p.add_argument("sheets", help="folder written by `matchline ingest`, with scale.json")
    p.set_defaults(func=cmd_walls)
    p = sub.add_parser(
        "schedules", help="door, window, lighting and mechanical schedules from ingested sheets"
    )
    p.add_argument("sheets", help="folder written by `matchline ingest`, with scale.json")
    p.set_defaults(func=cmd_schedules)

    p = sub.add_parser("ifc-export", help="Export BuildingModel JSON → IFC4 file")
    p.add_argument("model", help="BuildingModel JSON file path")
    p.add_argument("out", help="Output IFC4 file path")
    p.set_defaults(func=cmd_ifc_export)

    p = sub.add_parser(
        "feedback",
        help="Record open-ended feedback, or summarize the local feedback log (#1005)",
    )
    p.add_argument(
        "--text",
        help="Open-ended feedback: bugs, feature requests, process friction",
    )
    p.add_argument("--topic", help="Short generic topic label, e.g. review, detector, export")
    p.add_argument("--log", default="feedback.jsonl", help="Feedback JSONL path")
    p.add_argument("--summary", action="store_true", help="Print a summary of the feedback log")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("review", help="Review queue: list open items, confirm or reject decisions")
    p.add_argument("model", help="BuildingModel JSON file path")
    p.add_argument(
        "--confirm",
        metavar="ID",
        help="Confirm a review item (marks confirmed, re-runs validation)",
    )
    p.add_argument(
        "--reject", metavar="ID", help="Reject a review item (marks rejected, re-runs validation)"
    )
    p.add_argument("--show-all", action="store_true", help="Also show confirmed and rejected items")
    p.add_argument(
        "--enable-auto-triage",
        action="store_true",
        help="Explicitly enable auto-triage classifier when loading the model "
        "(enabled by default; flag is a no-op for backwards compatibility)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="List review items to stdout without prompting (exits 0 immediately)",
    )
    p.add_argument(
        "--bulk-confirm",
        action="store_true",
        help="Confirm every OPEN item matching the bulk filters (#996)",
    )
    p.add_argument(
        "--bulk-reject",
        action="store_true",
        help="Reject every OPEN item matching the bulk filters (#996)",
    )
    p.add_argument("--kind", metavar="KIND", help="Bulk filter: review-item kind")
    p.add_argument("--conf-above", metavar="MIN", type=float, help="Bulk filter: confidence >= MIN")
    p.add_argument("--conf-below", metavar="MAX", type=float, help="Bulk filter: confidence <= MAX")
    p.add_argument("--level", metavar="LEVEL", help="Bulk filter: level id (floor)")
    p.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for --list (default: text)",
    )
    p.add_argument(
        "--apply",
        metavar="DECISIONS",
        help="Replay a decisions.json exported from the HTML review report",
    )
    p.add_argument(
        "--out", metavar="PATH", help="With --apply: write the reviewed model here, not in place"
    )
    p.add_argument(
        "--report",
        metavar="DIR",
        help="Write the HTML review report to DIR/review (model, decisions template, page)",
    )
    p.add_argument(
        "--undo",
        metavar="N",
        type=int,
        help="Undo the last N accepted corrections (corrections.json, #997)",
    )
    p.add_argument(
        "--revert-to-auto",
        metavar="KEY",
        help="Put one field back to the automatic output; KEY is KIND:ID:FIELD (#997)",
    )
    p.add_argument(
        "--corrections",
        action="store_true",
        help="Print the correction history beside the model (#997)",
    )
    p.set_defaults(func=cmd_review)

    p = sub.add_parser(
        "viewer",
        help="read-only 3D review viewer page from a model JSON (#1001)",
    )
    p.add_argument("model", help="BuildingModel JSON file path")
    p.add_argument("--out", metavar="PATH", required=True, help="viewer.html path to write")
    p.set_defaults(func=cmd_viewer)

    p = sub.add_parser(
        "viewer-apply",
        help="apply a viewer corrections ops file through the decisions workflow (#1002)",
    )
    p.add_argument("model", help="BuildingModel JSON file path")
    p.add_argument("ops", metavar="OPS_JSON", help="viewer_corrections.json from the viewer page")
    p.add_argument(
        "--out",
        metavar="PATH",
        help="write the corrected model here instead of in place",
    )
    p.set_defaults(func=cmd_viewer_apply)

    p = sub.add_parser(
        "diff",
        help="diff two model JSON revisions at zone/space level",
    )
    p.add_argument(
        "--old",
        required=True,
        dest="old",
        help="Path to the baseline (old) revision model JSON",
    )
    p.add_argument(
        "--new",
        required=True,
        dest="new",
        help="Path to the new revision model JSON",
    )
    p.add_argument(
        "--out",
        default=None,
        dest="out",
        help="Write diff summary JSON here (default: stdout)",
    )
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser(
        "takeoff-diff",
        help=(
            "diff two revisions' takeoffs (rooms, windows, doors, symbols)"
            " with counts per change class (#993)"
        ),
    )
    p.add_argument(
        "--old",
        required=True,
        dest="old",
        help="Path to the baseline (old) revision: stage_02_model.json or a raw model JSON",
    )
    p.add_argument(
        "--new",
        required=True,
        dest="new",
        help="Path to the new revision: stage_02_model.json or a raw model JSON",
    )
    p.add_argument(
        "--old-report",
        default=None,
        dest="old_report",
        help="Optional old stage_00_set_report.json for the symbol takeoff",
    )
    p.add_argument(
        "--new-report",
        default=None,
        dest="new_report",
        help="Optional new stage_00_set_report.json for the symbol takeoff",
    )
    p.add_argument(
        "--out",
        default=None,
        dest="out",
        help="Write the diff report JSON here (default: stdout)",
    )
    p.add_argument(
        "--review-items",
        default=None,
        dest="review_items",
        help=(
            "Also write the diff as review-queue items here, ready for"
            " review_report.attach_takeoff_diff_items (#1048)"
        ),
    )
    p.set_defaults(func=cmd_takeoff_diff)

    p = sub.add_parser(
        "learn-legend",
        help="learn a project's legend tags from a set report (#995)",
    )
    p.add_argument("report", help="stage_00_set_report.json, or the out_dir holding it")
    p.add_argument(
        "--tag",
        action="append",
        default=[],
        metavar="DESC=CLASS",
        help="map a description the rules could not, e.g. --tag 'SUPPLY GRILLE=grille'; "
        "repeatable. Classes: vav, ahu, diffuser, grille, sensor",
    )
    p.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="sidecar to write (default: legend_tags.json beside the report)",
    )
    p.set_defaults(func=cmd_learn_legend)

    p = sub.add_parser(
        "legend-eval",
        help="before/after eval of a learned-tags run (#995)",
    )
    p.add_argument("before", help="baseline stage_00_set_report.json")
    p.add_argument("after", help="tagged-run stage_00_set_report.json")
    p.set_defaults(func=cmd_legend_eval)

    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
