"""Tests for detector/report_ab_stats.py (issues #500, #516).

Pure stdlib, so unlike test_eca.py these need no torch and run under either
detector venv:

    detector/.venv-det/bin/python -m pytest detector/tests/test_report_ab_stats.py -q

They live outside the main ``tests/`` suite to keep detector/ in one place
(pyproject testpaths = ["tests"]), so they do not move CI's test-count gate.

Happy path: the three spreads are computed from real on-disk layouts.
Invariants: a missing noise floor WITHHOLDS the verdict rather than guessing;
a delta inside a floor is reported as unmeasurable/underpowered, never as
"no effect".
Defect injection: absent runs dir, empty summary, one-arm summary, blank metric
cells, and a duplicate summary row (the #514 idempotency failure mode).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

DETECTOR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DETECTOR))

import report_ab_stats as R  # noqa: E402

SUMMARY_HEADER = "arm,seed,epochs,mAP50,mAP50_95,train_box_loss\n"


def write_summary(runs: Path, rows: list[tuple[str, int, float]]) -> None:
    lines = [SUMMARY_HEADER]
    for arm, seed, m in rows:
        lines.append(f"{arm},{seed},50,{m},{m * 0.6:.5f},1.0\n")
    (runs / "ab_summary.csv").write_text("".join(lines))


def write_repeat(runs: Path, name: str, final_map50: float, epochs: int = 10) -> None:
    d = runs / name
    d.mkdir(parents=True, exist_ok=True)
    head = "epoch,metrics/mAP50(B),metrics/mAP50-95(B),train/box_loss\n"
    body = "".join(
        f"{e},{final_map50 - 0.01 * (epochs - e)},{final_map50 * 0.6},1.0\n"
        for e in range(1, epochs + 1)
    )
    (d / "results.csv").write_text(head + body)


def run_cli(runs: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(DETECTOR / "report_ab_stats.py"), "--runs", str(runs), *extra],
        capture_output=True,
        text=True,
    )


# --- happy path -------------------------------------------------------------


def test_reads_summary_last_row_wins(tmp_path: Path) -> None:
    # A duplicate row for the same (arm, seed) is exactly what #514's
    # non-idempotent append produced. The later row must win, not both count.
    write_summary(tmp_path, [("yolo11n_eca", 0, 0.70), ("yolo11n_eca", 0, 0.75)])
    got = R.read_summary(tmp_path / "ab_summary.csv", "mAP50")
    assert got == {"yolo11n_eca": {0: 0.75}}


def test_reads_repeats_final_epoch(tmp_path: Path) -> None:
    write_repeat(tmp_path, "repro_seed0_r0", 0.800)
    write_repeat(tmp_path, "repro_seed0_r1", 0.802)
    got = R.read_repeats(tmp_path, "mAP50")
    assert sorted(got) == ["repro_seed0_r0", "repro_seed0_r1"]
    assert got["repro_seed0_r0"] == pytest.approx(0.800)


def test_spread_range_mean_stdev() -> None:
    rng, mean, sd = R.spread([0.10, 0.20, 0.30])
    assert rng == pytest.approx(0.20)
    assert mean == pytest.approx(0.20)
    assert sd == pytest.approx(0.1)


def test_spread_single_value_has_zero_stdev() -> None:
    assert R.spread([0.5]) == (0.0, 0.5, 0.0)


# --- invariants -------------------------------------------------------------


def test_verdict_withheld_without_noise_floor(tmp_path: Path) -> None:
    write_summary(
        tmp_path,
        [(a, s, m) for a, seeds in {
            "yolo11n_baseline": {0: 0.80, 1: 0.81, 2: 0.82},
            "yolo11n_eca": {0: 0.90, 1: 0.91, 2: 0.92},
        }.items() for s, m in seeds.items()],
    )
    out = run_cli(tmp_path).stdout
    assert "NOT MEASURED" in out
    # A +0.10 delta is large, but the floor is unknown, so no verdict is given.
    assert "WITHHELD" in out
    assert "DELTA CLEARS BOTH FLOORS" not in out


def test_delta_inside_noise_is_not_called_no_effect(tmp_path: Path) -> None:
    write_summary(
        tmp_path,
        [("yolo11n_baseline", s, v) for s, v in {0: 0.80, 1: 0.80, 2: 0.80}.items()]
        + [("yolo11n_eca", s, v) for s, v in {0: 0.801, 1: 0.801, 2: 0.801}.items()],
    )
    for i, v in enumerate([0.79, 0.81, 0.80]):
        write_repeat(tmp_path, f"repro_seed0_r{i}", v)
    out = run_cli(tmp_path).stdout
    assert "NO EFFECT MEASURABLE" in out
    assert "not" in out and "does not work" in out


def test_delta_clearing_noise_but_not_seed_spread_is_underpowered(tmp_path: Path) -> None:
    write_summary(
        tmp_path,
        [("yolo11n_baseline", s, v) for s, v in {0: 0.70, 1: 0.80, 2: 0.90}.items()]
        + [("yolo11n_eca", s, v) for s, v in {0: 0.75, 1: 0.85, 2: 0.95}.items()],
    )
    for i, v in enumerate([0.800, 0.801, 0.802]):
        write_repeat(tmp_path, f"repro_seed0_r{i}", v)
    out = run_cli(tmp_path).stdout
    assert "UNDERPOWERED" in out


def test_delta_clearing_both_floors_reports_direction(tmp_path: Path) -> None:
    write_summary(
        tmp_path,
        [("yolo11n_baseline", s, v) for s, v in {0: 0.700, 1: 0.701, 2: 0.702}.items()]
        + [("yolo11n_eca", s, v) for s, v in {0: 0.800, 1: 0.801, 2: 0.802}.items()],
    )
    for i, v in enumerate([0.700, 0.7005, 0.701]):
        write_repeat(tmp_path, f"repro_seed0_r{i}", v)
    out = run_cli(tmp_path).stdout
    assert "DELTA CLEARS BOTH FLOORS" in out
    assert "improves" in out


# --- defect injection -------------------------------------------------------


def test_missing_runs_dir_exits_nonzero(tmp_path: Path) -> None:
    p = run_cli(tmp_path / "nope")
    assert p.returncode == 1
    assert "runs dir not found" in p.stderr


def test_missing_summary_is_not_an_error(tmp_path: Path) -> None:
    assert R.read_summary(tmp_path / "ab_summary.csv", "mAP50") == {}


def test_one_armed_summary_reports_incomplete(tmp_path: Path) -> None:
    write_summary(tmp_path, [("yolo11n_baseline", 0, 0.80)])
    p = run_cli(tmp_path)
    assert p.returncode == 0
    assert "INCOMPLETE" in p.stdout


def test_blank_metric_cells_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "ab_summary.csv").write_text(
        SUMMARY_HEADER + "yolo11n_eca,0,50,,,1.0\nyolo11n_eca,1,50,0.75,0.45,1.0\n"
    )
    assert R.read_summary(tmp_path / "ab_summary.csv", "mAP50") == {"yolo11n_eca": {1: 0.75}}


def test_repeat_dir_without_results_csv_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "repro_seed0_r0").mkdir()
    write_repeat(tmp_path, "repro_seed0_r1", 0.5)
    assert sorted(R.read_repeats(tmp_path, "mAP50")) == ["repro_seed0_r1"]


# --- seed-paired analysis (#500) ---------------------------------------------
# Fixture is the committed #500 matrix (detector/results/ab_2026-09-28/
# per_seed.csv); expected values are scipy.stats.ttest_rel on the same data.

B50 = {0: 0.96547, 1: 0.97139, 2: 0.9673}
E50 = {0: 0.9685, 1: 0.97096, 2: 0.96763}
B95 = {0: 0.87505, 1: 0.88419, 2: 0.87929}
E95 = {0: 0.87837, 1: 0.88756, 2: 0.88363}


def test_t_cdf_matches_closed_forms():
    import math

    # df=1 (Cauchy) and df=2 have closed forms; the continued fraction must hit both.
    for t in (0.1, 0.93, 2.0, 11.07):
        assert abs(R.t_two_sided_p(t, 1) - (1 - 2 / math.pi * math.atan(t))) < 1e-10
        assert abs(R.t_two_sided_p(t, 2) - (1 - t / math.sqrt(2 + t * t))) < 1e-10


def test_t_critical_known_values():
    assert abs(R.t_critical(2) - 4.302653) < 1e-5
    assert abs(R.t_critical(4) - 2.776445) < 1e-5
    assert abs(R.t_critical(30) - 2.042272) < 1e-5


def test_paired_map50_matches_scipy_and_is_inconsistent():
    pr = R.paired(B50, E50)
    assert pr["n"] == 3 and pr["df"] == 2
    assert abs(pr["mean"] - 0.00098) < 1e-5
    assert abs(pr["t"] - 0.9312) < 1e-3
    assert abs(pr["p"] - 0.450) < 1e-3
    lo, hi = pr["ci"]
    assert lo < 0 < hi
    assert not pr["consistent"] and (pr["pos"], pr["neg"]) == (2, 1)


def test_paired_map50_95_matches_scipy_and_is_consistent():
    pr = R.paired(B95, E95)
    assert abs(pr["mean"] - 0.003677) < 1e-5
    assert abs(pr["t"] - 11.07) < 0.01
    assert abs(pr["p"] - 0.00806) < 1e-4
    lo, hi = pr["ci"]
    assert 0 < lo < hi and abs(lo - 0.00225) < 1e-4 and abs(hi - 0.00511) < 1e-4
    assert pr["consistent"] and pr["pos"] == 3


def test_paired_uses_only_matched_seeds():
    pr = R.paired({0: 1.0, 1: 2.0, 5: 9.0}, {0: 1.5, 1: 2.5, 7: 0.0})
    assert pr["seeds"] == [0, 1]


def test_paired_needs_two_matched_seeds():
    assert R.paired({0: 1.0}, {0: 2.0}) is None
    assert R.paired({0: 1.0, 1: 1.0}, {2: 1.0, 3: 1.0}) is None


def test_paired_identical_deltas_do_not_divide_by_zero():
    pr = R.paired({0: 1.0, 1: 2.0}, {0: 1.5, 1: 2.5})
    assert pr["p"] == 0.0 and pr["consistent"]
    pr = R.paired({0: 1.0, 1: 2.0}, {0: 1.0, 1: 2.0})
    assert pr["p"] == 1.0


def test_cli_prints_paired_block_and_reference(tmp_path):
    import csv as _csv

    with open(tmp_path / "ab_summary.csv", "w", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["arm", "seed", "epochs", "mAP50", "mAP50_95", "train_box_loss"])
        for s in (0, 1, 2):
            w.writerow(["yolo11n_baseline", s, 50, B50[s], B95[s], 0.4])
            w.writerow(["yolo11n_eca", s, 50, E50[s], E95[s], 0.4])
    for i in range(3):
        d = tmp_path / f"repro_seed0_r{i}"
        d.mkdir()
        (d / "results.csv").write_text(
            "epoch,metrics/mAP50(B),metrics/mAP50-95(B)\n10,0.88435,0.7\n"
        )
    cp = run_cli(tmp_path, "--metric", "mAP50_95", "--reference-delta", "0.0125")
    assert cp.returncode == 0, cp.stderr
    out = cp.stdout
    assert "pairing licensed" in out
    assert "consistent, all 3 seeds positive" in out
    assert "the CI excludes 0" in out
    assert "OUTSIDE the CI" in out
    assert 'floor is exactly zero, so "clears it"' in out
