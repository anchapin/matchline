"""Triage published (origin) branches against develop's CURRENT tree.

Method per #515 comment 2: three-dot diffs and `git cherry` both lie under
squash-merge, so we compare touched paths' blob SHAs against develop's tip and
measure added-line coverage. Also records deletions develop never took, which
the earlier pass explicitly flagged as a gap in the metric.
"""

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def sh(*a):
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True).stdout


branches = [
    b
    for b in sh("for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/").split()
    if b not in ("origin/HEAD", "origin/develop", "origin/main")
]

out = []
for b in branches:
    if (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", b, "origin/develop"],
            cwd=REPO,
            capture_output=True,
        ).returncode
        == 0
    ):
        continue  # already reachable from develop
    base = sh("merge-base", b, "origin/develop").strip()
    if not base:
        continue
    touched = [
        l.split("\t", 1)[1]
        for l in sh("diff", "--name-status", base, b).strip().splitlines()
        if "\t" in l
    ]
    identical = notin = differs = 0
    added_total = added_present = 0
    deletions_still_in_develop = 0
    for p in touched:
        bsha = sh("rev-parse", f"{b}:{p}").strip()
        dsha = sh("rev-parse", f"origin/develop:{p}").strip()
        if not dsha or "unknown" in dsha or len(dsha) != 40:
            notin += 1
            dev_lines = None
        elif bsha == dsha:
            identical += 1
            dev_lines = None
        else:
            differs += 1
            dev_lines = set(sh("show", f"origin/develop:{p}").splitlines())
        diff = sh("diff", base, b, "--", p).splitlines()
        adds = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
        dels = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
        added_total += len([a for a in adds if a.strip()])
        if dev_lines is None and bsha == dsha:
            added_present += len([a for a in adds if a.strip()])
        elif dev_lines is not None:
            added_present += len([a for a in adds if a.strip() and a in dev_lines])
            deletions_still_in_develop += len([d for d in dels if d.strip() and d in dev_lines])
    cov = (added_present / added_total * 100) if added_total else None
    out.append(
        dict(
            branch=b,
            files=len(touched),
            identical=identical,
            absent_from_develop=notin,
            differs=differs,
            added=added_total,
            coverage=None if cov is None else round(cov, 1),
            deletions_not_taken=deletions_still_in_develop,
            tip=sh("rev-parse", "--short", b).strip(),
        )
    )

out.sort(key=lambda r: (r["coverage"] is None, r["coverage"] or 0))
print(json.dumps(out, indent=1))
