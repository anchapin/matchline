# Gradio's pillow cap: does any current release allow pillow >= 13.0? (#1274)

Date: 2026-10-24. Issue: [#1274](https://github.com/anchapin/matchline/issues/1274).

**Conclusion (Branch B): no current gradio release allows `pillow>=13.0`.** Every
published gradio 5.x and 6.x release — including the newest one, 6.30.0 —
declares a `pillow` requirement with an upper bound below 13.0, and gradio's
`main` branch (the unreleased source of truth) still declares
`pillow>=8.0,<13.0`. The cap therefore stands, the pip-audit `--ignore-vuln`
rotations caused by it must stay for now, and **no dependency or workflow file
was changed by this investigation**.

---

## How to read this document

- **What was verified from PyPI primary metadata**: every gradio version
  constraint quoted below was read out of PyPI's JSON API
  (`https://pypi.org/pypi/gradio/<version>/json`, field `info.requires_dist`)
  or out of the `Requires-Dist:` line of the downloaded wheel's `METADATA`.
  Both are the packaging metadata PyPI serves for the release; nothing here is
  inferred.
- **What was verified by the resolver**: the `pip install --dry-run` output
  quoted in §3 is pip's own dependency solver rejecting the pair
  (gradio, pillow>=13) on this machine.
- **What was *not* verified**: nothing about gradio's internal reasons for the
  cap, and nothing about unmerged upstream pull requests. Upstream `main` is
  quoted as-is (§4) as the best available forward-looking signal.

---

## 1. The problem being investigated

`pyproject.toml` declares the optional UI dependency as:

```toml
ui = ["gradio>=4.0"]
```

`.github/workflows/ci.yml` (pip-audit job) currently carries a block of
pillow `--ignore-vuln` pins whose stated reason is, verbatim from the file,
that gradio "pins pillow>=8.0, <13.0 so 13.0.0 is unreachable in every fork
of the lock". That block has been rotated four times in five days:

| Rotation | Date | Issue / PR run | Newly ignored IDs |
|---|---|---|---|
| 1 | 2026-10-21 | #1229 | 24 pillow CVEs (11 then 13 seen by runners) |
| 2 | 2026-10-22 | #1239 | 10 more (`CVE-2026-96636..96644`, `CVE-2026-96778`) |
| 3 | 2026-10-22 | #1249 | `GHSA-78g4-xqwv-q3g8` |
| 4 | 2026-10-24 | #1259 / #1265 | `CVE-2026-112398`, `CVE-2026-114501` |

All of them are the same package (`pillow` 12.3.0), the same fix version
(pillow 13.0.0), and the same cause (gradio's cap). Retiring them therefore
requires a gradio release whose `pillow` constraint admits 13.0.0. This
document answers exactly that question.

## 2. Command used and what it returned

```console
$ python3 -m pip index versions gradio
WARNING: pip index is currently an experimental command. It may be removed or changed in a future release without prior warning.
gradio (6.30.0)
Available versions: 6.30.0, 6.29.1, 6.29.0, 6.28.0, 6.27.0, 6.26.0, 6.25.0,
6.24.0, 6.23.1, 6.22.0, 6.21.0, 6.20.0, 6.19.0, 6.18.0, 6.17.3, 6.16.0,
... 5.50.0, 5.49.1, ... 4.44.1, ... 3.50.2, ... 2.9.4, ... (680 releases)
```

So the newest published release at the time of writing is **gradio 6.30.0**.

## 3. The `pillow` constraint per gradio release

Read from `https://pypi.org/pypi/gradio/<version>/json` →
`info.requires_dist` (filtered to entries mentioning `pillow`), except where
noted:

| gradio version | exact `requires_dist` string for pillow | source |
|---|---|---|
| 6.30.0 (latest) | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.30.0/json |
| 6.29.1 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.29.1/json |
| 6.29.0 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.29.0/json |
| 6.28.0 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.28.0/json |
| 6.27.0 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.27.0/json |
| 6.20.0 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.20.0/json |
| 6.10.0 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.10.0/json |
| 6.1.0 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.1.0/json |
| 6.0.1 | `pillow<13.0,>=8.0` | https://pypi.org/pypi/gradio/6.0.1/json |
| 6.0.0 | `pillow<12.0,>=8.0` | https://pypi.org/pypi/gradio/6.0.0/json |
| 6.0.0.dev0 | `pillow<12.0,>=8.0` | https://pypi.org/pypi/gradio/6.0.0.dev0/json |
| 5.50.0 | `pillow<12.0,>=8.0` | https://pypi.org/pypi/gradio/5.50.0/json |
| 5.33.2 | `pillow<12.0,>=8.0` | wheel METADATA (`pip download gradio==5.33.2 --no-deps`) |
| 5.32.1 | `pillow<12.0,>=8.0` | wheel METADATA (`pip download gradio==5.32.1 --no-deps`) |
| 5.4.0 | `pillow<12.0,>=8.0` | https://pypi.org/pypi/gradio/5.4.0/json |
| 5.0.1 | `pillow<11.0,>=8.0` | wheel METADATA (`pip download gradio==5.0.1 --no-deps`) |
| 5.0.0 | `pillow<11.0,>=8.0` | https://pypi.org/pypi/gradio/5.0.0/json |
| 4.44.1 | `pillow<11.0,>=8.0` | https://pypi.org/pypi/gradio/4.44.1/json |
| 3.50.2 | `pillow<11.0,>=8.0` | https://pypi.org/pypi/gradio/3.50.2/json |
| 3.37.0 (first capped 3.x) | `pillow<11.0,>=8.0` | https://pypi.org/pypi/gradio/3.37.0/json |
| 3.36.1 (last gradio with **no** upper bound) | `pillow` | https://pypi.org/pypi/gradio/3.36.1/json |

The full 5.x line (every `X.Y.Z` release from 5.0.0 to 5.50.0) and the full 6.x
line (every `X.Y.Z` release from 6.0.1 to 6.30.0, plus the 6.0.0 dev builds)
were swept with the same query; the sweep output was:

```
5.0.0 ('pillow<11.0,>=8.0',)
5.0.2 ('pillow<11.0,>=8.0',)
5.1.0 ('pillow<11.0,>=8.0',)
5.3.0 ('pillow<11.0,>=8.0',)
5.4.0 ('pillow<12.0,>=8.0',)
... (every 5.x release) ...
5.50.0 ('pillow<12.0,>=8.0',)
6.0.0 ('pillow<12.0,>=8.0',)
6.0.0.dev0 ('pillow<12.0,>=8.0',)
6.0.1 ('pillow<13.0,>=8.0',)
6.1.0 ('pillow<13.0,>=8.0',)
... (every 6.x release) ...
6.29.1 ('pillow<13.0,>=8.0',)
6.30.0 ('pillow<13.0,>=8.0',)
```

**Not one release in the 4.x, 5.x or 6.x line carries a `pillow` requirement
that admits 13.0.0.** The upper bound moves over time (`<11.0` → `<12.0` →
`<13.0`) but it always stays below 13.0, i.e. below the fix version the
ignored advisories name.

The 3.x line was swept too (230 releases). Its pillow requirement is
**unbounded** (`pillow`, no version specifier) up to and including **3.36.1**
(uploaded 2023-07-07) and capped from **3.37.0** (uploaded 2023-07-17,
`pillow<11.0,>=8.0`) onwards. So the newest gradio that would tolerate
`pillow>=13` is gradio 3.36.1 — a release from July 2023 that predates this
repository's own floor (`ui = ["gradio>=4.0"]`, i.e. gradio 4.0.0 of
2023-10-31) and that predates the current major line (6.x) by three years.
It is not a "current release" and using it would be a large, semantically
breaking downgrade, so it does not open Branch A.

Reproduce the single-version read:

```console
$ curl -s https://pypi.org/pypi/gradio/6.30.0/json | python3 -c "
import json,sys
d=json.load(sys.stdin)
print([r for r in (d['info']['requires_dist'] or []) if 'pillow' in r.lower()])"
['pillow<13.0,>=8.0']
```

## 4. Forward-looking signal: gradio's unreleased `main`

`gradio`'s `pyproject.toml` declares its dependencies dynamically from
`requirements.txt`. On `main`:

```console
$ curl -s https://raw.githubusercontent.com/gradio-app/gradio/main/requirements.txt | grep -in "pillow"
16:pillow>=8.0,<13.0
```

So even the not-yet-released source of truth still caps pillow below 13.0.
Waiting for "the next gradio bump" is therefore not a near-term expectation:
the cap is still in the upstream working tree.

## 5. Resolver evidence

pip's own solver, run in a scratch virtualenv inside the worktree:

```console
$ python3 -m venv .scratch/venv
$ .scratch/venv/bin/python -m pip install --dry-run "gradio==6.30.0" "pillow>=13"
...
INFO: pip is looking at multiple versions of gradio to determine which version is compatible with other requirements. This could take a while.
ERROR: Cannot install gradio==6.30.0 and pillow>=13 because these package versions have conflicting dependencies.

The conflict is caused by:
    The user requested pillow>=13
    gradio 6.30.0 depends on pillow<13.0 and >=8.0

Additionally, some packages in these conflicts have no matching distributions available for your environment:
    pillow

To fix this you could try to:
1. loosen the range of package versions you've specified
2. remove package versions to allow pip to attempt to solve the dependency conflict

ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/#dealing-with-dependency-conflicts
```

For reference, the newest pillow on PyPI is 13.0.0
(`https://pypi.org/pypi/pillow/json` → `info.version` = `13.0.0`), so the
advisory fix version *does* exist; it is simply unreachable through gradio.

### 5.1 The resolver backtracks over *every* gradio >= 4.0

With the repository's own floor (`gradio>=4.0`) the solver has no escape:

```console
$ .scratch/venv/bin/python -m pip install --dry-run "gradio>=4.0" "pillow>=13"
...
INFO: pip is looking at multiple versions of gradio to determine which version is compatible with other requirements. This could take a while.
...
    gradio 4.0.2 depends on pillow<11.0 and >=8.0
    gradio 4.0.1 depends on pillow<11.0 and >=8.0
    gradio 4.0.0 depends on pillow<11.0 and >=8.0

Additionally, some packages in these conflicts have no matching distributions available for your environment:
    pillow
...
ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/#dealing-with-dependency-conflicts
```

The conflict list enumerates **183 gradio versions** (`grep -c "depends on
pillow"` on the resolver output) — every published `gradio>=4.0` release was
tried and every one of them caps pillow below 13.0.

For completeness, the same command *without* the floor (`"gradio"`
instead of `"gradio>=4.0"`) does resolve — but only by backtracking all the
way to the 2023 release:

```console
$ .scratch/venv/bin/python -m pip install --dry-run "gradio" "pillow>=13"
...
Would install ... gradio-3.36.1 ... pillow-13.0.0 ...
```

That is the `gradio 3.36.1` case from §3 (unbounded `pillow` requirement),
which violates this repository's `gradio>=4.0` floor and would downgrade the
UI stack by three major lines. It is not a viable Branch A.

### 5.2 What the resolver actually picks today

Installing the newest gradio into the scratch venv resolves pillow to the
last 12.x release, not 13.0.0:

```console
$ .scratch/venv/bin/python -m pip install gradio==6.30.0
$ .scratch/venv/bin/python -m pip list | grep -iE "gradio|pillow"
gradio          6.30.0
gradio_client   2.7.2
hf-gradio       0.4.1
pillow          12.3.0
```

### 5.3 pip-audit reproduces the ignore list exactly

`pip-audit` runs on this VM, and against the gradio 6.30.0 environment it
reports **37** known vulnerabilities, all of them `pillow 12.3.0`, all fixed in
`13.0.0` — which is exactly the set of 37 `--ignore-vuln` lines currently
pinned in `.github/workflows/ci.yml`:

```console
$ .scratch/venv/bin/python -m pip_audit --strict
Found 37 known vulnerabilities in 1 package
Name   Version ID                  Fix Versions
------ ------- ------------------- ------------
pillow 12.3.0  CVE-2026-112895     13.0.0
pillow 12.3.0  CVE-2026-112400     13.0.0
pillow 12.3.0  CVE-2026-112397     13.0.0
...
pillow 12.3.0  GHSA-78g4-xqwv-q3g8 13.0.0
pillow 12.3.0  CVE-2026-114501     13.0.0
pillow 12.3.0  CVE-2026-112398     13.0.0
$ echo $?
1
```

No other package in the resolved environment is flagged. The advisories are
real and the fix version (pillow 13.0.0) exists — the only reason the audit
cannot pass is gradio's cap, which is precisely the reason recorded in the
ignore block.

## 6. Conclusion

- The blocker is gradio's own metadata, not pillow's release cadence and not
  anything in this repository's lockfile.
- Because the cap stands in every published gradio release that satisfies this
  repository's `gradio>=4.0` floor (all 183 of them, per pip's own resolver)
  and in upstream `main`, the four pip-audit `--ignore-vuln` rotations in
  `.github/workflows/ci.yml` **cannot** be retired by a dependency bump today.
  Bumping `ui = ["gradio>=4.0"]` to any current release changes nothing: the
  cap would still apply.
