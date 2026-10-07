"""Release license check (#751).

Reads ``license_ledger.json`` and fails when:

* the ledger is malformed (unknown class, an artifact naming an unknown dataset,
  a share-alike artifact without its notice)
* a committed file that looks like a trained artifact or derived data is not in
  the ledger
* a built wheel or sdist given on the command line contains an artifact that is
  evaluation-only (non-commercial or copyleft training data), a share-alike
  artifact without NOTICE.md, or an artifact-like file the ledger doesn't know

Usage::

    python scripts/check_release_licenses.py                # ledger + committed files
    python scripts/check_release_licenses.py dist/*.whl     # ... and these builds
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "license_ledger.json"
SCHEMA = "matchline.license_ledger/1"
CLASSES = ("permissive", "share_alike", "noncommercial", "copyleft")
EVAL_ONLY = ("noncommercial", "copyleft")
# the worst class among an artifact's datasets decides what may be done with it
_RANK = {c: i for i, c in enumerate(CLASSES)}
ARTIFACT_PATTERNS = (
    "*.npz", "*.pt", "*.pth", "*.onnx", "*.pkl", "*.joblib", "*.safetensors",
    "*.h5", "*.ckpt", "*.tflite", "*priors*.json",
)  # fmt: skip


def load(path: Path = LEDGER) -> dict:
    return json.loads(Path(path).read_text())


def artifact_class(ledger: dict, art: dict) -> str:
    ds = {d["id"]: d for d in ledger.get("datasets", [])}
    classes = [ds[i]["class"] for i in art.get("datasets", []) if i in ds]
    return max(classes, key=lambda c: _RANK.get(c, len(CLASSES))) if classes else "unknown"


def validate(ledger: dict) -> List[str]:
    errs: List[str] = []
    if ledger.get("schema") != SCHEMA:
        errs.append(f"ledger schema is {ledger.get('schema')!r}, expected {SCHEMA}")
    ids = set()
    for d in ledger.get("datasets", []):
        for k in ("id", "name", "license", "class", "source"):
            if not d.get(k):
                errs.append(f"dataset {d.get('id', '?')}: missing {k}")
        if d.get("class") not in CLASSES:
            errs.append(f"dataset {d.get('id')}: unknown class {d.get('class')!r}")
        if d.get("id") in ids:
            errs.append(f"dataset {d['id']} listed twice")
        ids.add(d.get("id"))
    for a in ledger.get("artifacts", []):
        p = a.get("path", "?")
        if not a.get("datasets"):
            errs.append(f"artifact {p}: no datasets listed")
        for i in a.get("datasets", []):
            if i not in ids:
                errs.append(f"artifact {p}: unknown dataset {i!r}")
        if artifact_class(ledger, a) == "share_alike" and not a.get("notice"):
            errs.append(f"artifact {p}: share-alike data needs a notice")
    return errs


def looks_like_artifact(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, pat) for pat in ARTIFACT_PATTERNS)


def _ledgered(ledger: dict, path: str) -> Optional[dict]:
    for a in ledger.get("artifacts", []):
        ap = a["path"]
        if path == ap or path.endswith("/" + ap) or fnmatch.fnmatch(path, ap):
            return a
    return None


def committed_files(root: Path = ROOT) -> List[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    return [f for f in out.splitlines() if f]


def check_committed(ledger: dict, files: Iterable[str]) -> List[str]:
    return [
        f"{f}: committed artifact is not in license_ledger.json"
        for f in files
        if looks_like_artifact(f) and _ledgered(ledger, f) is None
    ]


def dist_members(path: Path) -> List[str]:
    path = Path(path)
    if path.suffix in (".whl", ".zip"):
        with zipfile.ZipFile(path) as z:
            return z.namelist()
    with tarfile.open(path) as t:
        return [m.name for m in t.getmembers() if m.isfile()]


def check_dist(ledger: dict, name: str, members: List[str]) -> List[str]:
    errs: List[str] = []
    has_notice = any(m.rsplit("/", 1)[-1] == "NOTICE.md" for m in members)
    for m in members:
        a = _ledgered(ledger, m)
        if a is None:
            if looks_like_artifact(m):
                errs.append(f"{name}: {m} looks like a trained artifact and is not in the ledger")
            continue
        cls = artifact_class(ledger, a)
        if cls in EVAL_ONLY:
            errs.append(f"{name}: {m} is evaluation-only ({cls} training data) and must not ship")
        elif cls == "share_alike" and not has_notice:
            errs.append(f"{name}: {m} is share-alike and the build has no NOTICE.md")
    return errs


def run(dists: List[str], ledger_path: Path = LEDGER, root: Path = ROOT) -> List[str]:
    ledger = load(ledger_path)
    errs = validate(ledger)
    errs += check_committed(ledger, committed_files(root))
    for d in dists:
        errs += check_dist(ledger, Path(d).name, dist_members(Path(d)))
    return errs


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    errs = run(argv)
    for e in errs:
        print(f"LICENSE: {e}", file=sys.stderr)
    if errs:
        return 1
    ledger = load()
    counts: Dict[str, int] = {}
    for a in ledger["artifacts"]:
        c = artifact_class(ledger, a)
        counts[c] = counts.get(c, 0) + 1
    n = len(ledger["datasets"])
    print(f"license ledger OK: {n} datasets, {counts}; checked {len(argv)} build(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
