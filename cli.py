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

DEFAULT_CONFIDENCE_THRESHOLD = 0.75


def cmd_run(args: argparse.Namespace) -> None:
    """Unified pipeline: generate + link + validate + BEM export."""
    import run_pipeline

    if (args.seed is None) == (args.aec_bench is None):
        print("Error: exactly one of --seed or --aec-bench is required", file=sys.stderr)
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
        known_keys = {"simplify_tolerance", "wall_height", "review_confidence"}
        unknown = set(config.keys()) - known_keys
        if unknown:
            print(f"Warning: unknown config keys ignored: {sorted(unknown)}", file=sys.stderr)

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
    try:
        run_pipeline.main(args, config=config)
    except run_pipeline.StageError:
        sys.exit(1)


def cmd_validate(args: argparse.Namespace) -> None:
    """Build 2 synthetic buildings, link them, run the validation battery."""
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

    from ifc_import import import_ifc

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
        outp.write_text(model.to_json())
        print(f"wrote {args.out}")


def cmd_ifc_export(args: argparse.Namespace) -> None:
    """Export a BuildingModel JSON file to an IFC4 file."""
    from ifc_export import export_ifc

    export_ifc(args.model, args.out)
    print(f"wrote {args.out}")


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
    p.add_argument("--out-dir", default="bem_out")
    p.add_argument("--open-office-span", action="store_true")
    p.add_argument(
        "--elevation-key",
        default="elev_grid",
        choices=["elev_grid", "elev_nogrid"],
    )
    p.add_argument("--simplify-tol", type=float, default=0.02)
    p.add_argument(
        "--config",
        type=str,
        default=None,
        dest="config_path",
        help="YAML config file. Keys: review_confidence (float, default 0.0=no filter), "
        "simplify_tolerance (float, default 0.02=2%%), wall_height (float, metres, "
        "default from level). "
        "Example: review_confidence: 0.85  simplify_tolerance: 0.01  wall_height: 3.5",
    )
    p.add_argument(
        "--confidence-threshold",
        type=float,
        default=None,
        dest="confidence_threshold",
        help=f"Minimum confidence for review queue (0.0-1.0). Overrides config file. "
        f"Default: {DEFAULT_CONFIDENCE_THRESHOLD}. "
        f"Can also be set via MATCHLINE_CONFIDENCE_THRESHOLD env var.",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("bem-export", help="gbXML + IFC4 export demo (synthetic)")
    p.add_argument("--sheet-id", default="sheet_007")
    p.set_defaults(func=cmd_bem_export)

    p = sub.add_parser("elevation-windows", help="exact window placement + daylight demo")
    p.set_defaults(func=cmd_elevation_windows)

    p = sub.add_parser(
        "facade-takeoff",
        help="facade area takeoffs on CMP Facade (see DATASETS.md for setup; use --data-root to specify)",
    )
    p.add_argument("--n", type=int, default=5, help="facades for the detailed demo")
    p.add_argument("--data-root", default=None, help="CMP Facade dataset root")
    p.add_argument(
        "--full",
        action="store_true",
        help="also run the full 606-facade sweep -> facade_priors.json",
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

    p = sub.add_parser("ifc-export", help="Export BuildingModel JSON → IFC4 file")
    p.add_argument("model", help="BuildingModel JSON file path")
    p.add_argument("out", help="Output IFC4 file path")
    p.set_defaults(func=cmd_ifc_export)

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
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for --list (default: text)",
    )
    p.set_defaults(func=cmd_review)

    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
