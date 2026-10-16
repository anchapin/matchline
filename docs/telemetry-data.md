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
