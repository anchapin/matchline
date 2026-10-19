# Public API reference (gate #1167)

Every public symbol matchline ships, generated from the same surface
walk that pins `tests/api_surface.json` (schema
`matchline.api_surface/1`). Regenerate with
`python3 scripts/generate_api_surface.py`; the CI guard keeps this
page in sync with the pins. What counts as public, breaking, or
deprecated is in [api.md](api.md); per-module usage is in each
module's guide under `docs/`.

## `bem_export`

- `centroid(sp: BEMSpace)`
- `validate_gbxml(path: str | Path, xsd_path: str | Path=SCHEMA_PATH)`
- `write_gbxml(model: BEMModel, path: str | Path)`

## `building_model`

- `space_from_dict(d: dict)`
- class `AirLoop`
- class `BimElement`
- class `BimOpening`
- class `BuildingModel`
- class `ComponentRef`
- class `Construction`
- class `DaylitZone`
- class `EnvelopeWall`
- class `FixtureInstance`
- class `Level`
- class `OpeningAttachmentSummary`
- class `Provenance`
- class `ReviewItem`
- class `RevisionEvent`
- class `RoofPlane`
- class `ShadingSurface`
- class `Space`
- class `SpaceAdjacency`
- class `SpaceDaylight`
- class `SpaceHVAC`
- class `SpaceLighting`
- class `SpaceOpening`
- class `SpaceUse`
- class `SymbolLinkage`
- class `Zone`

## `cli`

- `build_parser()`
- `check_range_0_1(value: str)`
- `cmd_bem_export(args: argparse.Namespace)`
- `cmd_demo(args: argparse.Namespace)`
- `cmd_diff(args: argparse.Namespace)`
- `cmd_elevation_windows(args: argparse.Namespace)`
- `cmd_export_package(args: argparse.Namespace)`
- `cmd_facade_takeoff(args: argparse.Namespace)`
- `cmd_feedback(args: argparse.Namespace)`
- `cmd_ifc_export(args: argparse.Namespace)`
- `cmd_ifc_import(args: argparse.Namespace)`
- `cmd_index(args: argparse.Namespace)`
- `cmd_ingest(args: argparse.Namespace)`
- `cmd_learn_legend(args: argparse.Namespace)`
- `cmd_legend_eval(args: argparse.Namespace)`
- `cmd_mnist(args: argparse.Namespace)`
- `cmd_multidiscipline(args: argparse.Namespace)`
- `cmd_review(args: argparse.Namespace)`
- `cmd_room_labels(args: argparse.Namespace)`
- `cmd_run(args: argparse.Namespace)`
- `cmd_scale(args: argparse.Namespace)`
- `cmd_schedules(args: argparse.Namespace)`
- `cmd_symbols(args: argparse.Namespace)`
- `cmd_takeoff_diff(args: argparse.Namespace)`
- `cmd_validate(args: argparse.Namespace)`
- `cmd_viewer(args: argparse.Namespace)`
- `cmd_viewer_apply(args: argparse.Namespace)`
- `cmd_walls(args: argparse.Namespace)`
- `main(argv: list[str] | None=None)`

## `construction_library`

- `apply_construction_library(model, climate_zone: str='', category: str='Nonresidential')`
- `baseline_envelope(model, climate_zone: str, category: str)`
- `classify(text: str, surface: str)`
- `climate_zone_number(cz: str)`
- `heated_slab_evidence(equipment)`
- `heated_slab_rooms(equipment)`
- `lookup(surface: str, construction_type: str, climate_zone: str, category: str='Nonresidential')`
- `prm_rows(surface: str, construction_type: str, climate_zone: str, category: str)`
- class `LibraryRow`
- class `LibrarySummary`

## `corrections`

- `empty_doc()`
- `field_key(target: Optional[dict], field: str)`
- `load(model_path)`
- `path_for(model_path)`
- `read(path)`
- `record(model, item, action: str, source: str, model_path, field: str='', before=None, after=None, state_before: Optional[dict]=None, doc: Optional[dict]=None)`
- `revert_to_automatic(model, model_path, key: str, doc: Optional[dict]=None)`
- `save(model_path, doc: dict)`
- `summary(doc: dict)`
- `undo(model, model_path, n: int=1, doc: Optional[dict]=None)`

