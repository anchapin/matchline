"""Conservation law invariant tests for issue #403.

Tests that area_closure, volume_closure, and envelope_closure
correctly identify invariant violations and block export.
"""


from tests.model_factory import make_clean_model
from validate import export_gate, run_checks
from validate.conservation import area_closure, envelope_closure, volume_closure


def is_ok(result):
    """CheckResult passes if severity is not 'error'."""
    return result.severity != "error"


class TestAreaClosure:
    def test_area_closure_pass_on_clean_model(self):
        """area_closure returns ok=True when room areas sum to footprint area."""
        m = make_clean_model()
        result = area_closure(m)
        assert is_ok(result), f"expected pass but got: {result.message}"

    def test_area_closure_fail_on_mismatched_area(self):
        """area_closure returns ok=False when room areas don't sum to footprint."""
        m = make_clean_model()
        m.spaces["L1-101"].area_m2 = 20.0  # inject defect
        result = area_closure(m)
        assert not is_ok(result), f"expected fail but got: {result.message}"
        assert result.check_id == "area_closure"

    def test_area_closure_error_message_contains_values(self):
        """area_closure error message shows actual vs expected values."""
        m = make_clean_model()
        m.spaces["L1-101"].area_m2 = 20.0
        result = area_closure(m)
        assert not is_ok(result)
        # message should contain numeric values for actual and expected
        assert "m^2" in result.message or "m²" in result.message


class TestVolumeClosure:
    def test_volume_closure_pass_on_clean_model(self):
        """volume_closure returns ok=True when room volumes sum correctly."""
        m = make_clean_model()
        result = volume_closure(m)
        assert is_ok(result), f"expected pass but got: {result.message}"

    def test_volume_closure_fail_on_mismatched_volume(self):
        """volume_closure returns ok=False when room volumes don't sum correctly."""
        m = make_clean_model()
        m.spaces["L1-101"].volume_m3 = 20.0  # inject defect
        result = volume_closure(m)
        assert not is_ok(result), f"expected fail but got: {result.message}"
        assert result.check_id == "volume_closure"

    def test_volume_closure_error_message_contains_values(self):
        """volume_closure error message shows actual vs expected values."""
        m = make_clean_model()
        m.spaces["L1-101"].volume_m3 = 20.0
        result = volume_closure(m)
        assert not is_ok(result)
        assert "m^3" in result.message or "m³" in result.message


class TestEnvelopeClosure:
    def test_envelope_closure_pass_on_clean_model(self):
        """envelope_closure returns ok=True when opening areas sum to facade area."""
        m = make_clean_model()
        result = envelope_closure(m)
        assert is_ok(result), f"expected pass but got: {result.message}"

    def test_envelope_closure_fail_on_mismatched_opening_area(self):
        """envelope_closure returns ok=False when opening areas don't sum to facade."""
        m = make_clean_model()
        # Find a space with openings and modify an opening area
        for sp in m.spaces.values():
            if sp.openings:
                sp.openings[0].area_m2 = 1000.0  # inject defect - too large
                break
        result = envelope_closure(m)
        assert not is_ok(result), f"expected fail but got: {result.message}"
        assert result.check_id == "envelope_closure"


class TestExportGateBlocksConservation:
    def test_export_gate_blocks_when_area_closure_fails(self):
        """export_gate returns False when area_closure fails."""
        m = make_clean_model()
        m.spaces["L1-101"].area_m2 = 20.0
        report = run_checks(m)
        assert not export_gate(report), "export_gate should block when area_closure fails"

    def test_export_gate_blocks_when_volume_closure_fails(self):
        """export_gate returns False when volume_closure fails."""
        m = make_clean_model()
        m.spaces["L1-101"].volume_m3 = 20.0
        report = run_checks(m)
        assert not export_gate(report), "export_gate should block when volume_closure fails"

    def test_export_gate_blocks_when_envelope_closure_fails(self):
        """export_gate returns False when envelope_closure fails."""
        m = make_clean_model()
        for sp in m.spaces.values():
            if sp.openings:
                sp.openings[0].area_m2 = 1000.0
                break
        report = run_checks(m)
        assert not export_gate(report), "export_gate should block when envelope_closure fails"

    def test_export_gate_allows_clean_model(self):
        """export_gate returns True for a clean model with no violations."""
        m = make_clean_model()
        report = run_checks(m)
        assert export_gate(report), "export_gate should allow clean model"


class TestRunChecksIncludesConservation:
    def test_run_checks_includes_area_closure(self):
        """run_checks runs area_closure and records its result."""
        m = make_clean_model()
        m.spaces["L1-101"].area_m2 = 20.0
        report = run_checks(m)
        area_check = next(
            (c for c in report.results if c.check_id == "area_closure"), None
        )
        assert area_check is not None, "area_closure should be in report.results"
        assert area_check.severity == "error"

    def test_run_checks_includes_volume_closure(self):
        """run_checks runs volume_closure and records its result."""
        m = make_clean_model()
        m.spaces["L1-101"].volume_m3 = 20.0
        report = run_checks(m)
        vol_check = next(
            (c for c in report.results if c.check_id == "volume_closure"), None
        )
        assert vol_check is not None, "volume_closure should be in report.results"
        assert vol_check.severity == "error"

    def test_run_checks_includes_envelope_closure(self):
        """run_checks runs envelope_closure and records its result."""
        m = make_clean_model()
        for sp in m.spaces.values():
            if sp.openings:
                sp.openings[0].area_m2 = 1000.0
                break
        report = run_checks(m)
        env_check = next(
            (c for c in report.results if c.check_id == "envelope_closure"), None
        )
        assert env_check is not None, "envelope_closure should be in report.results"
        assert env_check.severity == "error"
