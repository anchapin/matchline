"""Resource limits for untrusted file imports.

All limits are configurable via environment variables with safe defaults.
"""

from __future__ import annotations

import os

MAX_IFC_SIZE_MB: int = int(os.environ.get("MATCHLINE_MAX_IFC_SIZE_MB", 100))
MAX_IFC_ELEMENTS: int = int(os.environ.get("MATCHLINE_MAX_IFC_ELEMENTS", 500_000))

MAX_XML_SIZE_MB: int = int(os.environ.get("MATCHLINE_MAX_XML_SIZE_MB", 50))
MAX_XML_NODES: int = int(os.environ.get("MATCHLINE_MAX_XML_NODES", 100_000))

MAX_IMAGE_SIZE_MB: int = int(os.environ.get("MATCHLINE_MAX_IMAGE_SIZE_MB", 20))


def check_file_size(path: str, max_mb: int) -> None:
    """Check file size against limit, raise ValueError if exceeded."""
    from pathlib import Path

    file_size_mb = Path(path).stat().st_size / (1024 * 1024)
    if file_size_mb > max_mb:
        raise ValueError(
            f"File '{path}' is {file_size_mb:.1f} MB, "
            f"exceeds the {max_mb} MB limit. "
            f"Set MATCHLINE_MAX_*_SIZE_MB to increase the limit."
        )


class _NodeCountingParser:
    """XML parser wrapper that counts elements and raises on complexity limit."""

    def __init__(self, max_nodes: int) -> None:
        self.max_nodes = max_nodes
        self._count = 0

    def start(self, tag: str, attrib: dict) -> None:
        self._count += 1
        if self._count > self.max_nodes:
            raise ValueError(
                f"XML document has more than {self._count} elements, "
                f"exceeds the {self.max_nodes} limit. "
                f"Set MATCHLINE_MAX_XML_NODES to increase the limit."
            )

    def end(self, tag: str) -> None:
        pass

    def data(self, data: str) -> None:
        pass


def make_node_counting_parser(max_nodes: int) -> _NodeCountingParser:
    """Create a node-counting parser for XML complexity validation."""
    return _NodeCountingParser(max_nodes)
