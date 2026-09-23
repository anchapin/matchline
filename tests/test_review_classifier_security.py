"""Security tests for the review queue classifier.

These tests verify that the system properly blocks potentially dangerous
file types (e.g., .pkl files) that could be used for supply-chain attacks
via arbitrary code execution on deserialization.
"""

from __future__ import annotations

import pathlib

import pytest

from run_review import SecurityError


class TestNoPklSecurityCheck:
    """Tests for the .pkl file blocking security check."""

    def test_pkl_file_in_review_classifier_raises_security_error(self):
        """If any .pkl file exists in review_classifier dir, SecurityError is raised."""
        from run_review import _check_no_pkl_in_review_classifier

        review_classifier_dir = pathlib.Path(__file__).parent.parent / "review_classifier"
        pkl_files = list(review_classifier_dir.glob("*.pkl"))
        if pkl_files:
            with pytest.raises(SecurityError) as exc_info:
                _check_no_pkl_in_review_classifier()
            assert "Pickle files are blocked" in str(exc_info.value)
            error_msg_lower = str(exc_info.value).lower()
            assert "supply" in error_msg_lower and "chain" in error_msg_lower

    def test_security_error_is_exception_subclass(self):
        """SecurityError should be a subclass of Exception."""
        assert issubclass(SecurityError, Exception)

    def test_security_error_can_be_raised_and_caught(self):
        """SecurityError should be catchable as a standard Exception."""
        with pytest.raises(Exception):
            raise SecurityError("test message")

    def test_security_error_message_preserved(self):
        """SecurityError should preserve its message."""
        msg = "Pickle files are blocked in review_classifier directory"
        with pytest.raises(SecurityError) as exc_info:
            raise SecurityError(msg)
        assert msg in str(exc_info.value)
