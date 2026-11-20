# Spike report: OpenGeometry/Floorkit typed-JSON agent authoring vs the canonical `BuildingModel`

**Issue:** matchline #1415 — "[spike] OpenGeometry/Floorkit typed-JSON agent authoring vs canonical BuildingModel, Phase 0 premise and format check first (Ploterr-mined)"
**Scope of this report:** Phase 0 only (premise and format check). Desk work. No code, no install, no deployment, no purchase, no Phase 1.
**Base:** `origin/develop` @ `a9c03dc` (worktree branch `worker/1415-opengeometry-phase0`).
**Contract:** `CONTRACT.md` @ `a9c03dc2e813b10e89df7ed587a748eec2baa79b`.
**Report date:** 2026-11-20 (Phase 0 desk check; docs pages fetched 2026-11-20 UTC — see the fetch log, Appendix A).

---

## 0. Method, evidence rules, and what was NOT done

* **Matchline-side claims** are cited to a repo file plus symbol (and line) that was actually read in this worktree: `building_model.py` (`BuildingModel`, `Space`, `SpaceOpening`, `Zone`, `EnvelopeWall`, `BimElement`, `BimOpening`, `Level`, `Provenance`, `BuildingModel.to_json` / `from_json`), `AGENTS.md`, `validate/`, `bem/export_gate.py`.
* **Floorkit/OpenGeometry-side claims** are cited to the public documentation page they came from (URL + section), all of which were fetched over HTTPS read-only and are logged in Appendix A. Anything not visible on a fetched page is marked **not verified** rather than inferred.
* **Field enumeration is from the source, not from the issue's summary.** The canonical field list in §2.2 was read out of `building_model.py`; the Floorkit field list was read out of the fetched TypeScript interface definitions in the vendor's API reference.
* **Hard stops honoured.** Nothing was installed, no npm package was downloaded, no key was requested or applied, no hostname was used, no waitlist was joined, no purchase was made, no `.github/workflows/` file was touched, no production code was changed, and no Phase 1 fixture or mapping code exists. The only file added by this spike is this report.
* **Fetches.** One attempt per page. The issue named three Floorkit/OpenGeometry pages (plus the product site); those were fetched. The vendor's own page footer and its documentation index (`https://docs.ploterr.com/llms.txt`) point at the API reference, and the issue's Phase 0 question 1 is *specifically* a question about whether the JSON document format is documented or inspectable enough to map field by field, so the model-type and coordinate pages surfaced by that index were also fetched read-only (one attempt each). Every fetch is logged in Appendix A with URL, status and byte count; nothing was written outside `/tmp` scratch and nothing from `/tmp` is part of the diff.

---

## 1. Answer summary (the four Phase 0 questions)

| # | Phase 0 question (issue §3) | Answer | Decisive evidence |
|---|---|---|---|
| 1 | Is Floorkit's saved JSON document format documented or inspectable enough to map field by field to `BuildingModel` (spaces, levels, envelope walls, openings, units, coordinate frame)? | **Yes, with named gaps.** The document format is public and typed: `schema: 'floorkit.building-model'`, `version: 3`, `units: 'meters'`, `upAxis: 'y'`, with `BmBuilding` / `BmStorey` / `BmWall` / `BmDoor` / `BmWindow` / `BmSpace` / `BmLevel` interfaces published field by field. Units (metres) and the frame (Y-up world, plan on the XZ plane) are stated by the vendor. Element identity survives (unique `id` on every space, wall and opening; the opening-to-wall relationship *is* the JSON nesting). Six gaps are recorded in §2.4, of which none destroys element identity or the frame conversion. | `docs.ploterr.com/floorkit/concepts/building-model`, `docs.ploterr.com/floorkit/api/architecture/validate-building-model`, `docs.ploterr.com/floorkit/concepts/coordinates-and-units` |
| 2 | Does the local-development license tier permit this evaluation with no key, no deployment, and no purchase? | **Yes for a local, key-free, spend-free evaluation.** The licensing page states local development and non-browser runs need no key ("`localhost`, `*.localhost`, or a loopback address → No key; keep the badge visible"; "Tests, builds, and scripts outside a browser → No key; exported files keep the badge"). A key is needed only for a *deployed* hostname; a trial key (14 days) and paid keys are for deployment. **No purchase, key or deployment is required for Phase 0 or for a local Phase 1**, but the SDK is proprietary and the "made with floorkit" badge must stay visible, so a Go here can never mean "depend on Floorkit". | `docs.ploterr.com/floorkit/licensing` |
| 3 | Is there any export path today that carries rooms, walls and hosted openings as *data*, not as a rendered picture? | **Yes — raw JSON.** The building model *is* a JSON document, and the saved project file (`schema: 'floorkit.project'`, field `buildingModel`) is JSON text that holds the building model with typed elements and ids. PDF is a sheet picture and GLB is exported geometry from the Three.js exporter; IFC and DXF are stated as *coming in the next release*, not shipped. So Phase 1 must test **raw JSON only** (model document and/or project file), never GLB/PDF. | `docs.ploterr.com/floorkit/concepts/exports`, `docs.ploterr.com/floorkit/api/exports/project-file`, `docs.ploterr.com/floorkit/what-is-floorkit` |
| 4 | Write the field mapping and the kill/continue call into the report before any Phase 1 work | **Done.** The field mapping is §2.2, the units/frame statement and the conversion are §2.3, and the Kill/Continue call is §6 (with the gate-by-gate evaluation in §6.1). No Phase 1 work exists. | this file |

