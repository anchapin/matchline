"""Tests for datasets_adapter.py: adapter functions and dataset loaders."""

from __future__ import annotations

import json
import zipfile

import numpy as np
import pytest

from datasets_adapter import (
    ArchCADFormatError,
    DrawingScale,
    FloorPlanCADFormatError,
    Region,
    ScheduleEntry,
    box_to_polygon,
    load_aec_bench,
    load_archcad,
    load_floorplancad,
    normalize_crop,
    parse_lighting_schedule_csv,
    parse_schedule_csv,
    parse_schedule_table,
    scale_from_reference,
)

# --- parse_schedule_csv -------------------------------------------------------


class TestParseScheduleCSV:
    def test_valid_csv_file(self, tmp_path):
        csv = tmp_path / "schedule.csv"
        csv.write_text(
            "tag,category,width_m,height_m,note\n"
            "W1,window,1.2,1.5,Office front\n"
            "D1,door,0.9,2.1,Main entry\n",
            encoding="utf-8",
        )
        result = parse_schedule_csv(csv)
        assert "W1" in result
        assert result["W1"].width_m == 1.2
        assert result["W1"].height_m == 1.5
        assert result["W1"].note == "Office front"
        assert "D1" in result
        assert result["D1"].category == "door"

    def test_csv_with_string_path(self, tmp_path):
        csv = tmp_path / "schedule.csv"
        csv.write_text(
            "tag,category,width_m,height_m\nW1,window,1.0,1.0\n",
            encoding="utf-8",
        )
        result = parse_schedule_csv(str(csv))
        assert "W1" in result

    def test_csv_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_schedule_csv(tmp_path / "nonexistent.csv")

    def test_empty_csv_file(self, tmp_path):
        csv = tmp_path / "empty.csv"
        csv.write_text("", encoding="utf-8")
        result = parse_schedule_csv(csv)
        assert result == {}

    def test_csv_missing_optional_columns(self, tmp_path):
        csv = tmp_path / "minimal.csv"
        csv.write_text(
            "tag,category,width_m,height_m\nW1,window,1.0,\n",
            encoding="utf-8",
        )
        result = parse_schedule_csv(csv)
        assert result["W1"].width_m == 1.0
        assert result["W1"].height_m is None

    def test_csv_with_file_like_object(self, tmp_path):
        csv = tmp_path / "rows.csv"
        csv.write_text(
            "tag,category,width_m,height_m\nW1,window,2.0,2.0\n",
            encoding="utf-8",
        )
        with open(csv) as f:
            result = parse_schedule_csv(f)
        assert "W1" in result
        assert result["W1"].width_m == 2.0

    def test_csv_tag_normalization(self, tmp_path):
        csv = tmp_path / "normalize.csv"
        csv.write_text(
            "tag,category,width_m,height_m\n"
            "  w1  ,window,1.0,1.0\n"
            "W2 UP,window,2.0,2.0\n"
            "w3 with space,window,3.0,3.0\n",
            encoding="utf-8",
        )
        result = parse_schedule_csv(csv)
        assert "W1" in result
        assert "W2UP" in result
        assert "W3WITHSPACE" in result


# --- parse_schedule_table ----------------------------------------------------


class TestParseScheduleTable:
    def test_parse_schedule_table_exists(self):
        assert callable(parse_schedule_table)


# --- normalize_crop ---------------------------------------------------------


class TestNormalizeCrop:
    def test_returns_numpy_array(self):
        crop = np.zeros((100, 200, 3), dtype=np.uint8)
        result = normalize_crop(crop)
        assert isinstance(result, np.ndarray)

    def test_default_size_28(self):
        crop = np.zeros((100, 200, 3), dtype=np.uint8)
        result = normalize_crop(crop)
        assert result.shape[0] == 28 and result.shape[1] == 28

    def test_custom_size(self):
        crop = np.zeros((100, 200, 3), dtype=np.uint8)
        result = normalize_crop(crop, size=56)
        assert result.shape[0] == 56 and result.shape[1] == 56

    def test_grayscale_input(self):
        crop = np.zeros((100, 200), dtype=np.uint8)
        result = normalize_crop(crop)
        assert isinstance(result, np.ndarray)

    def test_pad_frac_applied(self):
        crop = np.zeros((100, 200, 3), dtype=np.uint8)
        result_default = normalize_crop(crop, size=28)
        result_no_pad = normalize_crop(crop, size=28, pad_frac=0.0)
        assert result_default.shape == result_no_pad.shape


