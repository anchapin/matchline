"""The shared page scaffolding (#1044): one prefix for every HTML page.

export_package.render_html (trust report) and review_report.render_html
(review queue) both emitted the same four-line prefix from their own
copies; the copies were the style-drift risk the audit named. One helper
now emits the prefix, and each page keeps its own CSS and body.
"""

from __future__ import annotations

from html_page import page_head


def test_page_head_shape():
    assert page_head("matchline review: Demo", "<style-css>", "Review queue: Demo") == "\n".join(
        [
            "<!doctype html><html><head><meta charset=utf-8>",
            "<title>matchline review: Demo</title>",
            "<style><style-css></style></head><body>",
            "<h1>Review queue: Demo</h1>",
        ]
    )


def test_page_head_escapes_and_none():
    assert "<title>a &lt;b&gt;</title>" in page_head("a <b>", "css", "x")
    # None becomes an empty value, not the string "None"
    assert "<title>matchline trust report: </title>" in page_head(
        "matchline trust report: ", "css", "Trust report: "
    )


def test_both_renderers_start_from_the_shared_prefix():
    from pathlib import Path

    for name in ("export_package.py", "review/review_report.py"):
        src = (Path(__file__).parent.parent / name).read_text()
        body = src.split("def render_html")[1]
        assert "page_head(" in body, name
        assert "<!doctype html>" not in body, f"{name} still carries its own doctype copy"
