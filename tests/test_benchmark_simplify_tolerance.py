"""Benchmark-path tolerance units (#1374).

``BEMModel.simplify_tolerance`` is stored in PERCENT and
``model_from_linked_model`` validates it against the shared 3% cap. The
benchmark harnesses (``scripts/benchmark.py``, ``scripts/schedule_bench.py``)
carry their tolerance as a FRACTION (0.02 = 2% of the original area, the
value ``simplify_ring`` takes), so each one converts through the one shared
helper ``bem.bem_geometry.simplify_tolerance_pct`` before calling the
factory. These tests pin that conversion on the script paths: the default
tolerance lands as 2.0, and a 5% tolerance is refused by the shared rule
instead of passing silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geometry.geometry_simplify import footprint_from_regions, simplify_ring  # noqa: E402
from scripts.benchmark import Building, _bem_model, _write_gbxml  # noqa: E402
from scripts.schedule_bench import _bem_model_for_export, export_validity  # noqa: E402
from tests.model_factory import make_clean_model  # noqa: E402


def _simplified(model):
    """The harness's own simplification of a synthetic building."""
    ring = footprint_from_regions([sp.polygon_m for sp in model.spaces.values()])
    return simplify_ring(ring, tol=0.02, wall_height=model.levels[0].wall_height_m)


def test_benchmark_bem_model_stores_the_tolerance_in_percent() -> None:
    """The benchmark's default fraction (0.02) reaches the factory as 2.0%."""
    model = make_clean_model()
    sres = _simplified(model)
    bem = _bem_model(model, sres, model.levels[0].wall_height_m, 0.02)
    assert bem.simplify_tolerance == 2.0


def test_benchmark_bem_model_refuses_a_tolerance_over_three_percent() -> None:
    """5% (0.05 as a fraction) trips the shared >3% rule, not a silent pass."""
    model = make_clean_model()
    sres = _simplified(model)
    with pytest.raises(ValueError, match="exceeds maximum threshold 3%"):
        _bem_model(model, sres, model.levels[0].wall_height_m, 0.05)


def test_benchmark_simplify_ring_still_gets_the_fraction() -> None:
    """The simplification itself keeps the fraction (0.02 = 2% of the area)."""
    import scripts.benchmark as bench

    seen: list[float] = []
    original = bench.simplify_ring

    def spy(ring, tol=0.02, **kwargs):
        seen.append(tol)
        return original(ring, tol=tol, **kwargs)

    bench.simplify_ring = spy
    try:
        bench._simplify_model(make_clean_model(), 0.02)
    finally:
        bench.simplify_ring = original
    assert seen == [0.02]


def test_benchmark_write_gbxml_refuses_a_five_percent_tolerance(
    tmp_path: Path, capsys
) -> None:
    """The gbXML export path refuses 5% and writes nothing (#1374)."""
    model = make_clean_model()
    sres = _simplified(model)
    out = _write_gbxml(
        Building(name="synth"),
        tmp_path,
        model,
        sres,
        model.levels[0].wall_height_m,
        0.05,
    )
    assert out is None
    assert not (tmp_path / "synth-gbxml" / "model.gbxml").exists()
    # the reason is reported, not swallowed
    assert "exceeds maximum threshold 3%" in capsys.readouterr().err


def test_schedule_bench_bem_model_stores_the_tolerance_in_percent() -> None:
    """The schedule benchmark's hard-coded fraction reaches the factory as 2.0%."""
    model = make_clean_model()
    sres = _simplified(model)
    bem = _bem_model_for_export(model, sres, model.levels[0].wall_height_m)
    assert bem.simplify_tolerance == 2.0


def test_schedule_bench_export_validity_passes_percent_to_the_factory(
    tmp_path: Path, monkeypatch
) -> None:
    """``export_validity`` hands the factory 2.0 (PERCENT), not 0.02."""
    import run_pipeline
    from tests.ifc_builder import IfcBuilder

    seen: dict = {}
    real = run_pipeline.model_from_linked_model

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(run_pipeline, "model_from_linked_model", spy)

    ifc = tmp_path / "arch.ifc"
    b = IfcBuilder()
    b.space("ROOM 101", [(0, 0), (4, 0), (4, 4), (0, 4)], height=3.0, long_name="Room")
    b.write(ifc)

    result = export_validity("synth", ifc, tmp_path / "out")
    assert result["status"] == "valid", result
    assert seen["simplify_tolerance"] == 2.0
