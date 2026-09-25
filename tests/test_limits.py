"""Tests for limits.py: file-size and XML-node-count limits."""

from __future__ import annotations

import pytest

from limits import _NodeCountingParser, check_file_size, make_node_counting_parser


class TestCheckFileSize:
    def test_file_within_limit(self, tmp_path):
        path = tmp_path / "small.txt"
        path.write_bytes(b"x" * 100)
        check_file_size(str(path), max_mb=1)

    def test_file_at_exact_limit(self, tmp_path):
        path = tmp_path / "exact.txt"
        path.write_bytes(b"x" * 100)
        check_file_size(str(path), max_mb=1)

    def test_file_exceeds_limit_raises(self, tmp_path):
        path = tmp_path / "large.txt"
        path.write_bytes(b"x" * (2 * 1024 * 1024))
        with pytest.raises(ValueError, match="exceeds the"):
            check_file_size(str(path), max_mb=1)

    def test_missing_file_raises(self, tmp_path):
        missing = tmp_path / "does_not_exist.txt"
        with pytest.raises(FileNotFoundError):
            check_file_size(str(missing), max_mb=1)

    def test_error_message_includes_filename(self, tmp_path):
        path = tmp_path / "big.txt"
        path.write_bytes(b"x" * (5 * 1024 * 1024))
        with pytest.raises(ValueError, match="big.txt"):
            check_file_size(str(path), max_mb=1)


class TestNodeCountingParser:
    def test_init_sets_max_nodes_and_count(self):
        parser = _NodeCountingParser(max_nodes=50)
        assert parser.max_nodes == 50
        assert parser._count == 0

    def test_start_increments_count(self):
        parser = _NodeCountingParser(max_nodes=10)
        parser.start("root", {})
        assert parser._count == 1

    def test_start_counts_nested_elements(self):
        parser = _NodeCountingParser(max_nodes=10)
        parser.start("root", {})
        parser.start("parent", {})
        parser.start("child", {})
        assert parser._count == 3

    def test_start_over_limit_raises(self):
        parser = _NodeCountingParser(max_nodes=2)
        parser.start("root", {})
        parser.start("child", {})
        with pytest.raises(ValueError, match="exceeds the"):
            parser.start("grandchild", {})

    def test_end_does_not_affect_count(self):
        parser = _NodeCountingParser(max_nodes=10)
        parser.start("root", {})
        parser.end("root")
        assert parser._count == 1

    def test_data_does_not_affect_count(self):
        parser = _NodeCountingParser(max_nodes=10)
        parser.start("root", {})
        parser.data("some text content")
        assert parser._count == 1

    def test_attributes_dont_count(self):
        parser = _NodeCountingParser(max_nodes=10)
        parser.start("el", {"attr1": "v1", "attr2": "v2"})
        assert parser._count == 1

    def test_whitespace_data_does_not_count(self):
        parser = _NodeCountingParser(max_nodes=10)
        parser.start("root", {})
        parser.data("   \n\t  ")
        assert parser._count == 1

    def test_at_limit_exactly_no_error(self):
        parser = _NodeCountingParser(max_nodes=3)
        parser.start("root", {})
        parser.start("a", {})
        parser.start("b", {})
        assert parser._count == 3


class TestMakeNodeCountingParser:
    def test_returns_node_counting_parser(self):
        parser = make_node_counting_parser(max_nodes=50)
        assert isinstance(parser, _NodeCountingParser)

    def test_parser_has_correct_max_nodes(self):
        parser = make_node_counting_parser(max_nodes=50)
        assert parser.max_nodes == 50

    def test_parser_count_starts_at_zero(self):
        parser = make_node_counting_parser(max_nodes=100)
        assert parser._count == 0