- The only gradio that would tolerate pillow 13.0.0 is 3.36.1 (July 2023,
  unbounded `pillow` requirement), which is below the repository's floor and
  three major lines behind; it is not a "current release" and adopting it is
  not a viable fix.
- Per the issue's Branch B, **no dependency or workflow file was modified**.
  The `git diff` of this branch contains only this document and its one line
  in the `docs/README.md` index.

## 7. Proposal: a scheduled cap-rotation check

The four rotations happened because each advisory drop was discovered by a PR
CI run, and the fix ("ignore more IDs") had to be hand-written under time
pressure. The check itself is trivial and mechanical, so it should be
scheduled rather than discovered.

### 7.1 What it would run

A new workflow, `.github/workflows/gradio-pillow-cap.yml`, on a weekly
schedule (plus `workflow_dispatch`):

```yaml
name: gradio pillow cap check
on:
  schedule:
    - cron: "17 6 * * 1"   # Mondays 06:17 UTC
  workflow_dispatch:
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
      - name: Query PyPI for the newest gradio and its pillow constraint
        run: |
          python - <<'PY'
          import json, urllib.request
          meta = json.load(urllib.request.urlopen("https://pypi.org/pypi/gradio/json"))
          latest = meta["info"]["version"]
          pins = [r for r in (meta["info"]["requires_dist"] or []) if r.lower().startswith("pillow")]
          print("latest gradio:", latest)
          print("pillow constraint:", pins)
          open("gradio-pillow-cap.txt", "w").write(f"{latest}\t{';'.join(pins)}\n")
          PY
      - name: Fail (and report) when the cap is lifted
        run: |
          # exit 1 when the newest gradio no longer caps pillow below 13.0
          ! grep -qE 'pillow[^,]*<13\.0' gradio-pillow-cap.txt
```

