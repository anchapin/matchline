# Release notes and scope: v0.1.0

`pyproject.toml` carries `version = "0.1.0"`. This document states what a
v0.1.0 release contains, what it explicitly does not, and the cadence for
cutting releases.

## What v0.1.0 contains

- The full drawings pipeline: seed, drawing-set PDF (single or zip of
  PDFs, merged in filename order), and sheet-image inputs (images yield
  symbols and takeoff only, stated honestly in the UI and the CLI).
- The IFC frontend: Tier 0 geometry plus Tier 1 space attachment (host
  intervals, facade review, `adjacency_ambiguous` flags per #681), the
  IFC2X3 to IFC4 upgrade (#929), and IFC4 export of semi-exterior and
  vestibule walls (#747).
- The simplifier: area-budgeted greedy vertex removal with per-ring
  budgets and shared-budget splitting (#970, #983), exported for all
  footprint rings including holes and courtyards (#985), with
  multi-storey per-space wall splitting (#986/#989).
- The validation battery: conservation and closure checks, the
  review-queue gate (unacknowledged items block export), the review
  workflow (`matchline review --confirm/--reject/--apply`), and
  auto-triage with confidence thresholds.
- The benchmark harness (#748) with per-building counts for the four
  adopted buildings (bsi-clinic, esplanades, duplex, gni-18) including
  the surface-count columns (#972), plus the fast/big split and the
  license ledger (#751).
- matchline-ui (#982/#987/#988): the web app over the same pipeline
  stages with PDF, zip, IFC, and image inputs.
- Dependency hygiene: the Renovate schedule, the CPython 3.15 pin with
  its upstream issue, and the dependency dashboard.

## What v0.1.0 explicitly does not contain

- MEP schedule parsing and system-type classification (#974).
- Explicit area definitions: GSF, BOMA rentable, program/assignable (#975).
- Vector-native geometry parsing: R1 stays a time-boxed spike (#973).
- The professional trust package: E&O disclaimer, stampable sign-off
  report, data-contribution agreement, CLA (#976).
- Product backlog UX: revision tracking, split-sheet dedup, legend
  learning, correction history, progressive mode, export package (#977).
- Completed real-building benchmark results: #748 waits on licensed
  building data beyond the adopted four.
- Revit/AutoCAD plugins, SSO/SOC 2 story, SaaS, and anything from the
  Strategy list: parked, not started.

## Release cadence

- Releases are cut by hand from `develop` when CI is green and the
  release notes match reality; nothing cuts itself.
- The version stays 0.1.0 until the real-building benchmark completes
  on licensed data; that lands as 0.2.0 together with #974 or #975 if
  either has shipped.
- Between releases, merged work is visible on the project board and in
  the merged-PR log; there is no intermediate changelog to maintain.
- A regression on a released surface justifies an immediate patch
  release (0.1.x) without waiting for a cadence tick; the bar is "CI
  green plus the duck review" as always.

## Dependency and sequencing map

Which roadmap items gate which (see ROADMAP.md "Non-goals and parked
ideas" for what is explicitly out):

- **v0.1.0** gates on this document and the risk register existing, and
  on green CI. It does NOT gate on #973-#977: those land after.
- **#748 completion** gates on licensed building data (risk 2 in the
  register); it gates the go-to-market "real-building benchmark" item
  and the 0.2.0 version bump.
- **#974 (MEP)** depends on the HVAC decisions already made (#746 AHU
  as AirLoop, zone inputs before sizing) and is independent of #975.
- **#975 (GSF/BOMA)** builds on the footprint-rings and simplifier work
  (#985, #983); nothing else depends on it.
- **#977 (UX backlog)**: split-sheet dedup depends on multi-ring
  footprints (#985); progressive mode depends on matchline-ui
  (#987/#988); the rest is independent.
- **#976 (trust package)** gates the first public-facing release claims
  more than the code; it needs the docs truth from #979 (done).
- **#973 (R1 vector spike)** informs the long-term accuracy plan and
  R2 (detection-provider interface); it gates nothing shipped.
- **The CPython 3.15 pin** lifts only when NREL/OpenStudio ships cp315
  wheels (#5668); Renovate reopens the question every Monday window.


## v0.2.0 (2026-10-16)

### What v0.2.0 contains

Everything in the `[0.2.0]` changelog section: the from-scratch detector
track and its eval tooling (#743), HVAC terminal detection and the duct
skeleton trace (#744, #745), mechanical schedules, zone airflow and air
loops on export (#746), the construction library and semi-exterior
envelope classes (#747, #900), storefront and glazing reads (#793),
explicit area definitions (#975), the 3D review viewer and queue
(#996, #1001-#1004), correction history and revision tracking with
takeoff diffs (#993, #997), progressive takeoff (#998), the export
package (#999), the telemetry/feedback spec and its wiring (#1003),
match lines and split-sheet dedup (#994), the benchmark on 12 licensed
buildings, the wheel packaging fixes that make `pip install matchline` a
real install, contributor docs, the generated repo map, and the
tag-driven PyPI release workflow (#1099).

### What v0.2.0 explicitly does not contain

- Completed real-building benchmark results: #748 waits on licensed
  building data beyond the adopted twelve.
- The #1093 top-level deep clean, the #1094 research sweep, and the
  #1095 training/research improvements: they target v0.3.0.
- Revit/AutoCAD plugins, SSO/SOC 2, SaaS: still parked.

### Release cadence (updated for 0.2.0)

- Releases are tag-driven now: `release.yml` builds, smoke-tests and
  publishes to PyPI when a `vX.Y.Z` tag matching pyproject's version is
  pushed; the tag push is the approval (CONTRIBUTING Never List).
- The earlier note that the version stays 0.1.0 until the real-building
  benchmark completes is superseded: 0.2.0 ships on 2026-10-16 with
  #974 and #975 shipped, which is what that bullet's own rule allowed.
- v0.3.0 targets the deep clean (#1093), the research sweep (#1094) and
  the training/research improvements (#1095).