# --- scale_from_reference ----------------------------------------------------


class TestScaleFromReference:
    def test_basic_scale_calculation(self):
        regions = [
            Region(
                category="wall",
                polygon_px=[(0, 0), (100, 0), (100, 10), (0, 10)],
                source="test",
            ),
        ]
        scale = scale_from_reference(regions, "wall", known_m=1.0)
        assert isinstance(scale, DrawingScale)
        assert scale.m_per_px is not None
        assert scale.m_per_px > 0

    def test_multiple_regions_aggregates(self):
        regions = [
            Region(
                category="wall",
                polygon_px=[(0, 0), (100, 0), (100, 10), (0, 10)],
                source="test",
            ),
            Region(
                category="wall",
                polygon_px=[(0, 0), (200, 0), (200, 10), (0, 10)],
                source="test",
            ),
        ]
        scale = scale_from_reference(regions, "wall", known_m=2.0)
        assert scale.m_per_px is not None

    def test_empty_regions_returns_note(self):
        regions = []
        scale = scale_from_reference(regions, "wall", known_m=1.0)
        assert isinstance(scale, DrawingScale)

    def test_unknown_category_returns_note(self):
        regions = [
            Region(
                category="unknown_type",
                polygon_px=[(0, 0), (100, 0), (100, 10), (0, 10)],
                source="test",
            ),
        ]
        scale = scale_from_reference(regions, "wall", known_m=1.0)
        assert isinstance(scale, DrawingScale)

    def test_custom_aggregation_function(self):
        regions = [
            Region(
                category="wall",
                polygon_px=[(0, 0), (100, 0), (100, 10), (0, 10)],
                source="test",
            ),
            Region(
                category="wall",
                polygon_px=[(0, 0), (200, 0), (200, 10), (0, 10)],
                source="test",
            ),
        ]
        import numpy as _np

        scale = scale_from_reference(regions, "wall", known_m=2.0, agg=_np.mean)
        assert isinstance(scale, DrawingScale)


# --- box_to_polygon --------------------------------------------------------


class TestBoxToPolygon:
    def test_box_returns_correct_polygon(self):
        result = box_to_polygon(10.0, 20.0, 110.0, 70.0)
        assert result == [(10.0, 20.0), (110.0, 20.0), (110.0, 70.0), (10.0, 70.0)]

    def test_box_integer_coords(self):
        result = box_to_polygon(0, 0, 100, 50)
        assert result == [(0, 0), (100, 0), (100, 50), (0, 50)]

    def test_box_with_none_returns_none_for_that_point(self):
        result = box_to_polygon(None, 0, 100, 50)
        assert result[0] == (None, 0)
        assert result[1] == (100, 0)

    def test_box_zero_width_and_height(self):
        result = box_to_polygon(0, 0, 0, 0)
        assert result == [(0, 0), (0, 0), (0, 0), (0, 0)]

    def test_box_with_float_coords(self):
        result = box_to_polygon(1.5, 2.5, 3.5, 4.5)
        assert result == [(1.5, 2.5), (3.5, 2.5), (3.5, 4.5), (1.5, 4.5)]


# --- parse_lighting_schedule_csv ---------------------------------------------


class TestParseLightingScheduleCSV:
    def test_valid_lighting_csv(self, tmp_path):
        csv = tmp_path / "lighting.csv"
        csv.write_text(
            "tag,description,lamp_type,watts,category,note\n"
            "L1,2x4 LED troffer,LED,40,lighting,Office\n"
            "L2,4ft linear,Linear Fluorescent,32,lighting,Corridor\n",
            encoding="utf-8",
        )
        result = parse_lighting_schedule_csv(csv)
        assert "L1" in result
        assert result["L1"].description == "2x4 LED troffer"
        assert result["L1"].lamp_type == "LED"
        assert result["L1"].watts == 40.0

    def test_lighting_csv_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            parse_lighting_schedule_csv(tmp_path / "nonexistent.csv")

    def test_lighting_csv_defaults_category_to_lighting(self, tmp_path):
        csv = tmp_path / "lighting.csv"
        csv.write_text(
            "tag,description,lamp_type,watts\nL1,LED troffer,LED,40\n",
            encoding="utf-8",
        )
        result = parse_lighting_schedule_csv(csv)
        assert result["L1"].category == "lighting"

    def test_lighting_csv_empty_file(self, tmp_path):
        csv = tmp_path / "empty.csv"
        csv.write_text("", encoding="utf-8")
        result = parse_lighting_schedule_csv(csv)
        assert result == {}


