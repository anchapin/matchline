#!/usr/bin/env python3
"""Split the pytest suite into shards and prove the shards cover it (#1423).

The CI `quality` job runs the suite in two shards (see
.github/workflows/ci.yml). The split is balanced by *measured* time, not by
file count: every test file is weighed by the sum of the durations recorded
for its tests in the committed durations file, and the heaviest files go to
the shard that is currently the least loaded (longest-processing-time first).

Two subcommands:

* `split` -- compute the shard assignment and write one file list per shard.
* `union` -- the gate: collect the full suite and fail unless the union of
  the shard file lists is exactly that set.

The durations file is the same shape pytest-split uses (a JSON mapping of
test node id to seconds), so the recorded timings can be refreshed with
`store-durations` and stay readable by pytest-split if the project ever
switches to that plugin.

Usage:

    python scripts/check_test_shards.py split --shards 2 --out-dir shard-lists
    python scripts/check_test_shards.py union --shard-lists a.txt b.txt
    python scripts/check_test_shards.py store-durations
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTS = "tests/"
DEFAULT_DURATIONS = ".github/test-durations.json"

# "12.34s call     tests/test_x.py::test_y" (pytest --durations output).
_DURATION_RE = re.compile(r"^([\d.]+)s\s+(\w+)\s+(\S+)\s*$")
# "4370 tests collected in 91.20s" (pytest --collect-only -q summary line).
_COLLECTED_RE = re.compile(r"(\d+) tests? collected")


def _run_pytest(args: list[str]) -> str:
    """Run pytest with the current interpreter and return its stdout."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"pytest {' '.join(args)} failed with exit code {proc.returncode}")
    return proc.stdout


def collect_node_ids(tests: str) -> list[str]:
    """Every test node id the suite collects, in collection order."""
    out = _run_pytest([tests, "--collect-only", "-q"])
    node_ids = [line.strip() for line in out.splitlines() if "::" in line]
    if not node_ids:
        raise SystemExit(f"pytest collected no tests from {tests!r}")
    return node_ids


