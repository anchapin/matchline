#!/usr/bin/env python3
"""Weekly cleanup sweep: fail when too many new tracked files or folders appear.

Counts tracked files (``git ls-files``) and the directories that contain them,
compares against a committed baseline (``.github/cleanup-sweep-baseline.json``),
and exits 1 when net growth exceeds the thresholds. Intended to run as a
scheduled GitHub Actions workflow; a ``--update`` pass re-baselines after
intentional growth (renames keep the count flat, so they never trip the check,
though new names still show in the report).

Usage:
    python3 scripts/cleanup_sweep.py [--baseline PATH]
                                     [--max-new-files N] [--max-new-dirs N]
                                     [--update]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from dataclasses import dataclass

DEFAULT_BASELINE = ".github/cleanup-sweep-baseline.json"
DEFAULT_MAX_NEW_FILES = 10
DEFAULT_MAX_NEW_DIRS = 3
_REPORT_CAP = 20


@dataclass
class SweepResult:
    ok: bool
    files: int
    dirs: int
    baseline_files: int
    baseline_dirs: int
    new_file_names: list[str]
    new_dir_names: list[str]
    removed_file_names: list[str]
    removed_dir_names: list[str]

    @property
    def new_files(self) -> int:
        return self.files - self.baseline_files

    @property
    def new_dirs(self) -> int:
        return self.dirs - self.baseline_dirs


def tracked_paths() -> list[str]:
    """Tracked file paths in this git repository."""
    out = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout.decode(
        "utf-8"
    )
    return [p for p in out.split("\0") if p]


def count_paths(paths: list[str]) -> tuple[int, set[str]]:
    """Return (file count, set of directories holding at least one tracked file)."""
    dirs = {os.path.dirname(p) for p in paths}
    dirs.discard("")
    return len(paths), dirs


def evaluate(
    baseline: dict,
    paths: list[str],
    max_new_files: int = DEFAULT_MAX_NEW_FILES,
    max_new_dirs: int = DEFAULT_MAX_NEW_DIRS,
) -> SweepResult:
    files, dirs = count_paths(paths)
    base_files = set(baseline["file_names"])
    base_dirs = set(baseline["dir_names"])
    cur_files = set(paths)
    return SweepResult(
        ok=(files - baseline["files"] <= max_new_files)
        and (len(dirs) - baseline["dirs"] <= max_new_dirs),
        files=files,
        dirs=len(dirs),
        baseline_files=baseline["files"],
        baseline_dirs=baseline["dirs"],
        new_file_names=sorted(cur_files - base_files),
        new_dir_names=sorted(dirs - base_dirs),
        removed_file_names=sorted(base_files - cur_files),
        removed_dir_names=sorted(base_dirs - dirs),
    )


def _names(title: str, names: list[str]) -> list[str]:
    if not names:
        return []
    lines = [f"{title} ({len(names)}):"]
    lines += [f"  - {n}" for n in names[:_REPORT_CAP]]
    if len(names) > _REPORT_CAP:
        lines.append(f"  - ... and {len(names) - _REPORT_CAP} more")
    return lines


def report(result: SweepResult, max_new_files: int, max_new_dirs: int) -> str:
    verdict = "PASS" if result.ok else "FAIL"
    lines = [
        f"cleanup sweep: {verdict}",
        f"tracked files: {result.files} (baseline {result.baseline_files}, "
        f"+{result.new_files}, limit +{max_new_files})",
        f"tracked directories: {result.dirs} (baseline {result.baseline_dirs}, "
        f"+{result.new_dirs}, limit +{max_new_dirs})",
    ]
    lines += _names("new files", result.new_file_names)
    lines += _names("new directories", result.new_dir_names)
    lines += _names("removed files", result.removed_file_names)
    lines += _names("removed directories", result.removed_dir_names)
    return "\n".join(lines)


def load_baseline(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("files", "dirs", "file_names", "dir_names"):
        if key not in data:
            raise SystemExit(
                f"baseline {path} is malformed: missing '{key}'. "
                "Re-run with --update to rebuild it."
            )
    return data


def write_baseline(path: str, paths: list[str]) -> dict:
    files, dirs = count_paths(paths)
    head = (
        subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True)
        .stdout.decode("utf-8")
        .strip()
    )
    data = {
        "updated": datetime.date.today().isoformat(),
        "commit": head,
        "files": files,
        "dirs": len(dirs),
        "file_names": sorted(paths),
        "dir_names": sorted(dirs),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--baseline", default=DEFAULT_BASELINE)
    ap.add_argument("--max-new-files", type=int, default=DEFAULT_MAX_NEW_FILES)
    ap.add_argument("--max-new-dirs", type=int, default=DEFAULT_MAX_NEW_DIRS)
    ap.add_argument(
        "--update",
        action="store_true",
        help="re-baseline current counts instead of checking (after intentional growth)",
    )
    args = ap.parse_args(argv)

    paths = tracked_paths()
    if args.update:
        data = write_baseline(args.baseline, paths)
        print(f"baseline written: {args.baseline}")
        print(f"  files: {data['files']}  dirs: {data['dirs']}  as of {data['updated']}")
        return 0

    try:
        baseline = load_baseline(args.baseline)
    except FileNotFoundError:
        print(f"ERROR: baseline {args.baseline} not found. Run with --update to create it.")
        return 2

    result = evaluate(baseline, paths, args.max_new_files, args.max_new_dirs)
    text = report(result, args.max_new_files, args.max_new_dirs)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("```\n" + text + "\n```\n")
    if not result.ok:
        print(
            "\nToo much growth since the last baseline. If it is intentional, "
            "re-run this workflow with update_baseline checked."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