# --- Dataset loaders --------------------------------------------------------


class TestDatasetLoaders:
    def test_load_aec_bench_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_aec_bench(tmp_path)

    def test_load_floorplancad_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_floorplancad(tmp_path)

    def test_load_archcad_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_archcad(tmp_path)


# --- ArchCAD JSON loader (#660) -----------------------------------------------

# One slice in the dataset card's documented JSON layout: a door (two lines and
# a swing arc), a toilet given by class id, a chair (countable, no takeoff
# category), plus a wall and grid line that are not symbols.
_ARCHCAD_SLICE = [
    {"type": "LINE", "start": [10, 10], "end": [10, 40], "semantic": "single_door",
     "instance": "single_door_1"},
    {"type": "LINE", "start": [10, 10], "end": [40, 10], "semantic": "single_door",
     "instance": "single_door_1"},
    {"type": "ARC", "center": [10, 10], "radius": 30, "start_angle": 0, "end_angle": 90,
     "semantic": "single_door", "instance": "single_door_1"},
    {"type": "CIRCLE", "center": [100, 100], "radius": 5, "semantic": 9, "instance": "toilet_4"},
    {"type": "LINE", "start": [200, 0], "end": [220, 20], "semantic": "Chair",
     "instance": "chair_2"},
    {"type": "LINE", "start": [0, 0], "end": [300, 0], "semantic": "wall"},
    {"type": "LINE", "start": [0, 50], "end": [300, 50], "semantic": "axis_grid"},
]  # fmt: skip


def _write_archcad(root, primitives, name="slice_0001.json"):
    d = root / "json"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(primitives))
    return root


class TestLoadArchCAD:
    def test_symbols_and_takeoff_from_json(self, tmp_path):
        samples, takeoff = load_archcad(_write_archcad(tmp_path, _ARCHCAD_SLICE))
        assert sorted(s.label for s in samples) == ["Chair", "Single Door", "Toilet"]
        door = next(s for s in samples if s.label == "Single Door")
        assert door.image.shape == (28, 28)
        assert door.image.min() < 128 < door.image.max()  # ink on a light ground
        assert door.source == "archcad:slice_0001"
        assert door.bbox == pytest.approx((10, 10, 40, 40), abs=0.5)
        assert takeoff.counts == {"door": 1, "fixture": 1}
        assert takeoff.area_px2["fixture"] == pytest.approx(100.0, rel=0.05)
        assert takeoff.scale.m_per_px is None

    def test_reads_unextracted_json_zip(self, tmp_path):
        with zipfile.ZipFile(tmp_path / "json.zip", "w") as zf:
            zf.writestr("json/a.json", json.dumps(_ARCHCAD_SLICE))
            zf.writestr("json/b.json", json.dumps(_ARCHCAD_SLICE[:3]))
        samples, takeoff = load_archcad(tmp_path)
        assert len(samples) == 4
        assert takeoff.counts["door"] == 2

    def test_scale_and_max_samples(self, tmp_path):
        _write_archcad(tmp_path, _ARCHCAD_SLICE, "a.json")
        _write_archcad(tmp_path, _ARCHCAD_SLICE, "b.json")
        samples, takeoff = load_archcad(tmp_path, scale_m_per_px=0.01, max_samples=1)
        assert len(samples) == 3
        assert takeoff.area_m2["door"] == pytest.approx(900 * 1e-4, rel=0.05)

    def test_parquet_only_root_explains_the_release_format(self, tmp_path):
        (tmp_path / "train-00000-of-00001.parquet").write_bytes(b"")
        with pytest.raises(FileNotFoundError, match="not parquet"):
            load_archcad(tmp_path)

    def test_missing_root_fails_clearly(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="request access|Request access"):
            load_archcad(tmp_path / "nope")

    @pytest.mark.parametrize(
        "bad, match",
        [
            ({"type": "LINE", "start": [0, 0], "end": [1, 1], "instance": "x"}, "no 'semantic'"),
            ({"type": "LINE", "start": "0,0", "end": [1, 1], "semantic": 1, "instance": "d"},
             "must be \\[x, y\\] numbers"),
            ({"type": "LINE", "start": [0, 0], "semantic": 1, "instance": "d"}, "no 'end'"),
            ({"type": "CIRCLE", "center": [0, 0], "radius": "5", "semantic": 9, "instance": "t"},
             "must be a number"),
            ({"type": "LINE", "start": [0, 0], "end": [1, 1], "semantic": "spaceship",
              "instance": "s"}, "unknown semantic"),
            ({"type": "LINE", "start": [0, 0], "end": [1, 1], "semantic": 1, "instance": [1]},
             "needs an 'instance'"),
        ],
    )  # fmt: skip
    def test_bad_primitive_raises_not_zero_count(self, tmp_path, bad, match):
        _write_archcad(tmp_path, _ARCHCAD_SLICE + [bad])
        with pytest.raises(ArchCADFormatError, match=match):
            load_archcad(tmp_path)

    def test_countable_primitive_without_instance_is_skipped(self, tmp_path):
        # Real ArchCAD files leave some countable primitives (piles) with
        # no instance id; they are not symbols and must not raise.
        _write_archcad(tmp_path / "a", _ARCHCAD_SLICE)
        n_base = len(load_archcad(tmp_path / "a")[0])
        loose = {"type": "LINE", "start": [0, 0], "end": [1, 1], "semantic": 1, "instance": None}
        _write_archcad(tmp_path / "b", _ARCHCAD_SLICE + [loose])
        assert len(load_archcad(tmp_path / "b")[0]) == n_base

    def test_non_list_json_raises(self, tmp_path):
        _write_archcad(tmp_path, {"shapes": []})
        with pytest.raises(ArchCADFormatError, match="list of primitives"):
            load_archcad(tmp_path)