## `datasets_adapter`

- `box_to_polygon(xtl, ytl, xbr, ybr)`
- `detections_from_regions(regions: list[Region], score: float=1.0)`
- `detections_from_yolo_json(yolo_json_path: str | Path, source: str)`
- `extract_with_tags(detections: list[Detection], image: np.ndarray, config: dict | None=None, backend: str | None=None, schedule_tags=None, reader=None)`
- `get_ocr_reader(backend: str='auto')`
- `load_aec_bench(root: str | Path, dpi: int=200, size: int=28, scale_m_per_px: float | None=None, drawing_type: str='floor_plan')`
- `load_archcad(root: str | Path, size: int=28, scale_m_per_px: float | None=None, max_samples: int | None=None)`
- `load_datasets_config(config: dict | None=None, pyproject: Path | None=None)`
- `load_floorplancad(root: str | Path, size: int=28, scale_m_per_px: float | None=None, max_samples: int | None=None)`
- `measure_takeoff(regions: list[Region], drawing_type: str, scale: DrawingScale)`
- `normalize_crop(crop: np.ndarray, size: int=28, pad_frac: float=0.15)`
- `normalize_tag(tag)`
- `parse_lighting_schedule_csv(path_or_rows)`
- `parse_schedule_csv(path_or_rows)`
- `parse_schedule_table(sheet_image: np.ndarray)`
- `polygon_area_px2(poly)`
- `rollup_takeoff(detections: list[Detection], schedule: dict[str, ScheduleEntry], drawing_type: str='floor_plan', tag_review_below: float=TAG_REVIEW_THRESHOLD)`
- `scale_from_reference(regions: list[Region], category: str, known_m: float, agg=np.median)`
- class `ArchCADFormatError`
- class `Detection`
- class `DetectorOutputValidationError`
- class `DrawingScale`
- class `FloorPlanCADFormatError`
- class `Region`
- class `ScheduleEntry`
- class `SymbolSample`
- class `TakeoffLine`
- class `TakeoffResult`

## `detection_provider`

- `license_class(artifact: str, ledger_path: Union[str, Path]=LEDGER)`
- `opening_segments(plan: Optional[dict])`
- `provider_from_config(cfg: Union[None, dict, str, Path], release: bool=False)`
- `thin_gap_segments(plan: Optional[dict])`
- `window_detections(walls: dict, height_pt: float, px_per_pt: float, source: str)`
- class `CombinedProvider(self, parts: List[DetectionProvider])`
- class `DetectionProvider(self, artifact: str='', ledger_path: Union[str, Path]=LEDGER)`
- class `DoorSwingProvider(self, px_per_m: Optional[float]=None, ink_max: Optional[int]=None, min_width_m: Optional[float]=None, max_width_m: Optional[float]=None)`
- class `LabeledProvider(self, inner: DetectionProvider, labels: List[str])`
- class `NoneProvider(self, **_kw)`
- class `PrecomputedProvider(self, dir: Union[str, Path], artifact: str='', **kw)`
- class `ProviderConfigError`
- class `ProviderInfo`
- class `ProviderLicenseError`
- class `ScratchProvider(self, weights: Union[str, Path], artifact: str='', conf: float=0.25, device: str='cpu', **kw)`
- class `VectorGlazingProvider(self)`
- class `YoloSahiProvider(self, weights: Union[str, Path], artifact: str='', tile: int=1024, overlap: float=0.2, conf: float=0.25, device: str='cpu', **kw)`

## `drawing_scale`

- `find_dimensions(sheet)`
- `find_notes(sheet)`
- `find_scale_bars(sheet)`
- `normalize(s: str)`
- `parse_length_m(s: str)`
- `parse_scale_note(s: str)`
- `scale_at(scale: SheetScale, x: float, y: float)`
- `scale_sheets(sheet_dir)`
- `sheet_scale(sheet)`
- class `Evidence`
- class `SheetScale`

## `elevation_windows`