**Phase 0 call: CONTINUE to Phase 1**, with the four conditions in §6.2. The deciding gate is **Kill gate 1** ("Phase 0 finds no inspectable JSON document format, or evaluation needs a paid key, a deployed hostname, or any spend"): the format *is* inspectable and the evaluation *does not* need a key, a hostname or spend, so that gate does not fire. Kill gate 2 ("mapping loses element identity … or cannot state a coordinate-frame conversion") also does not fire: ids are preserved and the conversion is stated in §2.3.

---

## 2. Question 1 — is the saved JSON document format documented / inspectable enough to map field by field?

### 2.1 What the document is (vendor's own words, fetched pages)

* "A building model is one JSON document. It describes the building with numbers and ids, not with meshes." — `concepts/building-model` (https://docs.ploterr.com/floorkit/concepts/building-model).
* Header fields: `schema: 'floorkit.building-model'`, `version: 3`, `units: 'meters'`, `upAxis: 'y'`, `id`, optional `wallTypes` — `concepts/building-model` ("The header fields" table) and `api/architecture/validate-building-model` (`interface BuildingModel`, where `units` and `upAxis` are *required*, `version` is `1 | 2 | 3 | 4`).
* Structure: a model contains **buildings**; a building contains **storeys**; a storey holds `walls`, `slabs`, `roofs`, `columns`, `beams`, `stairs`, `railings`, `spaces`, `instances`, `unhostedOpenings`, `modelObjects`, `annotations`, `roomSeparators` — `concepts/building-model` ("Structure" table) and `interface BmStorey` in `api/architecture/validate-building-model`.
* "Each wall, opening and room is a real building element with data. It is not only a picture of one. A door cuts its wall, and a wall joins the walls that it touches." — `what-is-floorkit`.
* Deterministic check: "`validateBuildingModel` reads a document and returns each problem with a path, a code and a message. It does not need the geometry kernel, so you can also run it on a server." — `concepts/building-model` ("Check a model"). The doc index repeats it: "Model JSON validation does not need kernel initialization." — `https://docs.ploterr.com/llms.txt`.
* "The TypeScript contract is not a downloadable JSON Schema or an LLM tool definition." — `concepts/building-model` ("Agent proposals"). This is a real limitation for a *machine-readable* schema, but the field-by-field contract is published as TypeScript interfaces (see below), which is enough for a hand-written mapping.

The field-level types were read from `api/architecture/validate-building-model` (the "Model types" section), which publishes `interface BuildingModel`, `interface BmBuilding`, `interface BmStorey`, `interface BmLevel`, `interface BmWall`, `interface BmSpace`, `interface BmOpeningBase` / `BmDoor` / `BmWindow`, `type BmOpening = BmDoor / BmWindow / BmVoid`, `type BmStationAlong = number`, `type PolygonProfile`.

### 2.2 Field mapping: Floorkit typed JSON → canonical `BuildingModel`

Canonical side is read from `building_model.py` at the base commit. "Direct" = same meaning, same unit, no transformation. "Derived" = computable from Floorkit fields by a stated rule. "Gap" = no canonical counterpart; needs a rule, an inference, or a drop (all gaps are enumerated in §2.4).

#### Model / level

| Floorkit (page · type.field) | Canonical `building_model.py` | Conversion / rule | Status |
|---|---|---|---|
| `BuildingModel.schema` = `'floorkit.building-model'` | `BuildingModel.to_dict` writes `{"model_version": MODEL_VERSION, "model": {...}}` (`building_model.py:1194`, `MODEL_VERSION = "1.3"` at `:42`) | no counterpart; record the source schema string in the provenance note | Gap (envelope only) |
| `BuildingModel.version` (1–4) | `BuildingModel.model_version` = `"1.3"` | not comparable (Floorkit document schema version ≠ canonical model schema version); keep both | Derived |
| `BuildingModel.units` = `'meters'` (required) | every canonical length is metres (`*_m`, `*_m2`, `*_m3` suffixes, e.g. `Space.polygon_m`, `SpaceOpening.width_m`) | identical unit, no scaling | Direct |
| `BuildingModel.upAxis` = `'y'` (required, `'y'` only) | canonical has no up-axis flag; the frame is fixed by the module docstring ("Coordinate frame (canonical): meters, y growing DOWNWARD (drawing frame…)", `building_model.py:24-25`; `BuildingModel` docstring `:889`; `AGENTS.md:78`) | see §2.3 | Derived (conversion) |
| `BuildingModel.buildings[]` | one canonical building (no building collection) | take the first building; a multi-building document is out of canonical shape | Derived |
| `BmBuilding.id` / `name` | `BuildingModel.name` (display name) | `name` → `BuildingModel.name`; `id` kept in provenance note | Derived |
| `BmBuilding.storeys[]` | `BuildingModel.levels: List[Level]` (`:943`; `Level` at `:691`) | 1:1, in document order | Direct |
| `BmStorey.id` | `Level.id` (`:692`) | verbatim | Direct |
| `BmStorey.name` | `Level.name` (`:693`) | verbatim | Direct |
| `BmStorey.elevation` ("World Y of this storey's structural floor datum") | `Level.elevation_z_m` (`:694`) | metres, world datum → verbatim; note that the canonical origin is the arch-plan frame, the Floorkit origin is the model world zero, so a global translation is free but the two origins are not the same point (finding F-6) | Direct |
| `BmStorey.height` ("Floor-to-floor height. Also the default wall height…") | `Level.wall_height_m` (`:695`, default 3.0) | floor-to-floor vs wall height: use `BmStorey.height` for `Level.wall_height_m` only when the fixture has no separate wall height; otherwise take `BmWall.height` per wall (see below) | Derived |
| `BmBuilding.levels[]` (`BmLevel`: datum levels) | none | canonical `Level` *is* a storey; datum levels have no home | Gap G-6 |

#### Spaces (rooms)

| Floorkit | Canonical | Conversion / rule | Status |
|---|---|---|---|
| `BmStorey.spaces[]` | `BuildingModel.spaces: Dict[str, Space]` (`:944`) keyed by `Space.id` | 1:1 | Direct |
| `BmSpace.id` | `Space.id` (`:325`), documented as `"{level}-{number}"` or `"{level}-UNLABELED-{k}"` | the canonical id is a *derived* key: build it as `f"{level_id}-{number}"` when `BmSpace.number` exists, else `f"{level_id}-UNLABELED-{k}"` with a deterministic `k`; the original Floorkit id is preserved in the provenance note (the canonical `Space` has no free-form id field) | Derived (identity preserved in provenance) |
| `BmSpace.name` | `Space.name` (`:327`) | verbatim | Direct |
| `BmSpace.number` | `Space.number` (`:328`) | verbatim | Direct |
| `BmSpace.profile` (`PolygonProfile`, plan metres) | `Space.polygon_m` (`:329`, `[[x, y], …]` y-down) | point-by-point frame conversion, §2.3: `x = X`, `y = Z`, ring order kept | Direct (after frame conversion); member names of `PolygonProfile` are **not verified** on the fetched pages → G-1 |
| `BmSpace.arcs.outerRing` / `.holes` | `Space.polygon_m` (no arcs in canonical) | canonical polygons are straight-segment only → an arc edge must be approximated or dropped (canonical has no arc field). The fixture must use straight walls only | Gap G-2 |
| `BmSpace.height` | `Space.height_m` (`:365`, "full floor-to-top height … as the source states it") | verbatim | Direct |
| `BmSpace.baseOffset` | no counterpart (canonical has no per-space slab offset) | drop | Gap G-6 |
| `BmSpace.usage` (free text 1–64 chars) | `Space.use: SpaceUse` (`:335`; `SpaceUse` at `:238`, key field `space_type` at `:249`) | not a key into `space_use_defaults_data.SPACE_USE_DEFAULTS`; treat as a free-text label kept in the provenance note, do **not** invent a prototype space type | Gap G-3 |
| `BmSpace.propertySets` / `psets` | none | drop (canonical has no per-space pset store) | Gap G-6 |
| (no area field on `BmSpace`) | `Space.area_m2` (`:330`, optional) | canonical computes it from `polygon_m`; Floorkit stores no area → let the canonical side compute | Derived |
| — | `Space.poly_type` (`:339`), `poly_type_confidence` (`:342`) | canonical classification of room/shaft/closet; Floorkit has no equivalent → leave canonical default `"room"`, confidence `None` | Derived (default) |

#### Walls (envelope) — the hardest part

| Floorkit | Canonical | Conversion / rule | Status |
|---|---|---|---|
| `BmStorey.walls[]` | `BuildingModel.envelope: List[EnvelopeWall]` (`:951`; `EnvelopeWall` at `:593`) | the canonical envelope holds **exterior wall runs only**; Floorkit stores every wall with no exterior flag → the mapping must classify exterior vs interior (inference) | Gap G-4 (see §2.4) |
| `BmWall.id` | `EnvelopeWall.id` (`:598`) | verbatim | Direct |
| `BmWall.points` ("Centreline in the world XZ plane") | `EnvelopeWall.from_m` / `.to_m` (`:600-601`, `[x, y]` canonical m) | 2-point straight wall → `from_m = points[0]`, `to_m = points[1]` after the §2.3 conversion; a multi-segment or arc wall must be split into one `EnvelopeWall` per straight segment (canonical has no polyline run) → id suffix `"{id}#s{n}"` | Direct / Derived (split) |
| `BmWall.thickness` | no field on `EnvelopeWall` | canonical keeps thickness in the construction (`Construction` at `:573`, `material_layers`) | Derived |
| `BmWall.height` (defaults to storey height) | `EnvelopeWall.height_m` (`:602`) | verbatim (storey default applied by the vendor) | Direct |
| `BmWall.material` / `BmWall.typeRef` → `BmWallType` (layers: material, thickness, function) | `Construction.id` / `.name` / `.u_value_w_m2k` (`:582-583`) and `EnvelopeWall.construction_id` (`:609`), plus `Construction.material_layers` (`[{"material", "thickness_m"}]`) | `typeRef` → `Construction.id`; each `BmWallType` layer → one `material_layers` entry. **No U-value exists anywhere in Floorkit** → `Construction.u_value_w_m2k` stays `None` (the canonical rollup then leaves those segments out of the weighted U, which is the documented canonical behaviour, not a bug) | Derived |
| `BmWall.labelName` | none | drop | Gap G-6 |
| `BmWall.closed` | none | drop (canonical envelope is a list of open runs) | Gap G-6 |
| (no facade/orientation field on a wall) | `EnvelopeWall.facade` (`:599`, `"south"` / `"north"` / `"east"` / `"west"`) | needs a compass; Floorkit gives site orientation only as `BmSite.trueNorthDeg` ("Clockwise from −Z, seen from above"). Canonical has **no** north reference field (only `site_latitude_deg`), so the facade string is either left `""` or derived under an explicitly stated orientation assumption | Gap G-5 |
| (no space↔wall link) | `EnvelopeWall.space_id` (`:610`) | canonical wants the enclosed space on the segment; Floorkit spaces carry their own polygons, so the enclosed space must be found by point-in-polygon of the wall centreline against `Space.polygon_m` | Derived (inference) |

#### Openings (hosted in walls)

| Floorkit | Canonical | Conversion / rule | Status |
|---|---|---|---|
| `BmWall.openings[]` — "Hosted by this wall. The nesting IS the host relationship." | `Space.openings: List[SpaceOpening]` (`:332`; `SpaceOpening` at `:138`) | the canonical opening hangs off a **space**, not off a wall; the host wall must be resolved to the space(s) it bounds | Derived |
| `BmDoor.kind` / `BmWindow.kind` | `SpaceOpening.category` (`:143`, `"window"` / `"door"` / `"skylight"`) | `"door"`→`"door"`, `"window"`→`"window"`; `BmVoid` (plain hole, `type BmOpening = BmDoor / BmWindow / BmVoid`) has **no** canonical category → drop with a recorded finding | Direct / Gap G-3b |
| opening `id` | `SpaceOpening.id` (`:141`) | verbatim (unique per the vendor rule "Each id is unique") | Direct |
| (no tag field) | `SpaceOpening.tag` (`:142`) | leave `""` | Gap G-3 |
| `BmDoor.width` / `height`, `BmWindow.width` / `height` | `SpaceOpening.width_m` / `.height_m` (`:144-145`) | verbatim, metres | Direct |
| `BmWindow.sillHeight` ("Bottom of the glass above the wall base") | `SpaceOpening.sill_m` (`:146`, above the floor datum) | verbatim if the level datum is the storey elevation (it is: `BmStorey.elevation` is the structural floor datum) | Direct |
| (no sill on a door) | `SpaceOpening.sill_m` | `BmDoor.baseHeight` ("Threshold height above the wall base", default 0) → `sill_m` | Direct |
| (derived) | `SpaceOpening.head_m` (`:147`, "sill + height; drives daylighting") | `head_m = sill_m + height_m` | Derived |
| `BmOpeningBase.offset` (`type BmStationAlong = number`, "Distance along the wall centreline from `points[0]`") | `SpaceOpening.host_interval_m` (`:149`, `[s0, s1]` along the host wall, m) | `host_interval_m = [offset, offset + width]` — exact, because the vendor defines the station as an arc length along the same centreline the canonical interval is measured on | Derived (exact) |
| (derived) | `SpaceOpening.s_center_m` (`:150`, exact along-wall position) | `s_center_m = offset + width / 2` | Derived (exact) |
| (host facade) | `SpaceOpening.host_facade` (`:148`) | same north problem as `EnvelopeWall.facade` → G-5 | Gap G-5 |
| `BmDoor.doorType`, `swingAngleDegrees`, `leafStyle`, `doorQuadrant`, `windowType`, `divisions`, `lights`, `sill`, `bay` | no canonical field on `SpaceOpening` (canonical `SpaceOpening` is geometry + hosting only) | drop; the canonical IFC-side `BimOpening` (`:614`) does carry `operation_type` / `leaf_count` / `hinge_side`, but that is the IFC path, not this one | Gap G-6 |
| `BmStorey.unhostedOpenings[]` | none | drop (canonical openings are always hosted) | Gap G-6 |

#### Other elements, energy semantics, provenance

| Floorkit | Canonical | Note | Status |
|---|---|---|---|
| `BmStorey.slabs[]`, `roofs[]`, `columns[]`, `beams[]`, `stairs[]`, `railings[]`, `instances[]`, `modelObjects[]`, `annotations[]`, `roomSeparators[]` | `BuildingModel.roof_planes` / `source_roof_planes` (`RoofPlane` at `:845`), `shading` (`ShadingSurface` at `:540`), `terrain`, `model_objects`-free | only roofs and shading have plausible canonical homes; the rest have none. Out of scope for the 2-room fixture. | Gap G-6 |
| (no zones, no constructions with thermal values, no schedules) | `BuildingModel.zones` (`Zone` at `:385`), `constructions` (`Construction` at `:573`), `schedules`, `Space.hvac`, `Space.use` | Floorkit has **no energy semantics** — as the issue states. The canonical zone/HVAC layer stays empty; this is expected, not a defect, and the spike does not grade Floorkit on it | Out of scope |
| (nothing) | `Provenance` (`:64`) on every derived fact | Floorkit carries no provenance. It can be attached **at mapping time** without inventing a source: `sheet_id = "floorkit:<model id>"`, `revision = BuildingModel.version`, `method = "floorkit_typed_json_import"`, `confidence = 1.0` for verbatim fields and a stated lower value for derived ones, `bbox = None`, `note` carrying the original Floorkit ids. The **confidence semantics are undefined by the vendor** → finding F-4, to be settled in Phase 1, not a Phase 0 blocker | Derived |

### 2.3 Units and coordinate frame (settled on paper)

**Canonical Matchline frame** — metres, **y growing downward** (drawing frame):
* `building_model.py:24-25` — "Coordinate frame (canonical): meters, y growing DOWNWARD (drawing frame, matches every synth layout and sheet raster). BEM export flips y to north-up."
* `building_model.py:889` — same statement on `BuildingModel`.
* `AGENTS.md:78` — "Coordinate frame: canonical model uses y-down (drawing frame); BEM export flips to north-up."
* `building_model.py:848-849` — for 3D vertices: "plan x east, plan y down the sheet (south), z metres up from the host level's floor" (i.e. +y is south, +x is east, and the canonical frame is a *drawing* frame with no independent north datum of its own).
* Plan data is 2D: `Space.polygon_m` (`:329`), `EnvelopeWall.from_m` / `.to_m` (`:600-601`); vertical data is stored per level/element, not in the plan frame: `Level.elevation_z_m` (`:694`), `Space.height_m` (`:365`), `SpaceOpening.sill_m` / `.head_m` (`:146-147`), `ComponentRef` uses `x_m` / `y_m` only (`:125-126`).

**Floorkit frame** — metres, **Y up**, plan on the **XZ** plane:
* `api/architecture/validate-building-model` — `BuildingModel.units` is `'meters'` (required); `BuildingModel.upAxis` is `'y'` (required).
* `concepts/coordinates-and-units` — "Model | `[x, y, z]`, the Y axis is up | Metres"; "Plan | `[x, z]`, seen from above | Metres"; "Paper | `{x, y}` from the top-left corner of the sheet | Millimetres"; "Screen | … | Pixels".
* `concepts/coordinates-and-units` — "A storey has an `elevation`. This is the only height in the model that is measured in the world. Each element in the storey gives its height as an offset from that elevation." (`Slab baseOffset`, `Roof baseOffset`, `Column baseOffset`, `Door baseHeight`, `Window sillHeight`).
* `concepts/coordinates-and-units` — "`yawDeg` of a free opening: Degrees. Zero points to +X. A positive angle turns to +Z."; "`Site trueNorthDeg`: Degrees. Clockwise from −Z, seen from above."
* `concepts/building-model` — "A wall is a line of `[x, z]` points…"; `api/architecture/validate-building-model` — `BmWall.points`: "Centreline in the world XZ plane… the compiler places it at the storey's elevation."

**The conversion (total, not partial):**

1. **Plan:** Floorkit `(X, Z)` → canonical `(x, y)` with **`x = X`, `y = Z`** — *no sign flip, no scaling*. Justification: the world is right-handed with Y up; viewed from above (looking down −Y) +X is to the right and **+Z points down the sheet** (equivalently −Z is north, since `trueNorthDeg` is measured clockwise from −Z). That is exactly the canonical drawing frame, whose +y runs down the sheet (south) and whose +x runs east. Both sides are metres, so no scale factor.
2. **Vertical:** Floorkit world **Y** ↔ canonical `Level.elevation_z_m` (storey `elevation` → level datum). Storey-local heights (`BmDoor.baseHeight`, `BmWindow.sillHeight`, `BmSlab.baseOffset`) are heights above that datum → canonical heights above the level floor datum (`SpaceOpening.sill_m`, `SpaceOpening.head_m = sill_m + height_m`). Canonical has no per-element z in the plan frame, so no z is lost.
3. **Origin:** Floorkit's world zero is the model origin; the canonical frame's origin is the architectural plan's origin. The two need not coincide, and the mapping does not require them to: both are metres in an arbitrary plan origin, so a pure translation is free. The canonical side carries no site/georeference beyond `site_latitude_deg` (`building_model.py`, `BuildingModel.site_latitude_deg`), and Floorkit carries `BmSite.elevation` / `trueNorthDeg`, so a georeferenced join is not attempted in this spike.
4. **North-up exports** (out of scope for the mapping, stated for completeness): the canonical BEM export flips y for north-up (`building_model.py:24-26`, `AGENTS.md:78`), i.e. canonical → north-up is a y-sign flip; Floorkit → north-up is the same sign flip on the Z coordinate. The two flips are the same operation, so the mapping is the identity in plan and the flip stays where it already is.

**Conclusion for the frame gate:** a coordinate-frame conversion *can* be stated, and it is stated above: `x = X`, `y = Z`, `z = storey.elevation + local_offset`. Kill gate 2's "cannot state a coordinate-frame conversion" clause does not fire.

### 2.4 Gaps and findings carried into Phase 1

| id | Finding | Severity for Phase 1 |
|---|---|---|
| G-1 | The exact member names of `PolygonProfile` (the shape of `BmSpace.profile`) are **not verified**: the type is referenced by `BmSpace.profile`, `BmSlab.profile`, `BmSite.boundary` but is not defined on the pages fetched. `concepts/building-model` only says a space "takes `arcs` (`outerRing` and `holes`, one per edge)". Phase 1 must read the SDK's own type declaration (or the API reference page for that type) before writing the polygon mapper. | Medium — blocks the space polygon mapper, not the format question |
| G-2 | Canonical polygons are straight-segment only; Floorkit spaces and walls may carry arcs. | Low for the fixture (straight walls only) |
| G-3 | Free-text `usage`, missing opening `tag`, and `BmVoid` openings have no canonical home. | Low |
| G-4 | **Exterior vs interior wall**: the canonical envelope holds exterior runs only; Floorkit marks neither. The mapping must classify (e.g. a wall whose centreline lies on a space boundary and outside all space polygons). | **High — this is the main Phase 1 risk** |
| G-5 | Compass `facade` / `host_facade` strings: canonical wants `"south"` / `"north"` / `"east"` / `"west"`, Floorkit gives only `BmSite.trueNorthDeg`, and canonical has no north datum. Needs an explicitly stated orientation assumption, or the fields stay `""`. | Medium |
| G-6 | Whole-element drops with no canonical counterpart (slabs, columns, beams, stairs, railings, instances, annotations, room separators, psets, datum levels, building collection). | Informational for the fixture |
| F-4 | Provenance confidence semantics for an agent-authored document are undefined by the vendor. | Medium — settle in Phase 1 |
| F-6 | The canonical frame's origin and Floorkit's world zero are independent; a pure translation is assumed. | Informational |
| F-7 | The contract is published as TypeScript interfaces, **not** as a downloadable JSON Schema ("The TypeScript contract is not a downloadable JSON Schema or an LLM tool definition" — `concepts/building-model`). A machine-readable schema is therefore unavailable today; the interfaces are the contract. | Informational, but relevant to the "reimplement natively" Go condition |

---

## 3. Question 2 — licensing: does the local tier permit this evaluation with no key, no deployment, no purchase?

From `docs.ploterr.com/floorkit/licensing` (fetched; the issue's own source line already summarised it, and the fetched page confirms it):

* "Floorkit is proprietary software. You can install the public package and develop locally without a key. Every deployed website needs a valid key for its hostname, including a public prototype or non-commercial demo."
* "When you need a key" table: `localhost`, `*.localhost`, or a loopback address → "**No key**; keep the badge visible"; "Tests, builds, and scripts outside a browser" → "**No key**; exported files keep the badge"; "Any deployed website" → "A valid key for that hostname"; "Teaching or research on a deployed website" → "Ask about academic terms and a hostname key".
* "A trial key lets you evaluate deployment for 14 days and keeps the `made with floorkit` badge. A paid key removes the badge. Paid keys have a 30-day technical grace period after expiry; trial keys do not."
* "On a deployed hostname without a valid key, views stop after five seconds and SDK exports throw `LicenseError`. A wrong-domain, invalid, or expired key is also refused. **Saving the project remains available**, so your app can preserve the user's work."
* Keys are applied as `VITE_FLOORKIT_LICENSE_KEY` / `setLicenseKey(...)` and are per-hostname ("Use HTTPS on a named domain covered by the key") — none of which Phase 0 or a local Phase 1 needs.
* The docs index adds: "Floorkit is proprietary. Local development and non-browser runs need no key." (`https://docs.ploterr.com/llms.txt`).

**Answer: yes.** The evaluation can be done with **no key, no deployment and no purchase**: local development and non-browser runs are explicitly key-free, and a headless model check is explicitly supported ("Model JSON validation does not need kernel initialization", `llms.txt`; "It does not need the geometry kernel, so you can also run it on a server", `concepts/building-model`). The only obligations are: keep the "made with floorkit" badge visible, and do not use a deployed hostname without a key. Nothing in this spike or in a local Phase 1 requires the trial key (14 days) or a paid key, so the Kill gate 1 clause "evaluation needs a paid key, a deployed hostname, or any spend" **does not fire**.

**License boundary (must survive into any later report):** Floorkit is proprietary and Matchline is open source under a standing no-side-revenue / DOE-lab funding strategy (issue §2). A Continue call here means only what the issue says it can mean: Floorkit stays an *optional external authoring front end evaluated from outside the repo*, or its **document contract is mined** into Matchline's own model layer. It does **not** mean Matchline may take a dependency on `@opengeometry/floorkit`, vendor it, or ship it in the pipeline.

---

## 4. Question 3 — is there an export path that carries rooms, walls and hosted openings as data?

From `docs.ploterr.com/floorkit/concepts/exports` and `docs.ploterr.com/floorkit/what-is-floorkit` ("Open formats: Export sheets as PDF and the 3D model as GLB. IFC and DXF come in the next release."), plus `docs.ploterr.com/floorkit/api/exports/project-file`:

| Format | Status (vendor) | Carries rooms / walls / hosted openings **as data**? |
|---|---|---|
| **Raw JSON (building model)** | The building *is* one JSON document (`schema: 'floorkit.building-model'`, `version: 3`, `units`, `upAxis`, `buildings[]`). Every element carries a unique `id`; the wall→opening host relation is the JSON nesting (`BmWall.openings[]`). | **Yes — full typed data with element identity.** This is the Phase 1 carrier. |
| **Raw JSON (project file)** | `saveProject` / `openProject`; `interface ProjectFile { schema: 'floorkit.project'; version: 1 / 2 / 3; buildingModel: BuildingModel; views: SavedDrawingView[]; paper: PaperDocument; … }`. "The file does not hold geometry, images or undo history." The vendor's own words: "The project file is JSON text. It holds the building model, the views, the sheets and the `.op` text of the external blocks… **It is the only format that Floorkit opens again for editing.**" | **Yes** — the project file carries the same typed `buildingModel` plus saved views/sheets. |
| PDF | Available ("Sheets that people read, print and sign"; "Export sheets as PDF with vector linework") | No — a sheet picture |
| GLB | Available ("The 3D model in viewers, game engines and AR"; "A GLB contains the 3D model in one binary file. Floorkit makes standard Three.js objects, so you export them with the Three.js exporter.") | No — rendered geometry; the fetched pages make no promise of typed element identity in the GLB, so it must be treated as geometry only |
| IFC | **Not shipped** — "IFC export for building models comes in the next release" (`api/exports/ifc`) | n/a |
| DXF | **Not shipped** — "DXF export for drawings comes in the next release" (`api/exports/dxf`) | n/a |

**Answer: yes — raw JSON is the data path** (both the model document and the saved project file). PDF and GLB are pictures/geometry, and IFC/DXF do not exist yet. So Phase 1 must test **raw JSON only**, exactly as the issue's Phase 0 question 3 anticipates ("Phase 1 then tests raw JSON only, or closes"). The raw-JSON path also happens to be the one that does not need a deployed hostname: the file is text the vendor's own functions read and write without disk or network.

---

## 5. Question 4 — where the mapping and the call live

* Field mapping: **§2.2** (with the units/frame conversion in §2.3 and the gap list in §2.4).
* Kill/Continue call: **§6** (gate-by-gate evaluation in §5).

No Phase 1 work was done: no fixture, no mapping code, no scratch directory, no install.

---

## 6. Gate evaluation and the Kill / Continue call

### 6.1 The issue's pre-registered gates (issue §4), evaluated

| Gate | Type | Status after Phase 0 | Evidence |
|---|---|---|---|
| "Phase 0 finds no inspectable JSON document format, or evaluation needs a paid key, a deployed hostname, or any spend." | Kill | **Does not fire.** The document format *is* inspectable (typed interfaces published, `concepts/building-model` + `api/architecture/validate-building-model`), and the evaluation needs no key, no hostname and no spend (`floorkit/licensing`: local dev and non-browser runs are key-free). | §1, §2.1, §3 |
| "The mapping loses element identity for rooms, walls, or openings, or cannot state a coordinate-frame conversion." | Kill | **Does not fire.** Identity survives: unique `id` on `BmSpace`, `BmWall`, `BmDoor` / `BmWindow`; the host relation is the JSON nesting. The frame conversion is stated: `x = X`, `y = Z`, `z = storey.elevation + offset`. | §2.2, §2.3 |
| "Mapped model fails the existing validation battery on room closure or area conservation, where the synth baseline passes." | Kill | **Not decidable in Phase 0** (Phase 1 gate; the battery is `validate/` — `validate/invariants.py`, `validate/conservation.py`, `validate/export.py` — plus `bem/export_gate.py`). | Phase 1 |
| "Room areas differ from hand ground truth by more than 1 percent, or any hosted opening lands on the wrong wall or outside its host wall interval." | Kill | **Not decidable in Phase 0.** Note that the host interval is computable exactly (`[offset, offset + width]`), so the "outside its host wall interval" half is low risk; the "wrong wall" half is the real risk, because Floorkit does not say which wall is exterior (G-4). | Phase 1 |
| "Provenance cannot be attached at mapping time without inventing sources." | Kill | **Does not fire, with a caveat.** A `Provenance` (`building_model.py:64`) can be attached at mapping time with a truthful source (`sheet_id = "floorkit:<model id>"`, `revision = document version`, `method = "floorkit_typed_json_import"`); nothing has to be invented, because the document itself *is* the source. The caveat is F-4: the vendor defines no confidence semantics for an agent-authored document, so the confidence value is a Matchline-side convention and must be declared as such. | §2.2 (provenance row), §2.4 |
| "Fixture maps completely, validation battery and export gate pass, areas within 1 percent, openings correctly hosted." | Go | Not decidable in Phase 0 (Phase 1). | Phase 1 |
| "The agent-proposal contract (typed document in, deterministic check/build, JSON diagnostics out) is documented well enough to reimplement against `BuildingModel` natively, without the proprietary SDK, if Alex wants that lane." | Go | **Satisfied on the evidence available.** The contract is published: a typed JSON document in (`concepts/building-model`, `interface BuildingModel`), a deterministic check out with `path` / `code` / `message` per problem (`validateBuildingModel`, `api/architecture/validate-building-model`), and "A valid document can still fail during geometry construction… keep the existing project until its replacement builds" (`concepts/building-model`, "Agent proposals"). Two caveats: the contract is TypeScript interfaces, **not** a downloadable JSON Schema (F-7), and the SDK itself is proprietary (§3). | §2.1, §3 |
| "Report states the license boundary explicitly, so a later reader cannot mistake a Go for permission to depend on Floorkit." | Go | **Satisfied by §3** ("License boundary"). | §3 |

### 6.2 The call

**CONTINUE TO PHASE 1** (the issue's Phase 1 scope: one 2-room fixture, mapped in scratch, validated against `validate/` and `bem/export_gate.py`, with a synth baseline comparison, and a `REPORT.md` with its own Go/No-go).

**The gate that decided it: Kill gate 1** — the only Kill gate Phase 0 can decide on the evidence available, and it did not fire (the JSON document format *is* inspectable/documented, and the evaluation needs no key, no deployed hostname and no spend). Kill gate 2 was checked in the same Phase 0 pass and did not fire either (element identity preserved; the coordinate-frame conversion is stated in §2.3). The remaining three Kill gates and the fixture-dependent Go gates are Phase 1 gates and are **not** claimed here.

**Conditions attached to the Continue (binding on Phase 1):**
1. **License boundary stays explicit.** No Matchline dependency, no vendored Floorkit code, no new entry in `pyproject.toml`, all Phase 1 artefacts in scratch outside the package; the "made with floorkit" badge rule is respected if any output is produced; no key, no hostname, no deployment, no spend.
2. **Close G-1 before writing the polygon mapper** (the `PolygonProfile` member names are not verified), and state in the Phase 1 report which page or type declaration settled it.
3. **Resolve G-4 (exterior-wall classification) explicitly and report it as a mapping decision, not a silent inference** — it is the one gap that can trip the "opening lands on the wrong wall" Kill gate.
4. **Record provenance as attached-at-mapping-time with a declared confidence convention (F-4)** and keep the original Floorkit ids in the provenance note, so element identity survives into the canonical model.

**What this Continue does NOT mean:** it is not permission to adopt Floorkit as a Matchline dependency, not a purchase request, not a deployment, and not a claim that the fixture will map cleanly — G-4 in particular could still turn into a Phase 1 Kill.

---

## 7. Not verified / limitations of this Phase 0 report

* `PolygonProfile`'s member names are **not verified** (G-1); the fetched pages reference the type but do not define it.
* The exact behaviour of `validateBuildingModel`'s issue codes and of the build step (`buildFromBuildingModel`, `api/architecture/build-from-building-model`) was **not** read — not needed for Phase 0.
* The GLB export's ability to carry element identity was **not** established; it is treated as geometry-only, which is the conservative reading of "the 3D model as one GLB file … with the Three.js exporter".
* Whether Floorkit's kernel actually cuts an opening into its host wall (the "a door cuts its wall" claim) was **not** tested — testing it is Phase 1 and would need the SDK installed, which Phase 0 forbids.
* Nothing in the Matchline repo was executed for this report beyond file reads and greps; this is a docs-only change, so no test suite was run (CI runs it on the PR).

---

## Appendix A — external fetch log (read-only, HTTPS, one attempt each)

| # | URL | HTTP | Bytes | Used for |
|---|---|---|---|---|
| 1 | https://docs.ploterr.com/floorkit/what-is-floorkit | 200 | 262 889 | Q1/Q3 premise: "The building is a typed JSON document"; "Export sheets as PDF and the 3D model as GLB. IFC and DXF come in the next release." |
| 2 | https://docs.ploterr.com/floorkit/licensing | 200 | 262 995 | Q2: key table (localhost / non-browser runs → no key), 14-day trial, `LicenseError`, badge rule |
| 3 | https://opengeometry.io/ | 200 | 29 001 | vendor/kernel site named in the issue (landing page; no field-level detail) |
| 4 | https://ploterr.com/ | 200 | 48 116 | product site named in the issue |
| 5 | https://docs.ploterr.com/llms.txt | 200 | 29 654 | the vendor's own documentation index (linked from page 1); used to locate the model-type and coordinate pages |
| 6 | https://docs.ploterr.com/floorkit/concepts/building-model.md | 200 | 6 842 | Q1: document structure, header fields, validity rules, `validateBuildingModel` contract |
| 7 | https://docs.ploterr.com/floorkit/concepts/coordinates-and-units.md | 200 | 3 232 | Q1: units (metres) and the Y-up / plan-[x,z] frame |
| 8 | https://docs.ploterr.com/floorkit/api/architecture/validate-building-model.md | 200 | 73 240 | Q1: the field-by-field TypeScript interfaces (`BuildingModel`, `BmBuilding`, `BmStorey`, `BmLevel`, `BmWall`, `BmSpace`, `BmDoor`, `BmWindow`, `BmOpeningBase`, `BmStationAlong`) |
| 9 | https://docs.ploterr.com/floorkit/api/exports/project-file.md | 200 | 5 385 | Q3: the saved project JSON (`schema: 'floorkit.project'`, `buildingModel`) |
| 10 | https://docs.ploterr.com/floorkit/concepts/exports.md | 200 | 3 175 | Q3: export format table (PDF / GLB available; IFC / DXF next release) |
| 11 | https://docs.ploterr.com/floorkit/api/overview.md | 200 | 2 765 | cross-check of the API surface |
| 12 | https://docs.ploterr.com/floorkit/api/primitives/polygon-primitive.md | 200 | 2 269 | attempt to find `PolygonProfile` — **not found there**; the type is used by `BmSpace.profile` but its members remain unverified (G-1) |

No fetch was retried; no page was fetched more than once. Nothing was written outside `/tmp` scratch, and no `/tmp` artefact is part of the repository diff.

Not fetched (and not needed): the X post named in the issue (`https://x.com/Bootsblac/status/2123441338343641433`) — it is a social post, not a documentation page, and every fact this report needs from it is independently confirmed on the vendor's own pages above. The issue's section-2 facts about Floorkit (typed JSON document, PDF/GLB exports, proprietary licensing) were each re-confirmed against the fetched vendor pages rather than taken on trust.

## Appendix B — Matchline-side citation index (all read in this worktree at `a9c03dc`)

| Claim | File · symbol · line |
|---|---|
| Canonical model dataclass tree exists (Space, Zone, EnvelopeWall, BimElement, BimOpening, Level) | `building_model.py`: `Space` `:324`, `Zone` `:385`, `EnvelopeWall` `:593`, `BimOpening` `:614`, `BimElement` `:658`, `Level` `:691`, `BuildingModel` `:870` |
| JSON round-trip with a model version | `building_model.py`: `BuildingModel.to_dict` `:1194`, `BuildingModel.to_json` `:1201`, `BuildingModel.from_dict` `:1205`, `BuildingModel.from_json` `:1212`, `MODEL_VERSION = "1.3"` `:42`, `_migrate_model_dict` `:1224` |
| Canonical frame: metres, y downward | `building_model.py:24-25` (module docstring), `:889` (`BuildingModel` docstring), `AGENTS.md:78` |
| Canonical plan frame orientation (+x east, +y down the sheet/south, z up from level floor) | `building_model.py:848-849` (`RoofPlane` docstring) |
| Space fields used in the mapping | `building_model.py:324-343` (`id` `:325`, `level_id` `:326`, `name` `:327`, `number` `:328`, `polygon_m` `:329`, `area_m2` `:330`, `openings` `:332`, `use` `:335`, `height_m` `:365`, `poly_type` `:339`) |
| SpaceOpening fields used in the mapping | `building_model.py:138-179` (`id` `:141`, `tag` `:142`, `category` `:143`, `width_m` `:144`, `height_m` `:145`, `sill_m` `:146`, `head_m` `:147`, `host_facade` `:148`, `host_interval_m` `:149`, `s_center_m` `:150`) |
| EnvelopeWall fields used in the mapping | `building_model.py:593-610` (`id` `:598`, `facade` `:599`, `from_m` `:600`, `to_m` `:601`, `length_m` `:602`, `height_m` `:603`, `construction_id` `:609`, `space_id` `:610`) |
| Level fields used in the mapping | `building_model.py:691-699` (`id` `:692`, `name` `:693`, `elevation_z_m` `:694`, `wall_height_m` `:695`) |
| Construction / material layers | `building_model.py:573-590` (`id` `:582`, `name` `:583`, `u_value_w_m2k` `:584`) |
 | Provenance record shape | `building_model.py:64-73` (`sheet_id`, `revision`, `method`, `confidence`, `bbox`, `note`) |
| Validation battery and export gate referenced by the Phase 1 gates | `validate/` (`invariants.py`, `conservation.py`, `export.py`, `gbxml.py`, `ashrae90_1.py`), `bem/export_gate.py` |