# --- Region and ScheduleEntry dataclasses -----------------------------------


class TestRegionDataclass:
    def test_region_with_required_fields(self):
        region = Region(
            category="wall",
            polygon_px=[(0, 0), (10, 0), (10, 5), (0, 5)],
            source="test_source",
        )
        assert region.category == "wall"
        assert region.polygon_px == [(0, 0), (10, 0), (10, 5), (0, 5)]
        assert region.source == "test_source"
        assert region.drawing_type == "floor_plan"

    def test_region_with_all_fields(self):
        region = Region(
            category="door",
            polygon_px=[(0, 0), (10, 0), (10, 5), (0, 5)],
            source="test",
            drawing_type="elevation",
            label="Double door",
        )
        assert region.label == "Double door"
        assert region.drawing_type == "elevation"


class TestScheduleEntryDataclass:
    def test_schedule_entry_required_fields(self):
        entry = ScheduleEntry(tag="W1", category="window", width_m=1.0, height_m=1.5)
        assert entry.tag == "W1"
        assert entry.category == "window"
        assert entry.width_m == 1.0
        assert entry.height_m == 1.5

    def test_schedule_entry_defaults(self):
        entry = ScheduleEntry(tag="W1", category="window", width_m=1.0, height_m=1.5)
        assert entry.note == ""
        assert entry.watts is None
        assert entry.description == ""
        assert entry.lamp_type == ""

    def test_schedule_entry_lighting(self):
        entry = ScheduleEntry(
            tag="L1",
            category="lighting",
            width_m=None,
            height_m=None,
            watts=40.0,
            description="2x4 LED troffer",
            lamp_type="LED",
        )
        assert entry.watts == 40.0
        assert entry.description == "2x4 LED troffer"


class TestDrawingScale:
    def test_drawing_scale_with_m_per_px(self):
        scale = DrawingScale(m_per_px=0.01, note="test scale")
        assert scale.m_per_px == 0.01
        assert scale.note == "test scale"

    def test_drawing_scale_defaults(self):
        scale = DrawingScale(m_per_px=None)
        assert scale.m_per_px is None
        assert scale.note == ""


# ---------------------------------------------------------------------------
# OCR tag extraction (#668)
# ---------------------------------------------------------------------------


def _ocr_det(bbox=(100, 100, 140, 140), tag=""):
    from datasets_adapter import Detection

    return Detection(label="Window", tag=tag, score=0.9, bbox=bbox, source="t")


