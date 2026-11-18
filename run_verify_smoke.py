#!/usr/bin/env python3
"""E2E verify smoke: golden synthetic sheet -> gated BEM export (#1411).

The per-change end-to-end smoke named in CONTRACT.md's Verify set.
It chains two existing entry points and changes neither's semantics:

  1. generate-if-missing: if ``synth/out/sheets/<sheet>.gt.json`` is
     absent, generate exactly that sheet's slice with
     ``python -m synth.make_dataset --sheets 1 --seed <n>`` (the seed
     is the sheet's numeric suffix, which is how make_dataset names
     its sheets). If the ground truth is already on disk, generation
     is skipped entirely.
  2. export: ``run_bem_export.main`` — the gated transaction from
     #1404/#1405. gbXML (XSD) and IFC4 (structural) validation run
     inside that gate; a failed validation publishes nothing and
     makes this wrapper exit non-zero. The gate remains the final
     arbiter, unchanged.

Exit 0 iff the export published with both validations passing.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_SHEET_ID = "sheet_007"
DATA_OUT = REPO_ROOT / "synth" / "out"


def sheet_seed(sheet_id: str) -> int:
    """The make_dataset seed that generates ``sheet_id``.

    make_dataset writes ``sheet_<seed + i>`` for i in range(sheets),
    so a sheet's numeric suffix is a seed that regenerates it.
    """
    m = re.search(r"(\d+)$", sheet_id)
    if m is None:
        raise ValueError(f"sheet id {sheet_id!r} has no numeric suffix to seed from")
    return int(m.group(1))


def gt_path(data_out: Path, sheet_id: str) -> Path:
    return Path(data_out) / "sheets" / f"{sheet_id}.gt.json"


def generate_dataset(data_out: Path, sheet_id: str, runner=subprocess.run) -> bool:
    """Generate the one-sheet slice for ``sheet_id`` into ``data_out``.

    Returns True iff the ground-truth file exists afterwards.
    """
    argv = [
        sys.executable,
        "-m",
        "synth.make_dataset",
        "--sheets",
        "1",
        "--seed",
        str(sheet_seed(sheet_id)),
        "--out",
        str(data_out),
    ]
    result = runner(argv, cwd=REPO_ROOT)
    return result.returncode == 0 and gt_path(data_out, sheet_id).exists()


def verify_smoke(
    sheet_id: str = DEFAULT_SHEET_ID,
    data_out: Path = DATA_OUT,
    out_dir: Path | None = None,
    generate=generate_dataset,
    export=None,
) -> bool:
    """Run the smoke. Returns True iff the export published cleanly."""
    gt = gt_path(data_out, sheet_id)
    if gt.exists():
        print(f"verify-smoke: dataset present ({gt}), skipping generation", flush=True)
    else:
        print(f"verify-smoke: dataset missing, generating {sheet_id} -> {data_out}", flush=True)
        try:
            generated = generate(Path(data_out), sheet_id)
        except ValueError as exc:
            print(f"verify-smoke: cannot generate: {exc}", flush=True)
            return False
        if not generated:
            print(f"verify-smoke: generation failed to produce {gt}", flush=True)
            return False
    if export is None:
        import run_bem_export

        ok = run_bem_export.main(sheet_id, gt_path=gt, out_dir=out_dir)
    else:
        ok = export(sheet_id, gt_path=gt, out_dir=out_dir)
    print(f"verify-smoke: {'PASS' if ok else 'FAIL'}", flush=True)
    return bool(ok)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="E2E verify smoke: generate-if-missing + gated bem-export"
    )
    ap.add_argument("--sheet-id", default=DEFAULT_SHEET_ID)
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        dest="out_dir",
        help="output directory for the exports (default: bem_out/)",
    )
    args = ap.parse_args(argv)
    return 0 if verify_smoke(args.sheet_id, out_dir=args.out_dir) else 1


if __name__ == "__main__":
    raise SystemExit(main())
