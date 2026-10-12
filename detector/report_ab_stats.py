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
import math
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


# --- Student's t, stdlib only -------------------------------------------------
# This script must run with no scipy (either detector venv, or a bare python3).
# The regularised incomplete beta via Lentz's continued fraction is the standard
# route to the t CDF; the tests pin it to the df=1 and df=2 closed forms.


def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps = 1e-300, 1e-15
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        step = d * c
        h *= step
        if abs(step - 1.0) < eps:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: int) -> float:
    """Two-sided p-value for Student's t with df degrees of freedom."""
    if math.isinf(t):
        return 0.0
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def t_critical(df: int, alpha: float = 0.05) -> float:
    """Two-sided critical value: the t with P(|T| > t) = alpha."""
    lo, hi = 0.0, 1e4
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def paired(control: dict[int, float], treatment: dict[int, float]) -> dict | None:
    """Seed-paired comparison, treatment - control, on seeds present in BOTH arms.

    Valid only when a seed means the same run in both arms (same split, same data
    order, same init outside the inserted blocks). A #516 floor of exactly zero,
    i.e. bit-deterministic training at fixed seed, is what licenses the pairing;
    main() prints that condition next to the result.

    Returns None when fewer than two seeds pair: one difference has no variance.
    """
    seeds = sorted(set(control) & set(treatment))
    if len(seeds) < 2:
        return None
    deltas = [treatment[s] - control[s] for s in seeds]
    n = len(deltas)
    mean = statistics.fmean(deltas)
    sd = statistics.stdev(deltas)
    df = n - 1
    se = sd / math.sqrt(n)
    if se == 0.0:
        t = math.inf if mean != 0.0 else 0.0
    else:
        t = mean / se
    p = t_two_sided_p(abs(t), df) if t != 0.0 else 1.0
    tc = t_critical(df)
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    return {
        'seeds': seeds, 'deltas': deltas, 'n': n, 'mean': mean, 'sd': sd,
        'df': df, 't': t, 'p': p, 'ci': (mean - tc * se, mean + tc * se),
        'consistent': pos == n or neg == n, 'pos': pos, 'neg': neg,
    }


def fmt(vals: list[float]) -> str:
    return ', '.join(f'{v:.5f}' for v in vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--runs', default=os.environ.get(
        'MATCHLINE_RUNS', os.path.expanduser('~/workspace/datasets/detector_runs')))
    ap.add_argument('--metric', default='mAP50', choices=sorted(METRIC_COLUMNS))
    ap.add_argument('--arms', nargs=2, default=['yolo11n_baseline', 'yolo11n_eca'],
                    metavar=('CONTROL', 'TREATMENT'))
    ap.add_argument('--reference-delta', type=float, default=None,
                    help="an effect size to test against the paired CI, e.g. a paper's "
                         'reported gain (FloorYOLO: 0.0125 mAP50 on CVC-FP)')
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
        if noise_range == 0.0:
            print('  NOTE: the run-to-run floor is exactly zero, so "clears it" carries')
            print('  no information on its own. It does mean runs are deterministic at')
            print('  fixed seed, which licenses the paired test below, and that test is')
            print('  the more informative one.')
        print(f'  UNDERPOWERED. |delta| {abs(delta):.5f} clears run-to-run noise')
        print(f'  ({noise_range:.5f}) but sits inside the within-arm seed spread')
        print(f'  ({within:.5f}). More seeds are needed before a claim holds.')
    else:
        direction = 'improves' if delta > 0 else 'degrades'
        print(f'  DELTA CLEARS BOTH FLOORS: {treatment} {direction} {metric} by')
        print(f'  {abs(delta):.5f}, vs within-arm {within:.5f} and run-to-run')
        print(f'  {noise_range:.5f}. Still report all three numbers together.')
    print()

    # --- seed-paired comparison ---------------------------------------------
    # The unpaired verdict above treats the arms as independent samples, which
    # throws away that seed s means the same split/order/init in both. With a
    # deterministic floor the pairing is real and it is the more powerful test:
    # on the #500 matrix it finds a consistent mAP50-95 gain (paired p=0.008)
    # that the unpaired comparison cannot see (Welch p=0.38).
    pr = paired(summary.get(control, {}), summary.get(treatment, {}))
    print('seed-paired (treatment - control, matched seeds)')
    if pr is None:
        print('  SKIPPED: fewer than two seeds present in both arms.')
    else:
        if noise_range is None:
            print('  CAUTION: pairing is valid only if a seed means the same run in both')
            print('  arms. The #516 floor is unmeasured, so determinism at fixed seed is')
            print('  unverified. Read this as provisional.')
        elif noise_range == 0.0:
            print('  pairing licensed: the #516 floor is exactly zero (deterministic).')
        else:
            print(f'  pairing caution: the #516 floor is {noise_range:.5f}, nonzero, so')
            print('  matched seeds are not identical runs. Pairing still removes shared')
            print('  seed effects, but less cleanly.')
        print(f'  seeds:  {pr["seeds"]}')
        print(f'  deltas: {", ".join(f"{d:+.5f}" for d in pr["deltas"])}')
        lo, hi = pr['ci']
        print(f'  mean={pr["mean"]:+.5f}  sd={pr["sd"]:.5f}  t={pr["t"]:.2f}'
              f' (df {pr["df"]})  p={pr["p"]:.3f}')
        print(f'  95% CI [{lo:+.5f}, {hi:+.5f}]')
        if pr['consistent']:
            sign = 'positive' if pr['pos'] else 'negative'
            print(f'  direction: consistent, all {pr["n"]} seeds {sign}')
        else:
            print(f'  direction: NOT consistent ({pr["pos"]} up, {pr["neg"]} down)')
        if lo > 0 or hi < 0:
            direction = 'improves' if pr['mean'] > 0 else 'degrades'
            print(f'  paired verdict: {treatment} {direction} {metric}; the CI excludes 0.')
            print(f'  Small df ({pr["df"]}): report the CI, not just the p-value.')
        else:
            print('  paired verdict: the CI includes 0; no paired effect measurable.')
        if args.reference_delta is not None:
            ref = args.reference_delta
            if lo <= ref <= hi:
                print(f'  reference {ref:+.5f} lies INSIDE the CI: not ruled out.')
            else:
                print(f'  reference {ref:+.5f} lies OUTSIDE the CI: ruled out at 95% on')
                print('  this data (not merely undetected).')
    print()
    print('NOTE: detector/ is excluded from CI (pyproject.toml). Nothing here is')
    print('verified by a green upstream run.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