def _fake_reader(reads, calls=None):
    """reads: list of (text, conf, box in SHEET px); returned relative to the crop."""

    def read(crop):
        if calls is not None:
            calls.append(crop.shape)
        return list(reads)

    return read


class TestExtractWithTags:
    IMG = np.full((400, 400), 255.0)

    def _run(self, dets, reads, **kw):
        from datasets_adapter import extract_with_tags

        # crop origin for the default bbox (100,100,140,140), pad 1.0 x 40 px = (60, 60)
        rel = [(t, c, (b[0] - 60, b[1] - 60, b[2] - 60, b[3] - 60)) for t, c, b in reads]
        return extract_with_tags(dets, self.IMG, reader=_fake_reader(rel), **kw)

    def test_adjacent_tag_fills_tag_and_score(self):
        out = self._run([_ocr_det()], [("w-1", 0.82, (145, 110, 170, 125))])
        assert out[0].tag == "W-1" and out[0].tag_score == pytest.approx(0.82)

    def test_nearest_tag_wins(self):
        reads = [("D2", 0.99, (60, 60, 75, 70)), ("A", 0.7, (142, 115, 150, 125))]
        assert self._run([_ocr_det()], reads)[0].tag == "A"

    def test_schedule_tag_preferred_over_nearer_read(self):
        reads = [("X9", 0.95, (142, 115, 150, 125)), ("W-2", 0.6, (60, 60, 80, 70))]
        out = self._run([_ocr_det()], reads, schedule_tags={"W-2"})
        assert out[0].tag == "W-2"

    def test_non_tag_text_and_low_conf_ignored(self):
        reads = [("OFFICE", 0.99, (142, 110, 190, 125)), ("B", 0.1, (142, 110, 150, 125))]
        out = self._run([_ocr_det()], reads)
        assert out[0].tag == "" and out[0].tag_score is None

    def test_tagged_detections_untouched_and_not_read(self):
        from datasets_adapter import extract_with_tags

        calls = []
        d = _ocr_det(tag="A")
        out = extract_with_tags([d], self.IMG, reader=_fake_reader([("Z", 1, (0, 0, 1, 1))], calls))
        assert out == [d] and calls == []

    def test_low_confidence_read_lands_in_tag_review(self):
        from datasets_adapter import ScheduleEntry, rollup_takeoff

        out = self._run([_ocr_det()], [("A", 0.4, (142, 110, 150, 125))])
        sched = {"A": ScheduleEntry(tag="A", category="window", width_m=1.0, height_m=1.0)}
        res = rollup_takeoff(out, sched)
        assert res.counts == {"window": 1} and res.tag_review == out

    def test_missing_backend_leaves_tags_empty_and_warns(self, monkeypatch, caplog):
        import sys

        from datasets_adapter import extract_with_tags

        monkeypatch.setitem(sys.modules, "pytesseract", None)
        monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)
        d = _ocr_det()
        with caplog.at_level("WARNING", logger="datasets_adapter"):
            out = extract_with_tags([d], self.IMG, config={"datasets": {"ocr_backend": "auto"}})
        assert out == [d] and out[0].tag == ""
        assert "no OCR backend available" in caplog.text

    def test_backend_none_skips_ocr_quietly(self, caplog):
        from datasets_adapter import extract_with_tags

        with caplog.at_level("WARNING", logger="datasets_adapter"):
            out = extract_with_tags([_ocr_det()], self.IMG, backend="none")
        assert out[0].tag == "" and caplog.text == ""

    def test_unknown_backend_raises(self):
        from datasets_adapter import extract_with_tags

        with pytest.raises(ValueError, match="ocr_backend"):
            extract_with_tags([_ocr_det()], self.IMG, config={"datasets": {"ocr_backend": "x"}})


def test_load_datasets_config_precedence(tmp_path, caplog):
    from datasets_adapter import load_datasets_config

    pp = tmp_path / "pyproject.toml"
    pp.write_text('[tool.matchline.datasets]\nocr_backend = "rapidocr"\nocr_pad_frac = 2\n')
    cfg = load_datasets_config(pyproject=pp)
    assert cfg == {"ocr_backend": "rapidocr", "ocr_pad_frac": 2.0, "ocr_min_conf": 0.3}
    with caplog.at_level("WARNING", logger="datasets_adapter"):
        cfg = load_datasets_config(
            {"datasets": {"ocr_backend": "pytesseract", "bogus": 1}}, pyproject=pp
        )
    assert cfg["ocr_backend"] == "pytesseract" and cfg["ocr_pad_frac"] == 2.0
    assert "bogus" in caplog.text


