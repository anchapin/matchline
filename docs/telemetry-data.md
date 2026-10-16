# What leaves your machine: matchline telemetry data page

Spec version `matchline.telemetry-spec/1` (2026-10-15). Later versions bump
this line and date; a run is governed by the version in force when it was
accepted. See also the
[data-contribution agreement](data-contribution-agreement.md).

## The short version

Matchline can ask for anonymous, aggregated feedback about how a run went.
Telemetry is **opt-in**: nothing is emitted without your consent. If you run
with **local-only mode**, nothing is emitted at all, consent or not.

## What leaves the machine

Exactly five groups of aggregate numbers, and nothing else:

1. **Per-class detection confidence distributions** - how confident the
   detector was, binned, per symbol class (door, window, ...).
2. **Correction counts by type** - how many human corrections of each kind
   a run needed (no correction content).
3. **Validation-check failure frequencies** - which built-in checks failed
   how often, by check id (no check detail).
4. **Binned aggregate areas and counts** - floor areas, wall counts, space
   counts reported in coarse bins.
5. **Stage outcomes and timings** - whether each pipeline stage succeeded or
   failed, and how long it took.

## What never leaves the machine

- No geometry: no coordinates, polygons, vertices, or bounding boxes.
- No labels or names: no room, space, zone, level, or building names.
- No sheet images, drawings, or image data of any kind.
- No identifying information: no building, firm, project, file, path, or
  address data.
- No payloads beyond 128-character strings: payloads carry counts and short
  bin labels only.

The emitter enforces this with an allowlist (`telemetry_spec.py`): a field
that is not on the spec's allowlist fails validation at build time rather
than shipping to users, and a test proves emitted payloads contain no
building-specific data (#1006).

## How the emitter enforces this

The emitter (`telemetry_emitter.py`, issue #1006) is the only path from
run data to a payload. It offers one record method per metric group and
nothing else: there is no API for attaching another top-level field, so
a payload is allowlisted by construction. Bucket keys (a class name, a
correction type, a check name, a bin label) are checked against the
exclusion list at record time, and the finished payload is validated
against the spec before `build_payload` returns it. Anything that fails
raises `EmitterBuildError` at build time, never in front of users, and
nothing non-conforming ships. Local-only mode disables emission
entirely: the emitter collects nothing and `build_payload` returns
None, consent or not.

## How consent is granted

A run emits only when you grant consent for that run, two ways: the
`--telemetry` flag on `matchline run`, or a `telemetry_consent: true` key in
the run config. `telemetry_local_only: true` in the config wins over either:
nothing is collected, consent or not.

The emitted payload is written to `telemetry.json` in the run's output
directory; the pipeline transmits nothing anywhere. Stage outcomes and
timings, and validation-check failure counts, are the groups wired so far;
a failed payload build warns and is skipped, so telemetry never changes a
run's outcome.

## Feedback capture is separate from telemetry

Structured feedback (issue #1005, `feedback_capture.py`) is captured at
the moment of friction: a detector override, an abandoned review item, a
failed parse, plus an open-ended channel for bugs, feature requests, and
places where the conversation does not work well.

It is a local, append-only JSONL log someone on the team opens and
reads; nothing here sends anything anywhere. Context tokens are checked
against the same exclusion list as telemetry, so room names, paths, and
sheet references are refused at capture time, and a failed parse
records the stage and a coarse error kind, never the raw exception text.
Open-ended responses keep the user's own words, but feedback is not one
of the telemetry emitter's five metric groups, so it never flows into a
telemetry payload. `summarize()` turns a log into counts by kind, the
most common friction points, and theme words from open responses, for
roadmap re-prioritization.
