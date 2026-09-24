"""Tests for detector output schema validation in detections_from_yolo_json."""

import json
from pathlib import Path

import pytest

from datasets_adapter import (
    DetectorOutputValidationError,
    detections_from_yolo_json,
)


@pytest.fixture
def valid_yolo_json(tmp_path: Path) -> Path:
    data = {
        "image": "sheet_01.png",
        "width": 1920,
        "height": 1080,
        "preds": [
            {"cls": 0, "conf": 0.95, "x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 200.0},
            {"cls": 1, "conf": 0.87, "x0": 300.0, "y0": 400.0, "x1": 500.0, "y1": 600.0},
        ],
    }
    path = tmp_path / "detections.json"
    path.write_text(json.dumps(data))
    return path


class TestDetectorOutputValidation:
    def test_valid_json_passes(self, valid_yolo_json):
        dets = detections_from_yolo_json(valid_yolo_json, source="test:sheet_01")
        assert len(dets) == 2
        assert dets[0].label == "door"
        assert dets[1].label == "window"

    def test_missing_pred_key_raises(self, tmp_path: Path):
        data = {"image": "sheet_01.png", "width": 1920, "height": 1080}
        path = tmp_path / "missing_preds.json"
        path.write_text(json.dumps(data))
        with pytest.raises(DetectorOutputValidationError, match="preds"):
            detections_from_yolo_json(path, source="test:sheet_01")

    def test_missing_cls_key_raises(self, tmp_path: Path):
        data = {
            "image": "sheet_01.png",
            "width": 1920,
            "height": 1080,
            "preds": [{"conf": 0.95, "x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 200.0}],
        }
        path = tmp_path / "missing_cls.json"
        path.write_text(json.dumps(data))
        with pytest.raises(DetectorOutputValidationError, match="'cls' is a required property"):
            detections_from_yolo_json(path, source="test:sheet_01")

    def test_invalid_class_index_raises(self, tmp_path: Path):
        data = {
            "image": "sheet_01.png",
            "width": 1920,
            "height": 1080,
            "preds": [{"cls": 99, "conf": 0.95, "x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 200.0}],
        }
        path = tmp_path / "bad_cls.json"
        path.write_text(json.dumps(data))
        with pytest.raises(DetectorOutputValidationError, match="cls.*is out of range"):
            detections_from_yolo_json(path, source="test:sheet_01")

    def test_conf_out_of_range_raises(self, tmp_path: Path):
        data = {
            "image": "sheet_01.png",
            "width": 1920,
            "height": 1080,
            "preds": [{"cls": 0, "conf": 1.5, "x0": 10.0, "y0": 20.0, "x1": 100.0, "y1": 200.0}],
        }
        path = tmp_path / "bad_conf.json"
        path.write_text(json.dumps(data))
        with pytest.raises(DetectorOutputValidationError, match="greater than the maximum"):
            detections_from_yolo_json(path, source="test:sheet_01")

    def test_negative_bbox_dimension_raises(self, tmp_path: Path):
        data = {
            "image": "sheet_01.png",
            "width": 1920,
            "height": 1080,
            "preds": [{"cls": 0, "conf": 0.95, "x0": 10.0, "y0": 20.0, "x1": -1.0, "y1": 200.0}],
        }
        path = tmp_path / "negative_bbox.json"
        path.write_text(json.dumps(data))
        with pytest.raises(DetectorOutputValidationError, match="less than the minimum"):
            detections_from_yolo_json(path, source="test:sheet_01")

    def test_truncated_json_raises(self, tmp_path: Path):
        path = tmp_path / "truncated.json"
        path.write_text('{"image": "sheet_01.png", "width": 1920, "height": 1')
        with pytest.raises(DetectorOutputValidationError, match="not valid JSON"):
            detections_from_yolo_json(path, source="test:sheet_01")

    def test_wrong_pred_type_raises(self, tmp_path: Path):
        data = {
            "image": "sheet_01.png",
            "width": 1920,
            "height": 1080,
            "preds": "not an array",
        }
        path = tmp_path / "bad_preds_type.json"
        path.write_text(json.dumps(data))
        with pytest.raises(DetectorOutputValidationError, match="is not of type"):
            detections_from_yolo_json(path, source="test:sheet_01")
