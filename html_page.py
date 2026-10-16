"""The page scaffolding every matchline HTML page shares (#1044).

render_html in export_package and review_report both started from the
same four lines (doctype, title, style, h1); duplicating them let the
pages drift one edit at a time, which is exactly the style-drift risk
the audit named (#1044). One helper now emits the prefix, and each page
keeps its own CSS and body.
"""

from __future__ import annotations

import html


def _esc(x) -> str:
    # Same rule as export_package._e: None becomes an empty value, not "None".
    return html.escape("" if x is None else str(x))


def page_head(title: str, css: str, h1: str) -> str:
    """The doctype/head/style/h1 prefix of a matchline HTML page."""
    return "\n".join(
        [
            "<!doctype html><html><head><meta charset=utf-8>",
            f"<title>{_esc(title)}</title>",
            f"<style>{css}</style></head><body>",
            f"<h1>{_esc(h1)}</h1>",
        ]
    )