- `adjudicate_tag(candidates: list[WindowTagCandidate], prefer: str='grid')`
- `assign_window_tag(width_m: float, height_m: float, win_sched: dict, tol_m: float=0.15)`
- `assign_window_tag_candidates(width_m: float, height_m: float, win_sched: dict, tol_m: float=0.15, registration_method: str='unknown')`
- `attach_merged_windows(model: BuildingModel, spaces: list, bldg: dict, merged: list, win_sched: dict, facade: str)`
- `build_model_with_elevations(bldg: dict, elevation_keys: tuple=('elev_grid', 'elev_nogrid'), building_name: str='', detector_backend: str='contour', daylight_params: DaylightParams=None)`
- `compute_daylit_zones(space, W: float, D: float, params: DaylightParams=None)`
- `detect_windows(image, px_per_m: float, sheet_id: str, revision: int, backend: str='contour')`
- `keep_both(a: WindowTagCandidate, b: WindowTagCandidate)`
- `link_elevations(model: BuildingModel, bldg: dict, spaces: list, elevation_keys: tuple, win_sched: dict, detector_backend: str='contour', daylight_params: DaylightParams=None, dedup_tol_m: float=0.3)`
- `merge_elevation_observations(fw_lists: list, tol_m: float=0.3, min_overlap_frac: float=0.5)`
- `observations_to_facade(obs_list: list, reg: FacadeRegistration, facade: str)`
- `prefer_geometric(a: WindowTagCandidate, b: WindowTagCandidate)`
- `prefer_grid(a: WindowTagCandidate, b: WindowTagCandidate)`
- `reconcile_window_counts(model: BuildingModel, plan_counts: dict, merged: list, facade: str, plan_sheet_id: str)`
- `register_backend(name: str, backend: WindowDetectorBackend)`
- `same_object(a: WindowTagCandidate, b: WindowTagCandidate)`
- class `ContourWindowDetector(self, min_area_m2: float=0.4, max_area_m2: float=12.0, aspect_range: tuple=(0.3, 4.0), min_fill_ratio: float=0.85, confidence: float=0.9)`
- class `DaylightParams`
- class `ElevationLinkReport`
- class `ElevationWindowObs`
- class `FacadeWindow`
- class `MergedWindow`
- class `WindowDetectorBackend`
- class `WindowTagCandidate`
- class `YOLOWindowDetectorBackend(self, weights: str | Path='detector/best.pt', conf: float=0.25, iou_thr: float=0.5, tile: int=1024, overlap: float=0.2, imgsz: int=1024, device: str='cpu')`

## `export_package`

- `build_package(out_dir: Path, model, report, convention: dict, files: Iterable[Path], inputs: Iterable[dict]=(), pdf: bool=True, review_dir: Optional[Path]=None, require_gate: bool=False)`
- `build_trust_report(model, report, convention: dict, inputs: Iterable[dict]=(), review_dir=None, corrections: Optional[dict]=None, feedback: Optional[dict]=None)`
- `export_zip(out_dir: Path)`
- `fmt(v)`
- `render_html(tr: dict, banner: str='')`
- `review_viewer_scene(model, corrections_doc: Optional[dict]=None)`
- `sha256(path: Path)`
- `write_pdf(tr: dict, path: Path)`
- `zip_package(pkg: Path, zip_path: Path)`

## `facade_takeoff`

- `class_counts(mask: np.ndarray)`
- `dataset_priors(takeoffs: list[FacadeTakeoff])`
- `facade_bbox(mask: np.ndarray)`
- `facade_takeoff(mask: np.ndarray, facade_id: str, width_m: float | None=None, height_m: float | None=None, sheet_id: str | None=None)`
- `iter_facades(root: str | Path)`
- `load_mask(path: str | Path)`
- `parse_xml_boxes(xml_path: str | Path)`
- `rasterize_boxes(boxes: list[tuple[int, float, float, float, float]], H: int, W: int)`
- `sweep(root: str | Path, with_xml: bool=True)`
- `xml_agreement(mask: np.ndarray, boxes: list[tuple[int, float, float, float, float]], slop_frac: float=0.02)`
- class `FacadeTakeoff`

## `geometry_simplify`

