"""Browser-level test of the review page (#802).

Loads ``review.html`` from a synthetic drawing-set run in headless Chromium
and drives it the way a reviewer does: keys, the edit prompt, a layer
toggle, the sheet highlight, export, reload. The exported decisions.json is
then replayed with ``matchline review --apply``.

Skipped when Playwright or its Chromium is missing (this dev box lacks the
system libraries). CI installs both and sets MATCHLINE_BROWSER_TESTS=1, which
turns the skip into a failure.
"""

import json
import os
from types import SimpleNamespace

import pytest

import review_report as R
import run_review
from building_model import BuildingModel

REQUIRED = os.environ.get("MATCHLINE_BROWSER_TESTS") == "1"


def _skip(why):
    if REQUIRED:
        pytest.fail(f"MATCHLINE_BROWSER_TESTS=1 but {why}")
    pytest.skip(why)


@pytest.fixture
def page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _skip("playwright is not installed")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as e:  # missing browser or system libraries
            _skip(f"headless Chromium did not start: {str(e).splitlines()[0]}")
        ctx = browser.new_context(accept_downloads=True)
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        yield pg, errors
        browser.close()


def _run(tmp_path):
    from test_real_set import _one_floor

    # two 4'-0" rows that disagree on height leave the plan gap unsized
    rows = [("W1", "4'-0\"", "5'-0\"", "FIXED"), ("W3", "4'-0\"", "7'-0\"", "FIXED")]
    model, _rep = _one_floor(tmp_path, rows)
    return model, R.write_review(tmp_path / "out", model)


def _status(pg, k):
    return pg.locator("#rows tr").nth(k).locator("td").nth(4).inner_text()


def _go(pg, k):
    for _ in range(50):
        pg.keyboard.press("k")
    for _ in range(k):
        pg.keyboard.press("j")


def test_reviewer_drives_the_page_and_the_export_replays(tmp_path, page):
    pg, errors = page
    model, d = _run(tmp_path)
    pg.goto((d / "review.html").as_uri())
    items = pg.evaluate("JSON.parse(document.getElementById('data').textContent).items")
    ids = [i["id"] for i in items]
    gap = next(k for k, i in enumerate(items) if i["kind"] == "opening_unsized")
    others = [k for k in range(len(items)) if k != gap][:2]
    assert len(others) == 2, ids
    assert pg.locator("#rows tr").count() == len(items)

    # c confirms the selected item, j moves down, r rejects
    _go(pg, others[0])
    pg.keyboard.press("c")
    assert _status(pg, others[0]).startswith("confirmed")
    _go(pg, others[1])
    pg.keyboard.press("r")
    assert _status(pg, others[1]).startswith("rejected")

    # e opens the edit prompt, which names the field and the candidate tags
    asked = []

    def answer(dlg):
        asked.append(dlg.message)
        dlg.accept("W1")

    pg.once("dialog", answer)
    _go(pg, gap)
    pg.keyboard.press("e")
    assert asked and "New opening for" in asked[0] and "(W1, W3)" in asked[0]
    assert _status(pg, gap).startswith("edited")

    # s highlights the gap on its plan sheet
    pg.keyboard.press("s")
    oid = items[gap]["id"][3:]
    assert pg.locator(f"[data-el='op:{oid}'].hit").count() == 1

    # a layer toggle hides that layer's drawing and shows it again
    cb = pg.locator(".layers input[data-layer='openings']").first
    layer = pg.locator("g[data-layer='openings']").first
    cb.uncheck()
    assert layer.evaluate("g => g.style.display") == "none"
    cb.check()
    assert layer.evaluate("g => g.style.display") == ""

    # decisions survive a reload (localStorage)
    pg.reload()
    assert _status(pg, others[0]).startswith("confirmed")
    assert _status(pg, gap).startswith("edited")

    # export, then replay with the CLI
    with pg.expect_download() as dl:
        pg.click("#export")
    out = tmp_path / "decisions.json"
    dl.value.save_as(out)
    doc = json.loads(out.read_text())
    assert [(x["id"], x["action"]) for x in doc["decisions"]] == [
        (ids[others[0]], "confirm"),
        (ids[others[1]], "reject"),
        (ids[gap], "edit"),
    ]
    reviewed = tmp_path / "reviewed.json"
    base = dict(confirm=None, reject=None, show_all=False, auto_triage=False, list=False,
                format="text", report=None)  # fmt: skip
    run_review.main(
        SimpleNamespace(**base, model=str(d / "model.json"), apply=str(out), out=str(reviewed))
    )
    m = BuildingModel.from_json(reviewed.read_text())
    q = {i.id: i.status for i in m.review_queue}
    assert q[ids[others[0]]] == "confirmed" and q[ids[others[1]]] == "rejected"
    _sp, op = R._find_opening(m, oid)
    assert op is not None and op.tag == "W1" and op.provenance.method == "review_edit"
    assert errors == []