### 7.2 How often, and what it files or updates

- **Cadence**: weekly. The upstream cap has moved three times across recent
  major lines (`<11.0` → `<12.0` → `<13.0`), and upstream ships fast (6.0.0
  was uploaded 2025-11-21, 6.30.0 on 2026-10-08 — 30 minor releases in ~11
  months), so weekly catches a lift within days of it shipping, without adding
  noise.
- **On cap still in place** (the expected steady state): the job passes
  silently — no issue, no PR, no CI signal. It is a monitor, not a gate.
- **On cap lifted** (gradio's `pillow` constraint admits 13.0.0): the job
  fails and opens (or updates) a single tracking issue, e.g.
  *"gradio no longer caps pillow < 13.0 — retire the pip-audit pillow
  ignores (#1274)"*, containing the exact constraint string it read from PyPI
  and the candidate gradio version to bump `ui` to. A follow-up PR then
  (a) bumps `ui = ["gradio>=4.0"]` to that version in `pyproject.toml`,
  (b) re-locks `uv.lock`, (c) deletes exactly the pillow `--ignore-vuln` lines
  whose reason was the gradio cap, and (d) leaves any ignore with a different
  reason untouched.
- **Idempotency**: the issue body is generated from the same script output, so
  a re-run updates one issue instead of opening duplicates. It should look for
  an existing open issue with the same title marker (e.g. a
  `cap-check` label) before creating one.
- **Why not Renovate**: Renovate already manages the `ui` extra
  (`renovate.json` is present) but it cannot reason about *transitive* caps,
  and it cannot retire pip-audit ignore lines. The scheduled check owns the
  "the cap moved" signal; Renovate owns the actual bump once it is allowed.
- **Local reproduction of the check** (same commands the workflow runs):

  ```console
  $ curl -s https://pypi.org/pypi/gradio/json | python3 -c "
import json,sys
m=json.load(sys.stdin)
print(m['info']['version'], [r for r in m['info']['requires_dist'] if r.lower().startswith('pillow')])"
  6.30.0 ['pillow<13.0,>=8.0']
  ```

  If that second field ever stops containing `<13.0`, the branch flips to
  Branch A of #1274 and the ignores can go.

## 8. What this investigation did *not* change

- `pyproject.toml` — unchanged (`ui = ["gradio>=4.0"]`).
- `.github/workflows/ci.yml` — unchanged; all 37 pillow `--ignore-vuln` pins
  and their rotation comments remain, because their stated reason (the gradio
  pillow cap, fix version pillow 13.0.0) still holds.
- `uv.lock` — unchanged.