- `envelope_area(ring, wall_height: float | None=None)`
- `footprint_from_regions(polygons: list[list])`
- `footprint_parts_from_regions(polygons: list[list])`
- `ring_edges(ring)`
- `ring_perimeter(ring)`
- `simplify_footprint(parts: list[FootprintPart], tol: float=0.02, wall_height: float | None=None, **kwargs)`
- `simplify_report(res: SimplifyResult)`
- `simplify_ring(ring, tol: float=0.02, min_feature: float | None=None, wall_height: float | None=None, dp_eps: float | None=None, max_single_step: float=0.0025)`
- class `FootprintPart`
- class `FootprintSimplifyResult`
- class `SimplifyResult`

## `grid_detect`

- `detect_grids(sheet, sheet_id: str='')`
- `elevation_bubbles(gs: GridSet, px_per_pt: float=1.0)`
- `plan_grid_m(gs: GridSet, orient: str, m_per_pt: float, origin_pt: float=0.0, flip: bool=False)`
- class `Bubble`
- class `GridLine`
- class `GridSet`

## `hvac_trace`

- `detect_components(gray: np.ndarray, templates: dict, clf: WisardClassifier)`
- `detect_legend_symbols(gray: np.ndarray, templates: list, exclude_px=())`
- `duct_skeleton(gray: np.ndarray)`
- `extract_zones(skel: np.ndarray, detections: list, rooms: list, px_per_m: float=PX_PER_M)`
- `main()`
- `merge_legend_detections(builtin: list, legend: list, legend_classes)`
- `ncc_locate(gray: np.ndarray, tmpl: np.ndarray, thresh: float=NCC_THRESH)`
- `suppress_vav_near_terminals(detections)`
- `template_bank(tmpl: np.ndarray, scales=(1.0,))`
- `trace_sheet(gray: np.ndarray, gt: dict, clf: WisardClassifier, templates: dict, provenance: Provenance | None=None)`
- `trace_symbols_to_terminals(gray: np.ndarray, dets: list, term_rects_px: list, assoc_px: int=ASSOC_PX, seed_pad: int=10)`
- `validate_hvac_solution(seeds, clf, templates, realistic: bool=False)`

## `ifc_export`

- `export_ifc(model_path: str, out_path: str)`

## `ifc_import`

- `import_ifc(path, sheet_id=None, revision=1)`

## `jesse`

- `com_normalize(img: np.ndarray, size: int=28)`
- `complex_invariant_review_item(inv: dict, glyph_name: str, provenance: object)`
- `is_complex_invariant(inv: dict)`
- `make_tuple_indices(h: int, w: int, seed: int=42)`
- `skeleton_invariants(skel: np.ndarray)`
- `tag_detections(detections: list, image: np.ndarray, **kwargs)`
- `thermometer_encode(img: np.ndarray)`
- `thermometer_encode_batch(images: np.ndarray)`
- `tuple_addresses(bitvecs: np.ndarray, tuple_idx: np.ndarray)`
- `zhang_suen(binary: np.ndarray)`
- class `WisardClassifier(self, n_classes: int, tuple_idx: np.ndarray | None=None, h: int=28, w: int=28, seed: int=42)`

## `legend_learning`

- `add_tags(tags: Dict[str, str], pairs)`
- `eval_before_after(before: dict, after: dict)`
- `learn_from_set_report(report: dict)`
- `load_tags(path)`
- `normalize(description: str)`
- `save_tags(path, tags: Dict[str, str])`
- `suggest_tags(report: dict, max_suggestions: int=3)`
- class `LegendTagsError`

## `lighting`

- `assign_fixtures_to_spaces(detections: list[Detection], spaces: list[LabeledSpace])`
- `lighting_takeoff(detections: list[Detection], schedule: dict[str, ScheduleEntry], spaces: list[LabeledSpace], scale: DrawingScale, space_areas_m2: list[float | None] | None=None, drawing_type: str='floor_plan')`
- `summarize_lighting(res: LightingResult)`
- class `LightingLine`
- class `LightingResult`
- class `RoomLighting`

## `mech_schedule`

- `classify(tag: str, type_name: str, type_description: str, component_description: str='')`
- `link_to_model(systems: List[MechSystem], model: Any)`
- `parse_capacity(text: str)`
- `parse_cobie_workbook(path: 'str | Path')`
- `parse_tag(name: str)`
- class `MechScheduleResult`
- class `MechScheduleRow`
- class `MechSystem`

