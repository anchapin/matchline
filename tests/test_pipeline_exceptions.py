"""Tests for pipeline_exceptions.py: pipeline exception hierarchy."""

from __future__ import annotations

import pytest

from pipeline_exceptions import PipelineDependencyError, PipelineError, PipelineValidationError


class TestPipelineErrorInheritance:
    def test_pipeline_error_is_exception(self):
        err = PipelineError("test message")
        assert isinstance(err, Exception)

    def test_pipeline_error_message(self):
        msg = "something went wrong"
        err = PipelineError(msg)
        assert str(err) == msg

    def test_pipeline_validation_error_inherits_from_pipeline_error(self):
        err = PipelineValidationError("validation failed")
        assert isinstance(err, PipelineError)
        assert isinstance(err, Exception)

    def test_pipeline_validation_error_message(self):
        err = PipelineValidationError("field X is invalid")
        assert str(err) == "field X is invalid"

    def test_pipeline_dependency_error_inherits_from_pipeline_error(self):
        err = PipelineDependencyError("missing dependency")
        assert isinstance(err, PipelineError)
        assert isinstance(err, Exception)

    def test_pipeline_dependency_error_message(self):
        err = PipelineDependencyError("module X not found")
        assert str(err) == "module X not found"

    def test_validation_and_dependency_are_distinct_exceptions(self):
        val_err = PipelineValidationError("val")
        dep_err = PipelineDependencyError("dep")
        assert not isinstance(val_err, PipelineDependencyError)
        assert not isinstance(dep_err, PipelineValidationError)

    def test_validation_error_catchable_as_pipeline_error(self):
        caught = []
        try:
            raise PipelineValidationError("validation")
        except PipelineError as e:
            caught.append(type(e).__name__)
        assert caught == ["PipelineValidationError"]

    def test_dependency_error_catchable_as_pipeline_error(self):
        caught = []
        try:
            raise PipelineDependencyError("dependency")
        except PipelineError as e:
            caught.append(type(e).__name__)
        assert caught == ["PipelineDependencyError"]

    def test_error_catchable_as_generic_exception(self):
        with pytest.raises(Exception) as exc_info:
            raise PipelineError("generic")
        assert str(exc_info.value) == "generic"

    def test_validation_error_catchable_as_generic_exception(self):
        with pytest.raises(Exception) as exc_info:
            raise PipelineValidationError("val")
        assert isinstance(exc_info.value, PipelineValidationError)

    def test_dependency_error_catchable_as_generic_exception(self):
        with pytest.raises(Exception) as exc_info:
            raise PipelineDependencyError("dep")
        assert isinstance(exc_info.value, PipelineDependencyError)