def test_extract_with_tags_real_backend_reads_rendered_tag():
    """Synthetic sheet: a window symbol with "W-1" printed beside it."""
    from PIL import Image, ImageDraw, ImageFont

    from datasets_adapter import extract_with_tags, get_ocr_reader

    name, reader = get_ocr_reader("auto")
    if reader is None:
        pytest.skip("no OCR backend installed (pytesseract+tesseract or rapidocr)")
    img = Image.new("L", (400, 240), 255)
    dr = ImageDraw.Draw(img)
    dr.rectangle((60, 90, 160, 130), outline=0, width=3)  # window symbol
    dr.line((60, 110, 160, 110), fill=0, width=2)
    dr.text((185, 92), "W-1", fill=0, font=ImageFont.load_default(size=32))
    out = extract_with_tags(
        [_ocr_det(bbox=(60, 90, 160, 130))], np.asarray(img, dtype=float), reader=reader
    )
    assert out[0].tag == "W-1", f"{name} read {out[0].tag!r}"
    assert 0 < out[0].tag_score <= 1


# --- FloorPlanCAD parquet loader (#683) -------------------------------------


def _fpcad_png(side=100):
    import io

    from PIL import Image, ImageDraw

    img = Image.new("L", (side, side), 255)
    ImageDraw.Draw(img).rectangle((10, 10, 30, 30), outline=0, width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _write_fpcad(path, rows, drop=None):
    import pyarrow as pa
    import pyarrow.parquet as pq

    cols = {
        "image": [{"bytes": _fpcad_png(), "path": f"{r[0]}.png"} for r in rows],
        "image_id": [r[0] for r in rows],
        "objects": [
            {
                "id": [str(i) for i in range(len(r[1]))],
                "bbox": [b for _, b in r[1]],
                "category": [c for c, _ in r[1]],
            }
            for r in rows
        ],
    }
    if drop:
        cols.pop(drop)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols), str(path))


class TestFloorPlanCADParquet:
    ROW = (
        "img0",
        [
            ("single_door", [100.0, 100.0, 300.0, 300.0]),
            ("window", [500.0, 400.0, 700.0, 450.0]),
            ("wall", [0.0, 0.0, 1000.0, 50.0]),
            ("class_31", [600.0, 600.0, 800.0, 900.0]),
        ],
    )

    def test_loads_symbols_and_regions_from_hf_layout(self, tmp_path):
        _write_fpcad(tmp_path / "data" / "train-00000-of-00001.parquet", [self.ROW])
        samples, takeoff = load_floorplancad(tmp_path, size=16)
        # wall is "stuff": no sample; unnamed class_31 kept as a sample only
        assert [s.label for s in samples] == ["single_door", "window", "class_31"]
        assert samples[0].source == "floorplancad:img0"
        # 0..1000 frame scaled to the 100 px image
        assert samples[0].bbox == pytest.approx((10.0, 10.0, 30.0, 30.0))
        assert samples[0].image.shape == (16, 16)
        assert takeoff.counts == {"door": 1, "window": 1}
        assert takeoff.area_px2["door"] == pytest.approx(400.0)
        assert takeoff.area_m2 == {}

    def test_max_samples_caps_rows(self, tmp_path):
        rows = [("a", self.ROW[1][:1]), ("b", self.ROW[1][:1]), ("c", self.ROW[1][:1])]
        _write_fpcad(tmp_path / "train-00000-of-00001.parquet", rows)
        samples, _ = load_floorplancad(tmp_path, max_samples=2)
        assert [s.source for s in samples] == ["floorplancad:a", "floorplancad:b"]

    def test_missing_column_is_a_format_error(self, tmp_path):
        _write_fpcad(tmp_path / "x.parquet", [self.ROW], drop="objects")
        with pytest.raises(FloorPlanCADFormatError, match="objects"):
            load_floorplancad(tmp_path)

    def test_malformed_bbox_is_a_format_error(self, tmp_path):
        _write_fpcad(tmp_path / "x.parquet", [("img0", [("window", [1.0, 2.0, 3.0])])])
        with pytest.raises(FloorPlanCADFormatError, match="bbox"):
            load_floorplancad(tmp_path)