## `pdf_ingest`

- `ingest_pdf(path, out_dir=None, dpi: float=DEFAULT_DPI, rasterize: bool=True)`
- `load_sheet(path)`
- `page_to_sheet_matrix(x0: float, y0: float, w: float, h: float, rotation: int)`
- class `ImageRef`
- class `IngestError`
- class `IngestResult`
- class `Primitive`
- class `Sheet`
- class `TextSpan`

## `pdf_schedules`

- `extract_schedules(sheet: dict, sheet_id: Optional[str]=None)`
- `schedules_for_sheets(sheets_dir, sheet_ids: Optional[Dict[str, str]]=None)`
- class `PdfSchedule`

## `plan_walls`

- `compare_rooms(pred: Sequence[Sequence[Pt]], truth: Sequence[Sequence[Pt]], min_iou=0.5)`
- `extract_walls(sheet, m_per_pt: Optional[float], scale_review: Optional[dict]=None)`
- `wall_length_error(pred_m: float, truth_m: float)`
- `walls_for_sheets(sheet_dir)`
- class `Band`
- class `Bridge`
- class `PlanWalls`
- class `Room`
- class `Seg`
- class `Wall`

## `polygon_classify`

- `classify_polygons(rooms: list, south_windows: list, grids_h: dict)`
- class `PolyClassEvidence`
- class `PolyClassification`

## `real_set`

- `build_set_model(pdf, out_dir, *, detections: Optional[dict]=None, storey_height_m=None, dpi: float=150, provider=None, legend_tags: Optional[dict]=None)`
- class `SetError`
- class `SetReport`
- class `SheetStatus`

## `registration`

- `assign_points_to_spaces(points: list, spaces: list)`
- `interval_overlap(a0: float, a1: float, b0: float, b1: float)`
- `match_interval_to_segments(s0: float, s1: float, segments: list)`
- `register_elevation_geometric(sheet_id: str, facade: Facade, wall_u0_px: float, elev_px_per_m: float, v_ground_px: float, revision: int, mirrored: bool | None=None)`
- `register_elevation_grid(sheet_id: str, facade: Facade, plan_grid_m: dict, elev_bubbles: list, v_ground_px: float, elev_px_per_m: float, revision: int)`
- class `Affine2D`
- class `Facade`
- class `FacadeRegistration`
- class `PlanRegistration`

## `review_report`

- `apply_decisions(model, doc: dict, model_sha: Optional[str]=None, source: str='human', corrections_path=None)`
- `attach_takeoff_diff_items(model, diff: dict)`
- `auto_state(item)`
- `check(doc: dict, ids)`
- `decide(model, item_id: str, action: str, value: Optional[str]=None, source: str='human', corrections_path=None)`
- `edit_field(model, item)`
- `final_states(doc: dict)`
- `item_links(model, sheets: List[dict])`
- `now_iso()`
- `original_value(model, item)`
- `render_html(model, doc: dict, sheets: Optional[List[dict]]=None)`
- `sha256_text(text: str)`
- `sheet_overlays(out_dir, model)`
- `summarize(summary: dict)`
- `template(model, model_sha: str)`
- `worst_first(items)`
- `write_review(out_dir: Path, model)`

## `review_viewer`

- `build_scene(model, corrections_doc: Optional[dict]=None)`
- `fragment(scene: dict, banner: str='')`
- `mep_scene(model, space_level: dict, level_of_space: dict)`
- `page_html(scene: dict, title: str='matchline review viewer')`

## `room_labels`

- `assign_labels(spaces: list[LabeledSpace], labels: list[RoomLabel], max_nearest_px: float=150.0)`
- `attach_room_labels(takeoff_result, sheet_image: np.ndarray | None=None, min_conf: float=0.3, max_nearest_px: float=150.0, sheet_id: str | None=None, revision: str | None=None)`
- `detect_text(image: np.ndarray, min_conf: float=0.3)`
- `get_ocr_engine()`
- `label_spaces_from_sheet(sheet_image: np.ndarray, spaces: list[LabeledSpace] | None, min_conf: float=0.3, max_nearest_px: float=150.0, pre_detected: list[TextBox] | None=None)`
- `looks_like_dimension(text: str)`
- `ocr_engine_name()`
- `parse_room_label(text: str)`
- `point_in_polygon(pt, poly)`
- `point_to_polygon_dist(pt, poly)`
- `synthesize_test_plan(seed: int=7)`
- `to_room_label(tb: TextBox)`
- class `LabeledSpace`
- class `LabeledTakeoff`
- class `RoomLabel`
- class `TextBox`

