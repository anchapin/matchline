# Roof simplification wiring: decision-ready evaluation (Refs #1275)

Issue [#1275](https://github.com/anchapin/matchline/issues/1275) asks:
*`simplify_roof` has no production caller; decide the wiring shape.* **The
decision is Alex's.** This document is the decision-ready evaluation only: it
states the verified current state, costs the candidate wiring shapes against
the real code, and names a recommendation with a follow-up issue text. **No
production code is changed by this document.**

## How to read this document

- **Verified by reading code in this repository.** Every statement about
  Matchline carries a `file:line` citation that was read, not inferred.
- **The grep evidence is reproduced verbatim** in
  [§2.3](#23-every-reference-in-the-repository) so the "no production caller"
  claim can be re-checked in one command.
- **Not verified here**: this document contains no new measurements. The only
  performance figures are the ones already recorded in-repo for issue #1267
  (`DATASETS.md`, `docs/benchmark_gni26.md`), quoted with their location.
- Where the evidence does not settle a question, the question is listed as an
  open question in [§4](#4-risks-and-open-questions) rather than guessed at.

---

## 1. Summary and recommendation

**Verdict: Option C — wire the roof simplifier into the production path
behind an opt-in flag now, with a defined promotion path to Option A (default
on) once an error-level gate exists.** Option B (leave it as a bench-only
utility) is the status quo and is *not* recommended, because it leaves a
public module, a model field and a conservation check unreachable from
`matchline run`.

Three facts drive the verdict:

1. **The mechanism is finished; only the call site is missing.**
   `apply_roof_simplification` (`geometry/roof_simplify.py:523`), the
   `source_roof_planes` field (`building_model.py:988`), the
   `roof_simplify:merge` provenance (`geometry/roof_simplify.py:472-483`) and
   the `roof_solar_aperture` check (`validate/conservation.py:1200`) all exist
   and are all tested (`tests/test_roof_simplify.py`,
   `tests/test_roof_checks.py:11`). The only non-test caller in the repo is
   the benchmark harness (`scripts/benchmark.py:429-433`).
2. **The guard is warn-only.** `roof_solar_aperture` "warns" rather than
   blocking (`validate/conservation.py:1206-1207`), while the *wall* simplifier
   — which is already in production, at `run_pipeline.py:717` — has a hard
   export gate at 3 % (`bem/bem_geometry.py:330-336`). Switching roof
   simplification on by default would change the exported roof surfaces
   (`bem/bem_export.py:364`, `bem/bem_ifc4.py:531`, `ifc/ifc_export.py:267`)
   with no error-level gate in front of them.
3. **The cost is bounded but non-zero.** #1267 made the simplifier fast
   enough for the benchmark budget (385 → 118 facets, 65 s wall, 320 MiB peak
   RSS — `docs/benchmark_gni26.md:56-60`, `DATASETS.md:478-484`), and the
   unit suite keeps a scaling guard (`tests/test_roof_simplify.py:215-232`).
   That is acceptable per run, but it is not free and it is only worth paying
   when the operator asks for it.

So: put it behind a flag (Option C), keep the defaults the function already
has (2 % area, 2 % aperture, 5°, 5 %), surface the report in a stage
artifact, and let the existing `roof_solar_aperture` check start firing. Then
promote to default-on once that check is promoted to error level.

Details, per-option costing and the follow-up issue text are in
[§3](#3-the-decision), [§4](#4-risks-and-open-questions) and
[§5](#5-recommendation).

---

## 2. Current state (verified facts)

### 2.1 The function itself

`geometry/roof_simplify.py` is the roof mode of geometry simplification
(roadmap item 2, #616). The module docstring states the contract
(`geometry/roof_simplify.py:1-13`): *"`simplify_roof` merges adjacent roof
facets greedily, cheapest first, under two hard budgets measured against the
ORIGINAL roof"* — geometric (true sloped) area and solar aperture
(`solar_aperture.py:92` `solar_aperture`, `:45` `sun_vectors`, #615).

**`simplify_roof`** — `geometry/roof_simplify.py:310`:

```python
def simplify_roof(
    planes,
    latitude_deg: float,
    angle_tol_deg: float = ANGLE_TOL_DEG,        # 5.0
    small_facet_frac: float = SMALL_FACET_FRAC,  # 0.05
    area_tol: float = AREA_TOL,                  # 0.02
    aperture_tol: float = APERTURE_TOL,          # 0.02
) -> RoofSimplifyResult:
```

Defaults are module constants, all at `geometry/roof_simplify.py:80-86`:

| Constant | Value | Line | Meaning |
|---|---|---|---|
| `ANGLE_TOL_DEG` | `5.0` | `:80` | near-coplanar normals |
| `SMALL_FACET_FRAC` | `0.05` | `:81` | facets this small (of the roof) may be absorbed |
| `AREA_TOL` | `0.02` | `:82` | \|area change\| / original area |
| `APERTURE_TOL` | `0.02` | `:83` | \|solar aperture change\| / original aperture |
| `MIN_SHARED_EDGE_M` | `0.1` | `:84` | minimum shared plan edge |
| `EDGE_Z_TOL_M` | `0.05` | `:85` | heights on a shared edge must agree this closely |
| `METHOD` | `"greedy_min_aperture_change"` | `:86` | report `method` string |

Other verified properties of the function:

- Candidate pair: same level, a shared plan edge of at least `MIN_SHARED_EDGE_M`
  whose heights agree within `EDGE_Z_TOL_M` at **both endpoints and the
  midpoint** (`geometry/roof_simplify.py:197-232`, the endpoint probing is
  #1271), and either normals within `angle_tol_deg` or the smaller facet at
  most `small_facet_frac` of the roof area
  (`geometry/roof_simplify.py:356-361`).
- Merges are taken cheapest-first and **batched** (several disjoint merges per
  pass): sort key `(abs(aperture change), abs(area change), (id_a, id_b))`,
  rounded to 12 decimals (`geometry/roof_simplify.py:397`), and a plane
  takes part in at most one merge per pass (`geometry/roof_simplify.py:404-406`).
- Budget refusals are re-offered on later passes and reported once
  (`geometry/roof_simplify.py:417-432`); build refusals (union not one
  polygon, merge would leave a hole, merged facet rejected) are remembered by
  plane **identity**, not id (#1269, `geometry/roof_simplify.py:332-335`,
  `:368-371`).
- The input list is never mutated; the result holds **new** planes, and merged
  planes carry `Provenance(method="roof_simplify:merge", note="merged from …")`
  (`geometry/roof_simplify.py:472-483`).
- The result object `RoofSimplifyResult` (`geometry/roof_simplify.py:90-105`)
  carries `planes`, `area_delta_pct`, `aperture_delta_pct`, `area_tol`,
  `aperture_tol`, `method`, `merges`, `skipped`, `n_before`, `n_after`, `stats`.

**`roof_simplify_report(res)`** — `geometry/roof_simplify.py:498`: the
"plain-dict report, the roof counterpart of `simplify_report`". Its keys
(`geometry/roof_simplify.py:499-521`): `method`, `n_planes_before`,
`n_planes_after`, `area_delta_pct`, `aperture_delta_pct`, `area_tol_pct`,
`aperture_tol_pct`, `merges`, `skipped`, `stats`, and `planes` (id, tilt,
azimuth, area per surviving plane).

**`apply_roof_simplification(model, latitude_deg=None, **kw)`** —
`geometry/roof_simplify.py:523`: the model-level wrapper (#617). It takes the
latitude from the caller or else `model.site_latitude_deg` and **raises**
`ValueError` when neither is available (`geometry/roof_simplify.py:532-534`);
it copies the pre-simplification planes to `model.source_roof_planes` **only
the first time**, so repeated runs still compare against the true source, and
replaces `model.roof_planes` with the simplified set
(`geometry/roof_simplify.py:536-538`).

### 2.2 Every caller and reference

**Production code (`*.py`, excluding tests) — the grep from the brief:**

```
$ grep -rn "simplify_roof" --include="*.py" . | grep -v test
./geometry/roof_simplify.py:3:``simplify_roof`` merges adjacent roof facets greedily, cheapest first, under
./geometry/roof_simplify.py:310:def simplify_roof(
./geometry/roof_simplify.py:535:    res = simplify_roof(model.roof_planes, lat, **kw)
```

That is the **whole** non-test hit list: two of the three hits are the
definition and its docstring, and the third is the call inside
`apply_roof_simplification` in the same file. **No production module calls
`simplify_roof` or `apply_roof_simplification`.**

**Callers of `apply_roof_simplification` (the model-level entry point):**

```
$ grep -rn "apply_roof_simplification" --include="*.py" .
./scripts/benchmark.py:429:        from geometry.roof_simplify import apply_roof_simplification
./scripts/benchmark.py:431:        rres = apply_roof_simplification(model)
./tests/test_roof_checks.py:11:from geometry.roof_simplify import apply_roof_simplification
./tests/test_roof_checks.py:140:    res = apply_roof_simplification(m)
./tests/test_roof_checks.py:172:    apply_roof_simplification(m)
./tests/test_roof_checks.py:181:            apply_roof_simplification(m)
```

So the **only** non-test caller is the benchmark harness
(`scripts/benchmark.py:429-433`), which is exactly why the GNI-2026 scoreboard
can report a `roof_in` / `roof_out` column (`scripts/benchmark.py:105-106`,
`:314-315`) while the production pipeline never simplifies a roof.

**Tests:**

- `tests/test_roof_simplify.py` — the unit suite for `simplify_roof` and
  `roof_simplify_report` (imports at `tests/test_roof_simplify.py:13`). It pins
  the budgets (e.g. `assert abs(res.area_delta_pct) <= 2 and
  abs(res.aperture_delta_pct) <= 2` at `tests/test_roof_simplify.py:73` and
  `:84`), the refusal behaviour (`tests/test_roof_simplify.py:96-108`,
  `:119-123`), order-independence of the report
  (`tests/test_roof_simplify.py:134-141`, `:203-211`), the report keys
  (`tests/test_roof_simplify.py:145-151`) and the scaling limits of #1265
  (`tests/test_roof_simplify.py:215-232`: 225 planes collapse to 1 in under
  60 s, `pair_scans < 12 * len(planes)`, `plan_computations <= 2 * len(planes)`).
- `tests/test_roof_checks.py` — the model-level path: it imports
  `apply_roof_simplification` (`tests/test_roof_checks.py:11`) and calls it on
  a model (`:140`, `:172`, `:181`), i.e. the `source_roof_planes` /
  `roof_solar_aperture` contract is already exercised by tests.

**Documentation:**

- `docs/building_model.md:205-226` — the reference description of the roof
  mode (candidates, budgets, report, "the input list is never changed").
- `docs/validation.md:145-156` — the `roof_solar_aperture` check, described
  as warn-only, and the `source_roof_planes` contract it depends on.
- `docs/api.md:86` — `roof_simplify` is listed among the public modules.
- `docs/repo-map.md:37`, `:246-248` — the generated module graph lists
  `geometry_roof_simplify` with edges to `building_model`,
  `geometry_roof_geometry` and `solar_aperture`.
- `docs/geometry_simplify.md` — the **wall/geometry** simplifier's doc, the
  contrast case (see §2.4).

**Benchmarks from #1267 (in-repo):**

- `DATASETS.md:478-484` — `gni26-p8-arc` was re-scored on 2026-10-24 with
  roof simplification enabled "now that #1267 made `simplify_roof` fast
  (**65 s wall, 320 MiB peak RSS**)", with the row carrying the measured roof
  facets **385 / 118** (in / out).
- `DATASETS.md:250` — same figures, shorter form.
- `docs/benchmark_gni26.md:56-60` — the same re-score, and the history: the
  2026-10-23 run scored the row with roof simplification **skipped** because
  the greedy one-merge-per-pass implementation took ~15 s per pass and was
  still at 335 planes after 50 passes (~12 min) — tracked as #1265.

### 2.3 Every reference in the repository

`grep -rn "simplify_roof" --include="*.py" . | grep -v test` — quoted in
§2.2 above. The wider sweep `grep -rn "roof_simplify" --include="*.md" .`
returns only: `docs/api.md:86`, `docs/building_model.md:205`, `:224`, `:225`,
`docs/repo-map.md:37`, `:246`, `:247`, `:248`, `docs/validation.md:147`. No
ADR, plan or design document records a decision to leave the roof simplifier
unwired.

### 2.4 The contrast case: is the wall/geometry simplifier wired in?

**Yes.** The wall/geometry simplifier *is* in the production path, and the
roof one is not — the same asymmetry is the substance of #1275.

- Production stage: `run_pipeline.py:708-747` `_stage_3_simplify(...)` calls
  `simplify_footprint(parts, tol=simplify_tol, wall_height=wall_height)`
  (`run_pipeline.py:717`) and writes `stage_03_simplified.json`
  (`run_pipeline.py:747`). It is invoked from the stage chain at
  `run_pipeline.py:1000`.
- The stage is documented at `docs/pipeline.md:14` and `docs/pipeline.md:27`.
- The tolerance is user-facing: `--simplify-tol` (default `0.02`,
  `cli.py:725`) and the YAML config key `simplify_tolerance`
  (`cli.py:84`, consumed at `run_pipeline.py:952-953`).
- Downstream, the BEM export refuses models simplified above 3 %:
  `MAX_SIMPLIFY_TOL = 3.0` (`bem/bem_geometry.py:330-336`).

So the walls path has: a production stage, a CLI knob, a config key, a written
report artifact, and a hard export gate. The roof path has none of these in
production.

### 2.5 The building import pipeline

The pipeline that turns imported geometry into the building model is the
**IFC path**; it is also the only path that produces roof planes at all.

1. `ifc/ifc_import.py:3814` `import_ifc(path, sheet_id=None, revision=1) ->
   BuildingModel` — the single IFC entry point.
2. Inside it, roof geometry is read at `ifc/ifc_import.py:4571-4573`:
   `from ifc.ifc_roof_planes import orient_skylights, read_roof_planes` then
   `read_roof_planes(model, f, level_by_storey, prov)`.
3. `ifc/ifc_roof_planes.py:207` `read_roof_planes(...)` fills
   `model.roof_planes` (`ifc/ifc_roof_planes.py:277`:
   `model.roof_planes.append(rp)`), building each facet with
   `geometry/roof_geometry.py:103` `roof_plane(...)`, which computes tilt,
   azimuth and area from the corners (`geometry/roof_geometry.py:110-118`).
4. The pipeline runner reaches the importer at `run_pipeline.py:593-597`
   (`from ifc.ifc_import import import_ifc` at `:595`,
   `model = import_ifc(str(args.ifc_path))` at `:597`) inside Stage 1
   (`run_pipeline.py:960`); the model then flows through Stage 2
   (`run_pipeline.py:982-985`), Stage 3 simplify
   (`run_pipeline.py:997-1000`), Stage 4 validation and Stage 6 BEM export
   (`run_pipeline.py:1071-1074`).
5. Roof planes are consumed downstream by the BEM writers
   (`bem/bem_export.py:364` `roofs = list(getattr(model, "roof_planes", None) or [])`,
   `bem/bem_ifc4.py:531`, `ifc/ifc_export.py:267`) and by the conservation
   checks (`validate/conservation.py:419`, `:1114`, `:1217-1218`).

**The drawing-set / PDF path produces no roof planes**: `grep -rn "roof_planes"
--include="*.py"` finds no hit in `link.py`, `plan_walls.py`, `real_set.py` or
`ingest/`. Roof planes come from IFC only — so any wiring decision has a
bounded blast radius: the `--ifc` path of `matchline run`.

**Site latitude** (needed by the aperture budget) is filled by the importer
from `IfcSite.RefLatitude`: `ifc/ifc_import.py:3973`
(`model.site_latitude_deg = round(lat, 9)`); the model field is
`building_model.py:990`.

### 2.6 The report/provenance surfaces that already exist

These are the structures a wired-in simplification could ride on — they are
already built, they are just never fed in production:

| Surface | Where | What it carries |
|---|---|---|
| `model.source_roof_planes` | `building_model.py:988` | the pre-simplification roof, kept by `apply_roof_simplification` (`geometry/roof_simplify.py:536-537`) |
| `roof_solar_aperture` check | `validate/conservation.py:1200-1264` (registered in `validate.BATTERY`, `validate/__init__.py:161`) | compares current vs source aperture at `site_latitude_deg`; **warn-only**; payload carries `aperture_delta_pct`, `area_delta_pct`, `planes_before/after`, per-orientation before/after |
| `roof_plan_coverage` check | `validate/conservation.py:1096-1160` (registered in `validate.BATTERY`, `validate/__init__.py:160`) | error-level (`validate/conservation.py:1178-1189`): the top level's roof planes must cover the footprint once (1 % tolerance, `validate/conservation.py:1086`) |
| `Provenance(method="roof_simplify:merge", note="merged from …")` | `geometry/roof_simplify.py:472-483` | per merged facet, names its sources |
| `roof_simplify_report(res)` | `geometry/roof_simplify.py:498-521` | the full audit dict (merges, skipped refusals, budgets, stats) |
| `stage_03_simplified.json` | `run_pipeline.py:708-747`, `docs/pipeline.md:58` | the wall simplifier's stage artifact — the pattern a roof artifact would follow |

Note the check that is *missing*: there is **no error-level gate on roof
simplification**. `roof_solar_aperture` is warn-only by design
(`validate/conservation.py:1206-1207`: *"Warn-only, like the ASHRAE checks,
until the export writes sloped roofs"*), whereas the wall path has a hard
export gate at 3 % (`bem/bem_geometry.py:330-336`). This asymmetry is the
main risk argument in [§4](#4-risks-and-open-questions).

---

## 3. The decision

Three wiring shapes are costed below against the code read above.

### Option A — wire it into the import path

**Insertion point.** `run_pipeline.py:_stage_3_simplify` is the natural home:
it is already the "simplify geometry" stage, it already receives the model,
it already writes a stage artifact, and it runs before validation
(`run_pipeline.py:999-1000`) and before the BEM export (`run_pipeline.py:1074`).
Concretely: a `stage_3b` (or a `roof` sub-step of stage 3) that calls
`geometry.roof_simplify.apply_roof_simplification(model)` — the wrapper
already exists and already handles the latitude fallback
(`geometry/roof_simplify.py:523-543`) — and writes
`roof_simplify_report(res)` to a new artifact, e.g.
`stage_03b_roof_simplified.json`, mirroring `stage_03_simplified.json`.

Alternative insertion point: inside `ifc/ifc_import.py` right after
`read_roof_planes` (`ifc/ifc_import.py:4573`). **Not recommended**: it would
make the importer lossy, would remove the ability to compare against
`source_roof_planes` (the "before" state would be gone before anything could
record it), and would hide the change from the stage artifacts.

**Proposed default tolerances** — the function's own defaults, unchanged:
`area_tol=0.02`, `aperture_tol=0.02`, `angle_tol_deg=5.0`,
`small_facet_frac=0.05` (`geometry/roof_simplify.py:80-83`, `:313-316`).
These are the values the unit tests pin (`tests/test_roof_simplify.py:73`,
`:84`, `:149`) and the values the in-repo measurements were taken with
(`docs/building_model.md:205-221`: *"Hip ends stay separate by default:
folding an east end into the south side costs about 2.7 % area and 1.3 %
aperture at 40° N"* — i.e. the 2 % area budget is what keeps them apart).

**Where the report/provenance would surface.**

- The audit dict → a new stage artifact next to `stage_03_simplified.json`
  (`run_pipeline.py:747`), using `roof_simplify_report(res)`
  (`geometry/roof_simplify.py:498`), which already contains `merges`,
  `skipped`, both deltas and both budgets.
- The per-facet provenance → the model itself, via the `Provenance`
  `method="roof_simplify:merge"` already stamped on merged planes
  (`geometry/roof_simplify.py:472-483`).
- The delta guard → the existing `roof_solar_aperture` check
  (`validate/conservation.py:1200`), which starts firing as soon as
  `model.source_roof_planes` is non-empty.

**Cost.** Small: one call plus one artifact write in
`run_pipeline.py:_stage_3_simplify`; no changes to the importer, the model
schema (`building_model.py:985-990` already has both fields) or the exporters
(`bem/bem_export.py:364` reads `model.roof_planes` as-is).

**Blast radius.** The IFC path only (§2.5): drawing-set/PDF models have no
roof planes, so `apply_roof_simplification` would be a no-op there (empty
`roof_planes` → `n_before == 0`).

### Option B — keep it as a bench/utility API

**What that means concretely.**

- The only caller stays `scripts/benchmark.py:429-433` (the scoreboard's
  `roof_in`/`roof_out` columns, `scripts/benchmark.py:105-106`, `:314-315`),
  plus the unit tests.
- The API is documented but not wired: `docs/building_model.md:205-226` keeps
  the reference description, and a note is added there (or in
  `docs/geometry_simplify.md`) saying the roof simplifier is a bench utility
  and is **not** part of `matchline run`.
- Nothing else changes. `model.source_roof_planes` (`building_model.py:988`)
  stays empty in production, so the `roof_solar_aperture` check
  (`validate/conservation.py:1218-1220`) keeps taking its `skip` branch
  (*"roof not simplified (no source roof planes)"*) — it is currently
  unreachable in production.
- The `roof_plan_coverage` check (`validate/conservation.py:1096`) keeps
  validating the *unsimplified* roof, which is the correct behaviour for a
  no-op pipeline.

**Deprecation / experimental marker.** The repo has a deprecation idiom to
copy: `run_review.py:285-291` (`format_review_list` → `list_review_items`)
emits a `DeprecationWarning` and points at the replacement. There is **no**
"experimental" marker convention in the codebase — the closest thing is a
prose status line in the doc (as `docs/geometry_simplify.md` uses "Gaps /
v2" and "Limitations" sections). A doc-only "not wired into the pipeline"
note is therefore the honest, convention-matching way to record Option B.

**Cost.** Zero code, zero behaviour change. The price is that the module keeps
sitting between "shipped" and "shipped-but-off": it is public API
(`docs/api.md:86`), it has a full test suite, a model-level wrapper, a
provenance stamp and a check that exists only to police it — all unreachable
from `matchline run`.

### Option C — wire behind an opt-in flag (evidence-supported)

The evidence supports a third shape: the repo already has an opt-in pattern
for a stage-level behaviour change.

- CLI flag + env var precedent: `--auto-triage` /
  `ENABLE_AUTO_TRIAGE=1` (`cli.py:523`, `docs/cli.md:107`, `:296`,
  `docs/pipeline.md:16`, `:29`), the stage artifact is written only when the
  flag is on.
- Config-key precedent: the YAML config has an explicit allow-list of keys
  (`cli.py:83-90`: `simplify_tolerance`, `wall_height`, `review_confidence`,
  `datasets`, `telemetry_consent`, `telemetry_local_only`) and warns on
  unknown keys (`cli.py:91-93`). A `roof_simplification` key would have to be
  added to that allow-list, otherwise it is silently ignored with a warning.
- Tolerance-knob precedent: `--simplify-tol` (`cli.py:725`) plus the config
  key `simplify_tolerance` (`cli.py:84`, `run_pipeline.py:952`).

**Concrete shape.** A `--roof-simplify` flag (and/or a `roof_simplification`
config key) that turns the Option A call on for a run; default off. The
tolerances would default to the function's own defaults and could be exposed
as `--roof-simplify-tol` / `roof_simplification.area_tol` in the same style as
`--simplify-tol`.

**Cost.** Option A's cost plus one flag, one config key, one allow-list entry
and one more doc row (`docs/cli.md`, `docs/pipeline.md`). The cost is that
the default production output does **not** change — which is also the point.

---

## 4. Risks and open questions

### 4.1 Area/aperture preservation (the behavioural risk)

- The two budgets are hard: a merge that would move area by more than
  `area_tol` or aperture by more than `aperture_tol` is refused and logged
  (`geometry/roof_simplify.py:417-432`), and the budgets are checked against
  the *working set with the merges already accepted in this pass applied*
  (`geometry/roof_simplify.py:408-424`) — not against the original roof
  alone.
- Both budgets are measured against the **original** roof, not cumulatively
  per merge (`area0`/`ap0` computed once at `geometry/roof_simplify.py:329-330`).
- Pinned by tests: `tests/test_roof_simplify.py:73`, `:84`, `:107-108`
  (aperture refusal), `:119-123` (area refusal), `:222-223` and `:318`
  (batched merges stay inside both budgets), `:235-246` (batched merge
  reproduces the exact area: `res.planes[0].area_m2 == pytest.approx(18 * 18)`).
- **Positive note (coverage):** a merged plane's plan outline is the *union*
  of the two merged outlines (`geometry/roof_simplify.py:242`), so the
  error-level `roof_plan_coverage` check
  (`validate/conservation.py:1096`, `:1178-1189`) sees the same covered
  footprint after simplification as before — the merge does not open a gap.
- **Open question 1:** the budgets bound *total* area and *total* aperture, but
  the distribution across orientations is only checked after the fact by
  `roof_solar_aperture`, and that check is **warn-only**
  (`validate/conservation.py:1206-1207`, `:1256-1265`). There is no
  error-level gate equivalent to `MAX_SIMPLIFY_TOL = 3.0`
  (`bem/bem_geometry.py:330-336`) for the roof. If roof simplification ships
  by default, that asymmetry should be closed first.
- **Open question 2:** `EDGE_Z_TOL_M = 0.05` (5 cm) is the height-agreement
  guard on the shared edge (`geometry/roof_simplify.py:85`, `:227`). A merge
  is therefore allowed to average a 5 cm height mismatch into one plane. That
  is a deliberate tolerance, not an area-budget breach, but it is invisible
  in the area/aperture deltas.
- **Open question 3:** `small_facet_frac = 0.05` lets a facet up to 5 % of
  the roof area be absorbed by a host **regardless of angle**
  (`geometry/roof_simplify.py:357-360`). The doc's own worked example says
  folding a hip end into the south side costs ~2.7 % area and ~1.3 % aperture
  (`docs/building_model.md:219-221`) — refused by the 2 % area budget
  *by default*, but a user who raises `area_tol` opens it. Whether the
  defaults are the right production defaults is a judgement call for Alex.

### 4.2 Performance

- #1267 (plan cache + STRtree candidate pairs + batched disjoint merges,
  `geometry/roof_simplify.py:19-31`) made the simplifier fast enough for the
  benchmark budget: the `gni26-p8-arc` re-score ran with roof simplification
  **enabled** in **65 s wall / 320 MiB peak RSS**, with the roof going
  **385 → 118** facets (`DATASETS.md:478-484`, `docs/benchmark_gni26.md:56-60`).
- The unit suite keeps a scaling guard: 225 planes → 1 plane in under 60 s,
  with `pair_scans < 12 * len(planes)` and
  `plan_computations <= 2 * len(planes)` (`tests/test_roof_simplify.py:215-232`).
- **Open question 4:** the 65 s figure is for a 385-plane roof on the
  benchmark machine; the scoreboard run has a ~870 s budget
  (`docs/benchmark_gni26.md:58-60`). A production default-on wiring adds that
  cost to *every* IFC run, including small ones where the cost is negligible
  but the latency is not free. No per-model roof-size guard exists in the
  repo today.

### 4.3 Per-option risk summary

| Risk | A (wire in, default on) | B (bench utility) | C (opt-in flag) |
|---|---|---|---|
| Exported BEM surfaces change without an error-level guard | **High** — `roof_solar_aperture` is warn-only (`validate/conservation.py:1206`) | None | Low (opt-in per run) |
| Every IFC run pays the simplify cost | Yes (65 s on a 385-plane roof, `docs/benchmark_gni26.md:58`) | No | Only when asked |
| Module stays unreachable from `matchline run` | n/a | Yes — `source_roof_planes` empty, `roof_solar_aperture` always `skip` (`validate/conservation.py:1218-1220`) | No |
| Contradicts the wall simplifier's precedent (already default-on in Stage 3, `run_pipeline.py:717`) | No | **Yes** | Partially (flag exists but off) |
| Auditability | Full — stage artifact + `roof_simplify:merge` provenance + check payload | Bench-only | Full when enabled |

### 4.4 Open questions that only Alex can close

1. **Default on or off?** The wall simplifier is default-on with a hard 3 %
   export gate (`bem/bem_geometry.py:330-336`); the roof has no equivalent
   gate. Turning roofs on by default without that gate is the riskiest of the
   three shapes; the safest is to add the gate *and* then default on.
2. **Which model wins for BEM?** The gbXML/IFC writers read `model.roof_planes`
   (`bem/bem_export.py:364`, `bem/bem_ifc4.py:531`, `ifc/ifc_export.py:267`).
   If the simplified planes replace the source planes, the exported roof
   geometry is the simplified one — presumably intended — but this also means
   the source planes are export-invisible. Confirm that is what is wanted.
3. **Tolerance policy.** Keep the function's defaults (2 % / 2 % / 5° / 5 %),
   or expose them per run the way `--simplify-tol` exposes the wall
   tolerance? Note that the roof tolerances have **two** budgets, so one
   knob (`--simplify-tol`) is not enough to describe them.

---

## 5. Recommendation

**Recommendation: Option C (opt-in flag), with Option A (default on) as the
promotion target once the guard is hardened.**

### Why Option C and not Option A today

- The pieces exist and are green; only the call site is missing
  (§1 fact 1). Wiring it under a flag costs one call, one flag, one config
  key and one artifact — the same shape as the existing `--auto-triage` /
  `ENABLE_AUTO_TRIAGE=1` precedent (`cli.py:523`, `docs/pipeline.md:16`,
  `:29`) and the `--simplify-tol` / `simplify_tolerance` precedent
  (`cli.py:725`, `cli.py:84`).
- Default-on is not safe yet: the only guard is warn-only
  (`validate/conservation.py:1206-1207`) while the wall path has a hard export
  gate (`bem/bem_geometry.py:330-336`). The roof has no equivalent, so a
  default-on wiring would silently change exported gbXML/IFC roof surfaces
  (`bem/bem_export.py:364`).
- The blast radius is small and well understood: only the `--ifc` path has
  roof planes at all (§2.5), and the model already carries both fields the
  check needs (`building_model.py:985-990`).

### Why not Option B

Option B is not "free": it is the current state, and it costs a public module
(`docs/api.md:86`) with a full test suite, a model-level wrapper, a
provenance stamp and a conservation check that can never fire in production
(`validate/conservation.py:1218-1220` takes the `skip` branch when
`source_roof_planes` is empty). It also contradicts the repo's own precedent:
the wall simplifier *is* in production (`run_pipeline.py:717`, Stage 3,
`docs/pipeline.md:27`). Keeping the roof one out of the pipeline needs an
explicit, written "not wired" note — otherwise the next reader assumes the
same shape as the wall path.

### The shape of the recommended wiring

- **Call site:** `run_pipeline.py:_stage_3_simplify`
  (`run_pipeline.py:708-747`), right after the wall simplification at
  `run_pipeline.py:717`, guarded by the new flag. The existing wrapper
  `apply_roof_simplification(model)` (`geometry/roof_simplify.py:523`) is
  called as-is: it already resolves the latitude from
  `model.site_latitude_deg` (`building_model.py:990`, filled by the importer
  at `ifc/ifc_import.py:3973`) and raises when there is none
  (`geometry/roof_simplify.py:532-534`) — a no-op run must not crash on a
  model without a latitude, so the call site has to catch that.
- **Tolerances:** the function's own defaults, unchanged —
  `area_tol=0.02`, `aperture_tol=0.02`, `angle_tol_deg=5.0`,
  `small_facet_frac=0.05` (`geometry/roof_simplify.py:80-83`), which are the
  values the tests pin (`tests/test_roof_simplify.py:73`, `:84`) and the
  values the in-repo measurements were taken with
  (`docs/building_model.md:205-221`).
- **Report surface:** `roof_simplify_report(res)`
  (`geometry/roof_simplify.py:498-521`) written to a new stage artifact next
  to `stage_03_simplified.json` (`run_pipeline.py:747`). Note that "stage 3b"
  is already taken by the construction library (`run_pipeline.py:1013`), so a
  new stage number or a distinct file name is needed; `docs/pipeline.md:12-18`
  and `docs/pipeline.md:129-134` would have to be updated with it.
- **Guard surface:** the existing `roof_solar_aperture` check
  (`validate/conservation.py:1200`), which starts reporting as soon as
  `model.source_roof_planes` is non-empty — the check needs no change, but
  it needs a decision on warn-vs-error (§4.4, question 1).
- **Config surface:** a new key in the YAML allow-list at `cli.py:83-90`
  (unknown keys are warned about and ignored, `cli.py:91-93`), e.g.
  `roof_simplification: true`, alongside the CLI flag.

### Follow-up issue text Alex could file (draft, not filed by this PR)

> **Title:** Wire roof simplification into `matchline run` behind an opt-in
> flag, then promote it to default once the aperture gate is error-level
>
> **Body:**
> Issue #1275 asked for the wiring decision on
> `geometry/roof_simplify.py::simplify_roof`. The evaluation is in
> `docs/roof-simplify-wiring-decision.md`; this issue carries out the
> recommended shape (Option C).
>
> 1. Add `--roof-simplify` to the CLI (`cli.py`, next to `--simplify-tol`
>    at `cli.py:725`) and a `roof_simplification` key to the YAML config
>    allow-list (`cli.py:83-90`). Default: off.
> 2. In `run_pipeline.py:_stage_3_simplify` (`run_pipeline.py:708-747`),
>    when the flag is on, call
>    `geometry.roof_simplify.apply_roof_simplification(model)` and write
>    `roof_simplify_report(res)` to a new stage artifact. Tolerances stay at
>    the function defaults (2 % area, 2 % aperture, 5°, 5 %) unless
>    `--roof-simplify-tol` is added.
> 3. Do not fail the run when the model has no latitude
>    (`geometry/roof_simplify.py:532-534`); record a skip in the artifact
>    instead.
> 4. Confirm the `roof_solar_aperture` check
>    (`validate/conservation.py:1200`) now reports against
>    `source_roof_planes`, and decide whether it stays warn-only.
> 5. Follow-up (separate issue): promote the roof aperture check to
>    error-level, mirroring the wall export gate
>    (`bem/bem_geometry.py:330-336`), then make roof simplification default
>    on and re-score the GNI-2026 rows (`docs/benchmark_gni26.md`).
>
> **Acceptance:** `matchline run --ifc <file> --roof-simplify` writes the roof
> report artifact with both deltas and the merge/refusal lists; the same run
> without the flag is byte-identical to today; `tests/test_roof_simplify.py`
> and `tests/test_roof_checks.py` stay green.

### What this PR does *not* do

- No production code is changed here: this PR adds the evaluation document
  only (plus its index row in `docs/README.md`).
- The decision itself (which option, and the open questions in
  [§4.4](#44-open-questions-that-only-alex-can-close)) is not taken in this
  document; the recommendation above is a recommendation, not a decision.

---

## 6. Verification of this document's claims

The two commands from the issue brief, run in this worktree, with their
verbatim output:

```
$ grep -rn "simplify_roof" --include="*.py" . | grep -v test
./geometry/roof_simplify.py:3:``simplify_roof`` merges adjacent roof facets greedily, cheapest first, under
./geometry/roof_simplify.py:310:def simplify_roof(
./geometry/roof_simplify.py:535:    res = simplify_roof(model.roof_planes, lat, **kw)

$ python3 -m pytest tests/test_roof_simplify.py -q
..........................                                               [100%]
26 passed in 5.23s
```

The grep output is identical to the one quoted in
[§2.2](#22-every-caller-and-reference): three hits, all inside
`geometry/roof_simplify.py`, none of them a production call site.