def load_durations(path: Path) -> dict[str, float]:
    """The recorded per-test durations (node id -> seconds)."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    durations: dict[str, float] = {}
    for node_id, seconds in raw.items():
        if isinstance(seconds, dict):  # phase breakdown: sum the phases
            seconds = sum(float(value) for value in seconds.values())
        durations[node_id] = float(seconds)
    return durations


def file_durations(node_ids: list[str], durations: dict[str, float]) -> dict[str, float]:
    """The recorded time per test file (sum over the file's tests).

    The sum runs in sorted node-id order and each file total is rounded:
    collection order is shuffled (pytest-randomly), float addition is
    order-sensitive, and every CI shard job computes this split in its own
    process — the weights must come out bit-identical in all of them or the
    shards can disagree about the assignment.
    """
    totals: dict[str, float] = defaultdict(float)
    for node_id in sorted(node_ids):
        totals[node_id.split("::", 1)[0]] += durations.get(node_id, 0.0)
    return {path: round(total, 6) for path, total in totals.items()}


def assign_shards(weights: dict[str, float], shards: int) -> list[list[str]]:
    """Distribute the test files over the shards, heaviest file first.

    The heaviest file goes to the least loaded shard, and so on. Files with
    no recorded duration (a newly added test file) weigh nothing and are
    spread by the same rule, so a new file never disappears from the run.
    """
    if shards < 1:
        raise SystemExit("--shards must be at least 1")
    loads = [0.0] * shards
    counts = [0] * shards
    assignment: list[list[str]] = [[] for _ in range(shards)]
    ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    for path, weight in ordered:
        target = min(range(shards), key=lambda index: (loads[index], counts[index], index))
        assignment[target].append(path)
        loads[target] += weight
        counts[target] += 1
    return assignment


def _read_list(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def cmd_split(args: argparse.Namespace) -> int:
    node_ids = collect_node_ids(args.tests)
    durations = load_durations(Path(args.durations_path))
    weights = file_durations(node_ids, durations)
    assignment = assign_shards(weights, args.shards)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for index, files in enumerate(assignment, start=1):
        target = out_dir / f"shard-{index}.txt"
        target.write_text("".join(f"{path}\n" for path in sorted(files)), encoding="utf-8")
        total = sum(weights.get(path, 0.0) for path in files)
        print(f"shard {index}: {len(files)} files, {total:.1f}s recorded -> {target}")

    known = sum(1 for node_id in node_ids if node_id in durations)
    print(
        f"collected {len(node_ids)} tests from {args.tests} "
        f"({known} with a recorded duration, {len(node_ids) - known} without)"
    )
    slowest = sorted(weights.items(), key=lambda item: -item[1])[:5]
    print("slowest files: " + ", ".join(f"{path} {value:.1f}s" for path, value in slowest))
    return 0


def cmd_union(args: argparse.Namespace) -> int:
    node_ids = collect_node_ids(args.tests)
    full = {node_id.split("::", 1)[0] for node_id in node_ids}

    shard_files: dict[str, set[str]] = {}
    for list_path in args.shard_lists:
        path = Path(list_path)
        if not path.exists():
            print(f"ERROR: shard file list {list_path} does not exist")
            return 1
        shard_files[list_path] = set(_read_list(path))

    if args.drop_file:
        dropped = args.drop_file
        for list_path, files in shard_files.items():
            if dropped in files:
                files.discard(dropped)
                print(f"dropped {dropped} from {list_path} (--drop-file)")

    union: set[str] = set()
    for files in shard_files.values():
        union |= files
    overlap = set()
    seen: set[str] = set()
    for files in shard_files.values():
        overlap |= seen & files
        seen |= files

    missing = sorted(full - union)
    extra = sorted(union - full)

    print(f"full suite: {len(full)} test files, {len(node_ids)} tests")
    for list_path, files in shard_files.items():
        print(f"{list_path}: {len(files)} files")
    print(f"union: {len(union)} files")

    ok = True
    if missing:
        ok = False
        print(f"ERROR: {len(missing)} test file(s) are in the suite but in no shard:")
        for path in missing:
            print(f"  - {path}")
    if extra:
        ok = False
        print(f"ERROR: {len(extra)} shard file(s) are not in the suite:")
        for path in extra:
            print(f"  - {path}")
    if overlap:
        ok = False
        print(f"ERROR: {len(overlap)} test file(s) are in more than one shard:")
        for path in sorted(overlap):
            print(f"  - {path}")
    if ok:
        print("OK: the shards' union is the full suite")
    return 0 if ok else 1


def cmd_store_durations(args: argparse.Namespace) -> int:
    """Record the per-test durations of a full local run into the JSON file."""
    # A measurement run must still record its durations when the suite has a
    # failure: the timings are the deliverable, and one red test (e.g. a
    # pre-existing failure on the base branch) must not discard them. So this
    # runs pytest directly instead of via _run_pytest, which aborts on a
    # non-zero exit; it fails only when no durations were produced at all.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", args.tests, "--durations=0", "-q", *args.pytest_args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    out = proc.stdout
    if proc.returncode != 0:
        print(
            f"note: pytest exited {proc.returncode}; recording the durations it produced anyway",
            file=sys.stderr,
        )
    durations: dict[str, float] = defaultdict(float)
    for line in out.splitlines():
        match = _DURATION_RE.match(line.strip())
        if match:
            seconds, _phase, node_id = match.groups()
            durations[node_id] += float(seconds)
    if not durations:
        raise SystemExit("no durations found in the pytest output")
    target = Path(args.durations_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {node_id: round(value, 4) for node_id, value in sorted(durations.items())}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    collected = _COLLECTED_RE.findall(out)
    total = f"{collected[-1]} tests collected" if collected else "unknown test count"
    print(f"recorded {len(durations)} test durations ({total}) -> {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tests", default=DEFAULT_TESTS, help="the suite to collect")
    parser.add_argument(
        "--durations-path", default=DEFAULT_DURATIONS, help="the recorded durations file"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    split = sub.add_parser("split", help="write the shard file lists")
    split.add_argument("--shards", type=int, default=2)
    split.add_argument("--out-dir", default="shard-lists")
    split.set_defaults(func=cmd_split)

    union = sub.add_parser("union", help="fail unless the shards cover the full suite")
    union.add_argument("--shard-lists", nargs="+", required=True)
    union.add_argument(
        "--drop-file",
        default=None,
        help="drop this file from the shard lists before checking (drill)",
    )
    union.set_defaults(func=cmd_union)

    store = sub.add_parser("store-durations", help="record the durations of a full local run")
    store.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        default=[],
        help="extra arguments for pytest (e.g. -n 4)",
    )
    store.set_defaults(func=cmd_store_durations)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