## `run_pipeline`

- `main(args, config: dict | None=None)`
- `model_from_linked_model(model, simplified_ring: list, wall_height_m: float, simplify_tolerance: float)`
- `parse_args()`
- `write_json(p: Path, data: Any)`

## `run_review`

- `format_review_list(model_path: str | pathlib.Path, show_all: bool=False)`
- `list_review_items(model_path: str | pathlib.Path, show_all: bool=False)`
- `main(args: argparse.Namespace | None=None)`
- class `SecurityError`

## `safe_xml`

- `is_safe_parser(module_name: str)`
- `safe_xml_fromstring(text: bytes, max_nodes: Optional[int]=None)`
- `safe_xml_parse(path: str | Path, max_nodes: Optional[int]=None)`
- `safe_xml_parser()`

## `sheet_index`

- `build_index(sheets: Sequence, files: Optional[Sequence[str]]=None)`
- `classify_title(title: str)`
- `find_drawing_index(spans: List[Span], exclude: Optional[Span]=None)`
- `find_sheet_number(spans: List[Span], w: float, h: float)`
- `find_title(spans: List[Span], number: Optional[Span], w: float, h: float)`
- `index_sheets(sheet_dir)`
- `parse_level(title: str)`
- `parse_sheet_number(s: str)`
- `read_title_block(sheet, file: Optional[str]=None)`
- class `SheetEntry`
- class `SheetIndex`
- class `Span`

## `sim_smoke`

- `annual(openstudio: str, osm: Path, epw: Path, out: Path, timeout: int)`
- `judge(err: dict, res: dict, ref: dict, data: dict)`
- `load_reference(path, building_type, template, climate_zone)`
- `main(argv=None)`
- `read_err(path: str | Path)`
- `read_sql(path: str | Path)`
- `wall_check(wall_seconds: float | None, budget: int | None)`

## `space_merge`

- `check_merge(model: BuildingModel, source_id: str, target_id: str)`
- `cuts_equal(cut: dict, parsed: dict)`
- `merge_closets_and_shafts(model: BuildingModel, doors: Optional[Iterable[dict]]=None, skip_ids: Iterable[str]=())`
- `merge_into(model: BuildingModel, source_id: str, target_id: str)`
- `parse_split(raw: str)`
- `split_space(model: BuildingModel, source_id: str, a, b)`
- `un_merge(model: BuildingModel, snap: dict)`
- `un_split(model: BuildingModel, snap: dict)`
- `validate_split(model: BuildingModel, source_id: str, raw: str)`
- class `KeptRecord`
- class `MergeRecord`
- class `MergeResult`

## `space_use_defaults`

- `apply_space_use_defaults(model)`
- `classify_space_type(name: str, poly_type: str='room')`
- `lighting_watts(space)`
- `schedule_profile(name: str)`
- class `SpaceUseSummary`

## `symbols`

- `draw(cls: int, thick: float=7.0)`
- `jitter(img: np.ndarray, rng: np.random.Generator)`
- `make_dataset(n_train: int, n_test: int, seed: int)`
- `to28(img: np.ndarray)`

## Package `link`


## Package `validate`

- `export_gate(report: ValidationReport, model: BuildingModel | None=None, min_review_confidence: float | None=None)`
- `run_checks(model, gbxml_path=None, ifc_path=None, sres=None, tol_area: float=0.03, tol_volume: float=0.03, tol_envelope: float=0.01, lpd_warn_max: float=25.0, opening_eps: float=0.005, min_review_confidence: float | None=None, hvac_unzoned_warn_frac: float=0.05, cross_level_dedup_warn_frac: float=0.01, opening_attachment_warn_frac: float=0.0)`

