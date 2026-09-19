# Facade area takeoffs (CMP Facade prototype)

## What this is

A prototype of the core requirement — **wall, window, and door area from
elevation drawings** — run against the closest public stand-in for
dimensioned CAD elevations: the CMP Facade dataset (606 rectified facade
photos with per-pixel semantic masks, `~/workspace/datasets/cmp-facade/`).

Code: `facade_takeoff.py` · demo/stats: `run_facade_takeoff.py` ·
priors: `facade_priors.json` · tests: `tests/test_facade_takeoff.py`.

## The scale problem (and how it's handled)

Rectified photos are **scale-free**: there is no dimension on the drawing,
so absolute m² is unknowable from the image alone. The module is honest
about this boundary:

- **Native outputs are fractions and ratios**: WWR, door fraction, opaque
  fraction — all over the *wall-plane* pixel set. These need no scale.
- **Absolute m² only when the caller supplies a real dimension**
  (`width_m`, optionally `height_m`) — mirroring the real workflow of
  *rectified elevation + one known dimension / scale bar*. Scale is derived
  from the facade-region bounding-box width mapped to the supplied width.
- If both width and height are supplied, height is used as a **consistency
  check, not averaged in**: disagreement > 5% warns (photo not perfectly
  rectified, or the bbox caught a projecting balcony).
- `FacadeTakeoff.scale_supplied` / `to_envelope_dict()["scale_supplied"]`
  make it impossible for a downstream consumer to mistake fractions for m².

## Class mapping decisions

| CMP class | Bucket | Rationale |
|---|---|---|
| 2 facade | opaque wall | the wall itself |
| 3 window | glazing | |
| 8 blind | **glazing** | verified visually: closed roller shutters *over window openings*. The rough opening is still a window; a downstream energy model may want the shuttered sub-fraction (reported as `frac_blind_of_glazing`) to derate solar aperture |
| 12 shop | **glazing** | storefront glazing at ground level; transparent aperture in the wall plane |
| 4 door | door (separate) | kept apart from WWR per the takeoff requirement |

Denominator: **wall plane** = {facade, window, door, blind, shop}. Trim
(cornice, sill, deco, molding, pillar) and projections (balcony) are
excluded — the same way a quantity surveyor excludes them from gross wall
area. By construction `opaque + glazing + door = 1.0` over the wall plane,
which gives the validation battery a free closure invariant.

Sky/background (class 1) is excluded from all fractions: metrics are
computed over the facade, not the photo.

## Cross-check: XML boxes vs pixel mask

Each facade also ships coarse per-region XML rectangles. Comparison over
all 606 facades (glazing combined):

| metric | mean | median |
|---|---|---|
| area ratio (XML / mask) | 1.07 | 1.03 |
| strict IoU | 0.14 | 0.12 |
| slop-tolerant recall | 0.49 | 0.49 |
| slop-tolerant precision | 0.47 | 0.46 |
| **instance counts** (20,045 XML boxes vs 19,936 mask components) | **ratio 1.01** | — |

Reading: the XML boxes are **loosely drawn** (verified on overlays — boxes
are offset from the true window pixels by varying amounts per image), so
they are *not* a localization reference. But their **total areas agree with
the mask to ~3–7%** and their **instance counts agree to 1%**: the XML is a
valid *counting and area* cross-check, which is exactly the granularity a
takeoff needs. Per-class area ratios: window 1.29/1.07 (mean/median), door
1.47/1.00, blind 1.10/1.02, shop 1.00/1.00 — XML boxes run slightly large,
as expected from coarse annotation.

## Priors (606 facades; fractions over wall plane)

| metric | mean | median | p10 | p90 | range |
|---|---|---|---|---|---|
| WWR | 0.308 | 0.289 | 0.186 | 0.446 | 0.052–1.000 |
| door fraction | 0.022 | 0.015 | 0.000 | 0.052 | 0.000–0.330 |
| opaque fraction | 0.670 | 0.686 | 0.527 | 0.798 | 0.000–0.927 |
| blind ÷ glazing | 0.105 | 0.000 | 0.000 | 0.378 | 0–0.814 |
| shop ÷ glazing | 0.140 | 0.037 | 0.000 | 0.408 | 0–1.000 |

These are candidate **plausibility priors for the validation layer's sanity
guards** (e.g. flag a facade takeoff with WWR > 0.6 or door fraction > 0.1
for review — both are above p90 here). Edge cases exist: one facade is
~100% storefront glazing (WWR = 1.0); ground-floor retail breaks the
residential pattern, which is why these should be *warn*, not *error*,
thresholds.

## Pipeline interface

`FacadeTakeoff.to_envelope_dict()` is the handoff shape for the
BuildingModel envelope layer — deliberately a plain dict, not an edit to
`building_model.py`. The envelope consumer attaches it to wall segments
(facade → wall runs via `registration.py`). Provenance on every takeoff
records the dataset, the **CC BY-SA share-alike license**, the method, and
whether a scale was supplied.

## Limitations

1. **Photos, not line drawings.** Real CAD elevations have crisp vector
   edges, no perspective residue, no occlusions (cars, signs, trees), no
   shadows. A detector trained/evaluated here transfers only partially.
2. **European stone/plaster facades**, not commercial curtain wall. The WWR
   priors above describe punched-opening masonry, not unitized glazing —
   do not apply them blindly to office towers.
3. **Rectified-photo geometry ≠ CAD elevation.** Rectification leaves
   residual distortion; the width/height scale-consistency check exists
   precisely because of this.
4. **Single facade per image, no storey breakdown.** No floor-to-floor
   heights, so no per-level envelope areas — that needs the CAD workflow
   (`elevation_windows.py` gives per-opening sill/head heights instead).
5. **Blinds counted as glazing** is correct for *opening area* but an energy
   model needs the shuttered fraction separated (reported) for SHGC
   treatment.
6. **CC BY-SA share-alike**: statistics and takeoffs derived from CMP
   inherit share-alike obligations. Fine for research; flag before any
   commercial use of derived numbers.

## What changes on real CAD elevations

- Scale comes from the drawing (title-block scale, a dimension string, or
  DXF units) — the `width_m` parameter becomes the parsed scale, and the
  consistency check compares against a second known dimension.
- Openings become *instances with positions* (`elevation_windows.py`),
  not just pixel fractions — per-room window assignment and daylighting
  zones follow.
- The wall-plane denominator can exclude spandrel vs vision glazing if the
  CAD layers distinguish them (CMP cannot).
- Priors should be recomputed on commercial stock before use as sanity
  guards; the masonry numbers above are a starting point, not a standard.
