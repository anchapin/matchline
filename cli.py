"""wisard-bem: unified command-line interface.

Every demo/evaluation script in this repo is reachable as a subcommand::

    wisard-bem validate
    wisard-bem bem-export --sheet-id sheet_007
    wisard-bem facade-takeoff --n 5 --full
    wisard-bem ifc-import building.ifc --out model.json

The historical ``python3 run_*.py`` scripts still work; they are thin
wrappers around the same functions.
"""

from __future__ import annotations

import argparse
import sys


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
    """Exact window placement from elevations + daylight zones (synthetic)."""
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
        Path(args.out).write_text(model.to_json())
        print(f"wrote {args.out}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wisard-bem",
        description="Deterministic, auditable drawing/BIM -> BEM quantity takeoffs.",
    )
    sub = ap.add_subparsers(dest="command", required=True, metavar="<command>")

    p = sub.add_parser("validate", help="validation battery demo (synthetic)")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("bem-export", help="gbXML + IFC4 export demo (synthetic)")
    p.add_argument("--sheet-id", default="sheet_007")
    p.set_defaults(func=cmd_bem_export)

    p = sub.add_parser("elevation-windows", help="exact window placement + daylight demo")
    p.set_defaults(func=cmd_elevation_windows)

    p = sub.add_parser(
        "facade-takeoff",
        help="facade area takeoffs on CMP Facade (needs ~/workspace/datasets/cmp-facade)",
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

    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
