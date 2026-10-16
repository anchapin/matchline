"""Telemetry wiring (#1037): consent-gated emission from run_pipeline.

Nothing is emitted without consent: a run without --telemetry (or a
telemetry_consent config key) writes no telemetry.json, a consented run
writes one spec-conforming payload to <out-dir>/telemetry.json, and
telemetry_local_only in the config disables emission entirely.
"""

from __future__ import annotations

import json
from pathlib import Path

import cli
import run_pipeline


def _args(tmp_path: Path, extra: list[str]):
    argv = ["run", "--seed", "101", "--out-dir", str(tmp_path / "out"), *extra]
    return cli.build_parser().parse_args(argv)


def test_no_telemetry_without_consent(tmp_path: Path):
    run_pipeline.main(_args(tmp_path, []))
    assert not (tmp_path / "out" / "telemetry.json").exists()


def test_flag_writes_payload(tmp_path: Path):
    run_pipeline.main(_args(tmp_path, ["--telemetry"]))
    payload = json.loads((tmp_path / "out" / "telemetry.json").read_text())
    assert payload["consent"] is True
    assert payload["spec_version"] == "matchline.telemetry-spec/1"
    stages = payload["stage_outcomes"]
    assert stages["1.generate"]["outcome"] == "ok"
    assert stages["6.export"]["outcome"] == "ok"
    assert stages["6.export"]["seconds"] >= 0


def test_config_key_grants_consent(tmp_path: Path):
    run_pipeline.main(_args(tmp_path, []), {"telemetry_consent": True})
    payload = json.loads((tmp_path / "out" / "telemetry.json").read_text())
    assert payload["consent"] is True
    assert payload["stage_outcomes"]["4.validation"]["outcome"] == "ok"


def test_local_only_wins(tmp_path: Path):
    run_pipeline.main(_args(tmp_path, ["--telemetry"]), {"telemetry_local_only": True})
    assert not (tmp_path / "out" / "telemetry.json").exists()
