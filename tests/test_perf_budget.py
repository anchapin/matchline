"""Performance-budget benchmark mechanics (#1162)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.perf_budget import (
    BUDGETS,
    build_reference_model,
    check_budgets,
    parse_grid,
    prepare_reference_dir,
    run_benchmark,
)

GRID = (2, 2)
LEVELS = 2


def _prepared(tmp_path: Path) -> Path:
    prepare_reference_dir(LEVELS, GRID, tmp_path)
    assert (tmp_path / "reference.ifc").exists()
    return tmp_path


def test_parse_grid():
    assert parse_grid("5x4") == (5, 4)
    assert parse_grid("2x2") == (2, 2)


def test_reference_model_counts():
    m = build_reference_model(levels=LEVELS, grid=GRID)
    assert len(m.levels) == LEVELS
    assert len(m.spaces) == LEVELS * GRID[0] * GRID[1]
    assert len(m.envelope) == 4 * len(m.spaces)
    assert all(len(s.openings) == 1 for s in m.spaces.values())
    # every space sits on its level and is 8x5 m
    for s in m.spaces.values():
        assert s.level_id in {lv.id for lv in m.levels}
        assert s.area_m2 == pytest.approx(40.0)


def test_run_benchmark_phases(tmp_path):
    _prepared(tmp_path)
    phases = run_benchmark(tmp_path)
    assert set(phases) == {"import", "validation", "export"}
    assert phases["import"]["spaces"] == LEVELS * GRID[0] * GRID[1]
    assert phases["import"]["elapsed_s"] >= 0
    assert phases["validation"]["gate_ok"] is True
    assert phases["validation"]["checks_ok"] is True
    assert phases["validation"]["conservation_bad"] == 0
    assert (tmp_path / "out.ifc").exists()
    assert (tmp_path / "out.xml").exists()
    assert phases["export"]["ifc_bytes"] > 0 and phases["export"]["gbxml_bytes"] > 0
    # peak RSS is a monotonic high-water mark: never decreasing across phases
    peaks = [phases[p]["peak_rss_mb"] for p in ("import", "validation", "export")]
    assert peaks == sorted(peaks)
    assert peaks[-1] > 0


def test_check_budgets_flags_violations():
    report = {
        "phases": {
            "import": {"elapsed_s": BUDGETS["import_s"] + 1, "peak_rss_mb": 1.0},
            "validation": {"elapsed_s": 1.0, "peak_rss_mb": 1.0, "gate_ok": False},
        },
        "peak_rss_mb": BUDGETS["peak_rss_mb"] + 1,
        "violations": [],
    }
    v = check_budgets(report)
    assert any("import" in x and "budget" in x for x in v)
    assert any("export gate did not pass" in x for x in v)
    assert any("export: no result recorded" in x for x in v)
    assert any("peak_rss" in x for x in v)


def test_full_run_tiny_no_violations(tmp_path, capsys):
    from scripts.perf_budget import main

    rc = main(
        [
            "--levels",
            str(LEVELS),
            "--grid",
            f"{GRID[0]}x{GRID[1]}",
            "--out",
            str(tmp_path / "perf_report.json"),
        ]
    )
    assert rc == 0
    out = json.loads((tmp_path / "perf_report.json").read_text())
    assert out["violations"] == []
    assert out["phases"]["validation"]["gate_ok"] is True
    assert out["peak_rss_mb"] > 0
    captured = capsys.readouterr()
    assert "PERF BUDGET VIOLATIONS" not in captured.err
