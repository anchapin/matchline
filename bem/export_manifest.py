"""Checksummed export manifest (#1404).

Every successful, gated export writes a ``<sheet>.export.json`` beside the
published files. The manifest records what was exported, from what, with
which settings, and what the validators and the geometric checks measured.

Determinism (binding constraint):

  * gbXML is byte-deterministic, so the manifest records the sha256 of the
    published bytes.
  * IFC4 is NOT byte-deterministic (a wall-clock header timestamp and
    random GlobalIds), so the manifest records a *normalised / semantic*
    digest: the header timestamp and every GlobalId are normalised away and
    the remaining entity and geometry content is hashed. Raw IFC4 bytes are
    never the recorded identity.

``matchline bem-export --verify <export.json>`` re-hashes the recorded
inputs, re-digests the recorded outputs and reports drift. It never re-runs
the export.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "matchline.export_manifest/1"

# The header timestamp of an IFC file, e.g. '2026-11-15T03:20:34+00:00'.
_TIMESTAMP_RE = re.compile(r"'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2})?'")
# An IFC GlobalId: the first attribute of an IfcRoot entity.
_GUID_ENTITY_RE = re.compile(r"(IFC[A-Z0-9]+)\('([0-9A-Za-z_$]{22})'")
# IfcOwnerHistory carries integer epoch timestamps.
_EPOCH_RE = re.compile(r"(CreationDate|LastChangeDate)=\d+")

REPO_ROOT = Path(__file__).resolve().parents[1]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalise_ifc(text: str) -> str:
    """Strip the non-semantic parts of an IFC4 file.

    The header timestamp and the random GlobalIds are replaced by stable
    placeholders (GlobalIds keep their first-appearance order, so the
    normalised text stays deterministic for one model).
    """
    out = _TIMESTAMP_RE.sub("'<timestamp>'", text)
    out = _EPOCH_RE.sub(lambda m: f"{m.group(1)}=0", out)
    guids: dict[str, str] = {}
    for _entity, guid in _GUID_ENTITY_RE.findall(out):
        if guid not in guids:
            guids[guid] = f"GID{len(guids):04d}"
    for guid, placeholder in guids.items():
        out = out.replace(f"'{guid}'", f"'{placeholder}'")
    return out


def normalised_ifc_digest(path: str | Path) -> str:
    """The semantic digest of an IFC4 file (timestamp and GlobalIds removed)."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return sha256_bytes(normalise_ifc(text).encode("utf-8"))


def git_commit(repo_root: str | Path = REPO_ROOT) -> str:
    """The commit SHA of the exporting code ("unknown" when unavailable)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build_manifest(
    *,
    sheet: str,
    settings: dict,
    inputs: dict,
    outputs: dict,
    validation: dict,
    replay_command: str,
    repo_root: str | Path = REPO_ROOT,
) -> dict:
    """Assemble the manifest record."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sheet": sheet,
        "git_commit": git_commit(repo_root),
        "replay_command": replay_command,
        "settings": settings,
        "inputs": inputs,
        "outputs": outputs,
        "validation": validation,
    }


def write_manifest(manifest: dict, path: str | Path) -> Path:
    """Write the manifest next to the published exports."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


@dataclass
class VerifyReport:
    """The result of verifying a manifest against the files on disk."""

    manifest_path: Path
    ok: bool = True
    drift: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "manifest": str(self.manifest_path),
            "ok": self.ok,
            "checked": self.checked,
            "drift": self.drift,
        }


def _verify_input(name: str, spec: dict, drift: list[str], checked: list[str]) -> None:
    content = spec.get("content")
    path = spec.get("path")
    if content is not None:
        # the content itself is recorded (the schedule CSV): re-hash it
        checked.append(name)
        digest = sha256_bytes(content.encode("utf-8"))
        if digest != spec.get("sha256"):
            drift.append(f"input {name}: recorded content hash {spec.get('sha256')} != {digest}")
        size = len(content.encode("utf-8"))
        if spec.get("bytes") is not None and size != spec["bytes"]:
            drift.append(f"input {name}: recorded {spec['bytes']} bytes, found {size}")
        return
    if not path:
        drift.append(f"input {name}: no path and no recorded content")
        return
    p = Path(path)
    if not p.exists():
        drift.append(f"input {name}: {p} is missing")
        return
    checked.append(name)
    digest = sha256_file(p)
    if digest != spec.get("sha256"):
        drift.append(f"input {name}: {p} hash {digest} != recorded {spec.get('sha256')}")
    size = p.stat().st_size
    if spec.get("bytes") is not None and size != spec["bytes"]:
        drift.append(f"input {name}: {p} is {size} bytes, recorded {spec.get('bytes')}")


def _verify_output(name: str, spec: dict, drift: list[str], checked: list[str]) -> None:
    path = spec.get("path")
    if not path:
        drift.append(f"output {name}: no path recorded")
        return
    p = Path(path)
    if not p.exists():
        drift.append(f"output {name}: {p} is missing")
        return
    checked.append(name)
    if spec.get("digest_kind") == "normalised-semantic":
        digest = normalised_ifc_digest(p)
        recorded = spec.get("normalised_sha256")
        if digest != recorded:
            drift.append(f"output {name}: {p} normalised digest {digest} != recorded {recorded}")
    else:
        digest = sha256_file(p)
        if digest != spec.get("sha256"):
            drift.append(f"output {name}: {p} hash {digest} != recorded {spec.get('sha256')}")
    size = p.stat().st_size
    if spec.get("bytes") is not None and size != spec["bytes"]:
        drift.append(f"output {name}: {p} is {size} bytes, recorded {spec.get('bytes')}")


def verify_manifest(path: str | Path) -> VerifyReport:
    """Re-hash the recorded inputs and re-digest the recorded outputs.

    The export is NOT re-run: only the files on disk are compared with the
    manifest.
    """
    path = Path(path)
    report = VerifyReport(manifest_path=path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        report.ok = False
        report.drift.append(f"manifest {path} could not be read: {exc}")
        return report
    if manifest.get("schema_version") != SCHEMA_VERSION:
        report.ok = False
        report.drift.append(
            f"manifest schema {manifest.get('schema_version')!r} != {SCHEMA_VERSION!r}"
        )
    for name, spec in (manifest.get("inputs") or {}).items():
        _verify_input(name, spec or {}, report.drift, report.checked)
    for name, spec in (manifest.get("outputs") or {}).items():
        _verify_output(name, spec or {}, report.drift, report.checked)
    report.ok = not report.drift
    return report
