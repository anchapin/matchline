#!/usr/bin/env python3
"""Report the #500 between-arm delta against the within-arm and run-to-run spread.

Issue #500 asks whether ECA improves mAP50. That question is unanswerable from a
delta alone: a between-arm difference is only meaningful next to the spread of
runs that differ by nothing but chance. Issue #516 measures that floor
(repro_seed0_r*, one config, one seed, fixed epochs). This script puts the three
numbers side by side and refuses to call a verdict the data cannot support.

Three spreads, in increasing strictness:

  run-to-run  repro_seed0_r*      same config, same seed, same epochs -> pure noise
  seed-to-seed  per arm across seeds   noise + seed variance
  between-arm   baseline vs eca means  what #500 is actually asking about

A delta smaller than the run-to-run spread is not a result. A delta inside the
seed-to-seed spread is underpowered at this repeat count, which is a legitimate
and publishable outcome; it is not evidence of "no effect".

Reads the same ab_summary.csv that run_ab_eca.sh appends to, and reads
results.csv directly for the #516 repeats (which are not matrix rows and so
never land in the summary).

Usage:
    python3 report_ab_stats.py                      # default runs dir
    python3 report_ab_stats.py --runs /path/to/detector_runs
    python3 report_ab_stats.py --metric mAP50_95
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
from pathlib import Path

METRIC_COLUMNS = {
    'mAP50': 'metrics/mAP50(B)',
    'mAP50_95': 'metrics/mAP50-95(B)',
}


def read_summary(path: Path, metric: str) -> dict[str, dict[int, float]]:
    """arm -> {seed: metric} from ab_summary.csv. Later rows win (idempotent re-runs)."""
    if not path.exists():
        return {}
    out: dict[str, dict[int, float]] = {}
    with open(path, newline='') as f:
        for row in csv.DictReader(f):
            if metric not in row or not row[metric]:
                continue
            out.setdefault(row['arm'], {})[int(row['seed'])] = float(row[metric])
    return out


def read_repeats(runs: Path, metric: str, prefix: str = 'repro_seed0_r') -> dict[str, float]:
    """run name -> final-epoch metric, for the #516 fixed-seed repeats."""
    col = METRIC_COLUMNS[metric]
    out: dict[str, float] = {}
    for d in sorted(runs.glob(f'{prefix}*')):
        csv_path = d / 'results.csv'
        if not csv_path.is_file():
            continue
        with open(csv_path, newline='') as f:
            rows = [r for r in csv.DictReader(f) if r.get(col)]
        if rows:
            out[d.name] = float(rows[-1][col])
    return out


def spread(values: list[float]) -> tuple[float, float, float]:
    """(range, mean, stdev). Range is the honest headline for n=3; stdev for n>=3."""
    rng = max(values) - min(values) if values else 0.0
    mean = statistics.fmean(values) if values else 0.0
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return rng, mean, sd


def fmt(vals: list[float]) -> str:
    return ', '.join(f'{v:.5f}' for v in vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=os.environ.get(
        'MATCHLINE_RUNS', os.path.expanduser('~/workspace/datasets/detector_runs')))
    ap.add_argument('--metric', default='mAP50', choices=sorted(METRIC_COLUMNS))
    ap.add_argument('--arms', nargs=2, default=['yolo11n_baseline', 'yolo11n_eca'],
                    metavar=('CONTROL', 'TREATMENT'))
    args = ap.parse_args()

    runs = Path(args.runs)
    if not runs.is_dir():
        print(f'FATAL: runs dir not found: {runs}', file=sys.stderr)
        print('Set --runs or MATCHLINE_RUNS.', file=sys.stderr)
        return 1

    metric = args.metric
    summary = read_summary(runs / 'ab_summary.csv', metric)
    repeats = read_repeats(runs, metric)
    control, treatment = args.arms

    print(f'metric: {metric}   runs: {runs}')
    print()

    # --- the noise floor (#516) ---------------------------------------------
    noise_range = None
    print('run-to-run at fixed seed (#516)')
    if len(repeats) < 2:
        print('  NOT MEASURED. Found '
              f'{len(repeats)} repeat run(s); need >= 2.')
        print('  Run: bash detector/run_repro_noise.sh 10 3')
        print('  Without this, no delta below the seed-to-seed spread can be')
        print('  called a result -- and the seed spread is an upper bound on')
        print('  the floor, not the floor itself.')
    else:
        vals = list(repeats.values())
        noise_range, mean, sd = spread(vals)
        print(f'  n={len(vals)}  values: {fmt(vals)}')
        print(f'  mean={mean:.5f}  range={noise_range:.5f}  stdev={sd:.5f}')
        print(f'  runs: {", ".join(repeats)}')
        print('  CAVEAT: this floor holds at the epoch count it was measured at.')
    print()

    # --- per-arm seed spread -------------------------------------------------
    arm_means: dict[str, float] = {}
    arm_ranges: dict[str, float] = {}
    print('seed-to-seed within each arm (#500 matrix)')
    for arm in (control, treatment):
        seeds = summary.get(arm, {})
        if not seeds:
            print(f'  {arm}: no rows in ab_summary.csv')
            continue
        vals = [seeds[s] for s in sorted(seeds)]
        rng, mean, sd = spread(vals)
        arm_means[arm], arm_ranges[arm] = mean, rng
        print(f'  {arm}: n={len(vals)} seeds={sorted(seeds)}')
        print(f'    values: {fmt(vals)}')
        print(f'    mean={mean:.5f}  range={rng:.5f}  stdev={sd:.5f}')
    print()

    # --- the comparison ------------------------------------------------------
    print('between-arm')
    if len(arm_means) < 2:
        print('  INCOMPLETE. Both arms need rows before a delta exists.')
        print(f'  Have: {sorted(arm_means) or "none"}')
        return 0

    delta = arm_means[treatment] - arm_means[control]
    within = max(arm_ranges.values())
    print(f'  delta ({treatment} - {control}) = {delta:+.5f}')
    print(f'  widest within-arm range      = {within:.5f}')
    if noise_range is not None:
        print(f'  run-to-run range (#516)      = {noise_range:.5f}')
    print()

    print('verdict')
    if noise_range is None:
        print('  WITHHELD: the noise floor is unmeasured (#516). The within-arm')
        print('  spread is an upper bound that includes seed variance, so it')
        print('  cannot separate "no effect" from "underpowered".')
        if abs(delta) <= within:
            print(f'  For what it is worth, the delta ({abs(delta):.5f}) does not')
            print(f'  clear even that bound ({within:.5f}).')
    elif abs(delta) <= noise_range:
        print(f'  NO EFFECT MEASURABLE. |delta| {abs(delta):.5f} <= run-to-run')
        print(f'  noise {noise_range:.5f}. The arms are indistinguishable at this')
        print('  budget. Report as "no delta measurable", not "ECA does not work".')
    elif abs(delta) <= within:
        print(f'  UNDERPOWERED. |delta| {abs(delta):.5f} clears run-to-run noise')
        print(f'  ({noise_range:.5f}) but sits inside the within-arm seed spread')
        print(f'  ({within:.5f}). More seeds are needed before a claim holds.')
    else:
        direction = 'improves' if delta > 0 else 'degrades'
        print(f'  DELTA CLEARS BOTH FLOORS: {treatment} {direction} {metric} by')
        print(f'  {abs(delta):.5f}, vs within-arm {within:.5f} and run-to-run')
        print(f'  {noise_range:.5f}. Still report all three numbers together.')
    print()
    print('NOTE: detector/ is excluded from CI (pyproject.toml). Nothing here is')
    print('verified by a green upstream run.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
