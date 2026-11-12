"""Benchmark scripts record the simplification tolerance in PERCENT (#1374).

``model_from_linked_model`` (run_pipeline.py) takes the simplification
tolerance in PERCENT -- the same unit as ``BEMModel.simplify_tolerance``
-- and rejects anything over the shared 3% maximum. The benchmark scripts
passed their FRACTION (0.02) straight through, so the exported model
recorded ``0.02%`` and the >3% guard could never trip from a benchmark run.

These tests pin the two scripts to the shared fraction-to-percent helper
(``bem.bem_geometry.simplify_tolerance_pct``): the default tolerance is
recorded as 2.0 percent, and a 5% budget (fraction 0.05) now fails the
>3% validation instead of passing silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark import Building, _simplify_model, _write_gbxml  # noqa: E402


def _model():
    from tests.model_factory import make_clean_model

    return make_clean_model()


def test_benchmark_path_records_simplify_tolerance_in_percent(tmp_path, monkeypatch):
    """The default 0.02 fraction is recorded on the BEM model as 2.0 percent."""
    captured: dict = {}

    def fake_write(bem, out):
        captured["bem"] = bem
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("<gbXML/>")
        return out

    monkeypatch.setattr("bem.bem_export.write_gbxml", fake_write)

    model = _model()
    simp = _simplify_model(model, 0.02)
    out = _write_gbxml(
        Building(name="syn"),
        tmp_path,
        model,
        simp["sres"],
        simp["wall_height"],
        0.02,
    )
    assert out is not None
    assert captured["bem"].simplify_tolerance == 2.0


def test_benchmark_path_rejects_a_tolerance_over_three_percent(tmp_path, capsys):
    """A 0.05 fraction is 5% -- over the shared maximum, so the export fails."""
    model = _model()
    simp = _simplify_model(model, 0.05)
    out = _write_gbxml(
        Building(name="syn"),
        tmp_path,
        model,
        simp["sres"],
        simp["wall_height"],
        0.05,
    )
    assert out is None
    err = capsys.readouterr().err
    assert "exceeds maximum threshold" in err
    assert "5.00%" in err


def test_schedule_bench_path_records_simplify_tolerance_in_percent(tmp_path, monkeypatch):
    """``scripts/schedule_bench.py`` converts its 0.02 fraction to 2.0 percent."""
    import scripts.schedule_bench as sb

    captured: dict = {}

    def fake_write(bem, out):
        captured["bem"] = bem
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("<buildingsync/>")
        return out

    monkeypatch.setattr(sb, "write_buildingsync", fake_write)
    monkeypatch.setattr(sb, "validate_buildingsync", lambda path: (True, []))
    monkeypatch.setattr("ifc.ifc_import.import_ifc", lambda path: _model())

    arch_ifc = tmp_path / "arch.ifc"
    arch_ifc.write_bytes(b"not really an ifc")
    result = sb.export_validity("syn", arch_ifc, tmp_path)

    assert result["status"] == "valid"
    assert captured["bem"].simplify_tolerance == 2.0
