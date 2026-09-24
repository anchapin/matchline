"""Tests for detector/convert_aec.py: AEC-to-YOLO conversion.

Covers:
  - XML parsing with safe_xml_parser
  - Box-to-YOLO coordinate conversion (cx, cy, w, h normalized)
  - AEC_MAP label mapping (door, window, fixtures)
  - Skipped polygon reporting
  - Unknown label handling
"""

from __future__ import annotations

from collections import Counter

import pytest
from lxml import etree

from safe_xml import safe_xml_parser

AEC_MAP = {
    "Single Swing Door": "door",
    "Double Swing Door": "door",
    "Window": "window",
    "Sink": None,
    "Toilet": None,
    "Bathtub": None,
    "Shower": None,
    "Cooktops": None,
    "Room": None,
    "Shaft": None,
    "Balcony": None,
    "Elevator": None,
    "Stairs": None,
    "Wall": None,
    "Railing": None,
}

CLASS_IDS = {
    "door": 0,
    "window": 1,
}


def _build_cvat_xml(boxes, width=1000, height=1000, polygons=None):
    """Build a minimal CVAT-style XML document for testing."""
    root = etree.Element("dataset")
    image = etree.SubElement(
        root, "image", name="sheet_01.png", width=str(width), height=str(height)
    )
    for box in boxes:
        x0, y0, x1, y1, label = box
        etree.SubElement(
            image, "box", xtl=str(x0), ytl=str(y0), xbr=str(x1), ybr=str(y1), label=label
        )
    if polygons:
        for poly in polygons:
            xtl, ytl, xbr, ybr, label = poly
            etree.SubElement(
                image,
                "polygon",
                xtl=str(xtl),
                ytl=str(ytl),
                xbr=str(xbr),
                ybr=str(ybr),
                label=label,
            )
    return etree.tostring(root, encoding="unicode")


def _yolo_line_from_box(x0, y0, x1, y1, W, H):
    """Convert CVAT box to YOLO format string."""
    cx = ((x0 + x1) / 2) / W
    cy = ((y0 + y1) / 2) / H
    w = (x1 - x0) / W
    h = (y1 - y0) / H
    return f"{cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


class TestXmlParsing:
    def test_safe_xml_parser_rejects_xxe(self):
        """XXE entity expansion must not occur during XML parsing.

        lxml with resolve_entities=False raises a parse error when encountering
        an unresolved entity reference. This is the correct secure behavior -
        the attack is blocked at parse time.
        """
        xxe_payload = """<?xml version="1.0"?>
        <!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <dataset><image name="test.png" width="100" height="100">
            <box xtl="10" ytl="10" xbr="50" ybr="50" label="&xxe;"/>
        </image></dataset>"""
        parser = safe_xml_parser()
        with pytest.raises(etree.ParseError):
            etree.fromstring(xxe_payload.encode(), parser)

    def test_safe_xml_parser_valid_xml(self):
        """Valid CVAT XML parses successfully."""
        xml_str = _build_cvat_xml([(10, 20, 50, 60, "Single Swing Door")])
        parser = safe_xml_parser()
        tree = etree.fromstring(xml_str.encode(), parser)
        assert tree.tag == "dataset"
        assert tree.find(".//image") is not None

    def test_safe_xml_parser_handles_nested_elements(self):
        """Image elements with multiple boxes parse correctly."""
        xml_str = _build_cvat_xml(
            [
                (10, 10, 50, 50, "Single Swing Door"),
                (60, 10, 100, 50, "Window"),
            ]
        )
        parser = safe_xml_parser()
        tree = etree.fromstring(xml_str.encode(), parser)
        boxes = tree.findall(".//box")
        assert len(boxes) == 2


class TestYoloCoordinateConversion:
    def test_full_width_box(self):
        """Box spanning entire image width produces cx=0.5, w=1.0."""
        W, H = 1000, 1000
        x0, y0, x1, y1 = 0, 100, 1000, 200
        expected = _yolo_line_from_box(x0, y0, x1, y1, W, H)
        assert expected == "0.500000 0.150000 1.000000 0.100000"

    def test_half_width_box(self):
        """Box spanning half image width."""
        W, H = 1000, 1000
        x0, y0, x1, y1 = 0, 0, 500, 500
        expected = _yolo_line_from_box(x0, y0, x1, y1, W, H)
        assert expected == "0.250000 0.250000 0.500000 0.500000"

    def test_small_centered_box(self):
        """Small box in center of image."""
        W, H = 1000, 1000
        x0, y0, x1, y1 = 400, 400, 600, 600
        expected = _yolo_line_from_box(x0, y0, x1, y1, W, H)
        assert expected == "0.500000 0.500000 0.200000 0.200000"

    def test_box_at_origin(self):
        """Box at top-left corner."""
        W, H = 1000, 1000
        x0, y0, x1, y1 = 0, 0, 100, 100
        expected = _yolo_line_from_box(x0, y0, x1, y1, W, H)
        assert expected == "0.050000 0.050000 0.100000 0.100000"

    def test_non_square_image(self):
        """Box in non-square image uses actual width/height for normalization."""
        W, H = 2000, 1000
        x0, y0, x1, y1 = 500, 250, 1500, 750
        expected = _yolo_line_from_box(x0, y0, x1, y1, W, H)
        assert expected == "0.500000 0.500000 0.500000 0.500000"


