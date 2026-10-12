"""matchline demo: one command from sample set to packaged trust report (#753)."""

import time


def test_demo_parser_defaults():
    import cli

    args = cli.build_parser().parse_args(["demo"])
    assert args.seed == 7 and args.out == "matchline-demo" and args.no_open is False


def test_demo_runs_the_pipeline_and_packages_the_report(tmp_path):
    import cli
    from building_model import BuildingModel

    out = tmp_path / "demo"
    t0 = time.time()
    cli.main(["demo", "--out", str(out), "--no-open"])
    took = time.time() - t0

    pkg = out / "package"
    assert (pkg / "trust_report.html").exists()
    assert (pkg / "trust_report.json").exists()
    assert (pkg / "DISCLAIMER.txt").exists()
    assert (out / "review" / "review.html").exists()
    assert list(pkg.glob("*.xml")) and list(pkg.glob("*.ifc"))
    # the trust report is the demo's headline: it must name its numbers
    tr = (pkg / "trust_report.json").read_text()
    assert "matchline.trust_report" in tr
    assert took < 300, f"demo took {took:.0f}s; the acceptance is under five minutes"
    # a demo run leaves a loadable model behind
    m = BuildingModel.from_json((out / "review" / "model.json").read_text())
    assert m.spaces


def test_demo_does_not_open_a_browser_when_asked_not_to(tmp_path, monkeypatch):
    import cli

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: opened.append(a) or True)
    cli.main(["demo", "--out", str(tmp_path / "d2"), "--no-open"])
    assert opened == []