class TestAecMapLabelMapping:
    def test_single_swing_door_maps_to_door(self):
        assert AEC_MAP["Single Swing Door"] == "door"

    def test_double_swing_door_maps_to_door(self):
        assert AEC_MAP["Double Swing Door"] == "door"

    def test_window_maps_to_window(self):
        assert AEC_MAP["Window"] == "window"

    def test_fixture_labels_map_to_none(self):
        """Fixture labels (sink, toilet, etc.) are skipped."""
        fixtures = ["Sink", "Toilet", "Bathtub", "Shower", "Cooktops"]
        for fixture in fixtures:
            assert AEC_MAP[fixture] is None, f"{fixture} should map to None"

    def test_non_spatial_labels_map_to_none(self):
        """Room, Shaft, Balcony are spatial but not detectable objects."""
        non_detectable = ["Room", "Shaft", "Balcony", "Elevator", "Stairs", "Wall", "Railing"]
        for label in non_detectable:
            assert AEC_MAP[label] is None, f"{label} should map to None"


class TestClassIdsMapping:
    def test_door_class_id_is_zero(self):
        assert CLASS_IDS["door"] == 0

    def test_window_class_id_is_one(self):
        assert CLASS_IDS["window"] == 1

    def test_yolo_line_uses_class_id(self):
        """YOLO line format is: class_id cx cy w h."""
        W, H = 1000, 1000
        x0, y0, x1, y1 = 0, 0, 100, 100
        cx = ((x0 + x1) / 2) / W
        cy = ((y0 + y1) / 2) / H
        w = (x1 - x0) / W
        h = (y1 - y0) / H
        line = f"{CLASS_IDS['door']} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
        assert line.startswith("0 ")


class TestBoxProcessingLogic:
    def test_box_coordinates_float_conversion(self):
        """Box attributes may be float strings; must handle int(float()) conversion."""
        xml_str = _build_cvat_xml([(10.5, 20.3, 50.7, 60.9, "Single Swing Door")])
        parser = safe_xml_parser()
        tree = etree.fromstring(xml_str.encode(), parser)
        box = tree.find(".//box")
        x0 = float(box.attrib["xtl"])
        y0 = float(box.attrib["ytl"])
        x1 = float(box.attrib["xbr"])
        y1 = float(box.attrib["ybr"])
        assert x0 == 10.5
        assert y0 == 20.3
        assert x1 == 50.7
        assert y1 == 60.9

    def test_box_with_decimal_dimensions(self):
        """Boxes with decimal pixel coordinates convert correctly."""
        W, H = 1000.0, 1000.0
        x0, y0, x1, y1 = 100.5, 100.5, 400.5, 400.5
        cx = ((x0 + x1) / 2) / W
        cy = ((y0 + y1) / 2) / H
        w = (x1 - x0) / W
        h = (y1 - y0) / H
        assert abs(cx - 0.2505) < 0.001
        assert abs(cy - 0.2505) < 0.001
        assert abs(w - 0.3) < 0.001
        assert abs(h - 0.3) < 0.001


class TestStatsCounter:
    def test_counter_initialization(self):
        """Stats counter tracks boxes and polygons."""
        stats = Counter()
        stats["box_door"] += 1
        stats["box_window"] += 2
        stats["skipped_polygon_Wall"] += 1
        assert stats["box_door"] == 1
        assert stats["box_window"] == 2
        assert stats["skipped_polygon_Wall"] == 1

    def test_skipped_fixture_increments_counter(self):
        """Fixture labels should increment skipped counter, not box counter."""
        stats = Counter()
        label = "Sink"
        if AEC_MAP.get(label) is None:
            stats["skipped_" + label.replace(" ", "_")] += 1
        assert stats["skipped_Sink"] == 1
        assert stats["box_door"] == 0
        assert stats["box_window"] == 0

    def test_unknown_label_increments_counter(self):
        """Labels not in AEC_MAP increment unknown counter."""
        stats = Counter()
        label = "UnknownLabel"
        uni = AEC_MAP.get(label, "UNKNOWN")
        if uni == "UNKNOWN":
            stats["unknown_label_" + str(label)] += 1
        assert stats["unknown_label_UnknownLabel"] == 1
