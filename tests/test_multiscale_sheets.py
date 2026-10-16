"""Multi-scale sheet rendering and the grouped train/val split (#743).

Multi-scale training renders each synthetic layout at several px/m scales so
the detector sees one building at several zooms. The converter must group all
scales of a seed onto one side of the split (no cross-scale leakage), and the
single-scale path must stay byte-for-byte compatible with the old stems
shuffle.
"""

import json
import re

from synth.sheets import PX_PER_M, generate_sheet

_CACHE: dict = {}


def _render(seed, px_per_m):
    key = (seed, px_per_m)
    if key not in _CACHE:
        _CACHE[key] = generate_sheet(seed, px_per_m=px_per_m)
    return _CACHE[key]


def test_scale_parameter_changes_size_and_naming():
    img50, gt50 = _render(0, PX_PER_M)
    img40, gt40 = _render(0, 40)
    assert gt50["sheet_id"] == "sheet_000"
    assert gt50["scale_px_per_m"] == PX_PER_M
    assert gt40["sheet_id"] == "sheet_000_s40"
    assert gt40["scale_px_per_m"] == 40
    assert img40.shape[0] < img50.shape[0] and img40.shape[1] < img50.shape[1]


def test_same_seed_same_layout_across_scales():
    _, gt50 = _render(0, PX_PER_M)
    _, gt40 = _render(0, 40)
    assert [r["name"] for r in gt50["rooms"]] == [r["name"] for r in gt40["rooms"]]
    assert [r["polygon_m"] for r in gt50["rooms"]] == [r["polygon_m"] for r in gt40["rooms"]]
    assert gt50["footprint"] == gt40["footprint"]
    assert gt50["expected"]["counts"] == gt40["expected"]["counts"]


def _write_sheet(dirpath, stem, scale=50.0, size=(100, 100)):
    from PIL import Image

    Image.new("L", size, 255).save(dirpath / f"{stem}.png")
    (dirpath / f"{stem}.gt.json").write_text(
        json.dumps(
            {
                "sheet_id": stem,
                "scale_px_per_m": scale,
                "sheet_size_px": list(size),
                "symbols": [],
            }
        )
    )


def test_convert_split_groups_all_scales_of_a_seed(tmp_path):
    from detector.convert_synth import convert

    sheets = tmp_path / "sheets"
    sheets.mkdir()
    for base in ("sheet_000", "sheet_001", "sheet_002", "sheet_003"):
        for scale in (40, 50, 65):
            _write_sheet(sheets, f"{base}_s{scale}", scale=float(scale))
    summary = convert(sheets, tmp_path / "out", val_fraction=0.5, seed=0, include_empty=True)
    assert summary["sheets_total"] == 12
    assert summary["scales"] == [40.0, 50.0, 65.0]
    seen: dict = {}
    for split in ("train", "val"):
        for p in (tmp_path / "out" / "images" / split).glob("*.png"):
            seen.setdefault(re.sub(r"_s\d+$", "", p.stem), set()).add(split)
    assert len(seen) == 4
    assert all(v == {"train"} or v == {"val"} for v in seen.values())
    assert sum(1 for v in seen.values() if v == {"val"}) == 2


def test_convert_single_scale_split_matches_stem_shuffle(tmp_path):
    from detector.convert_synth import convert

    sheets = tmp_path / "sheets"
    sheets.mkdir()
    stems = [f"sheet_{i:03d}" for i in range(4)]
    for s in stems:
        _write_sheet(sheets, s)
    convert(sheets, tmp_path / "out", val_fraction=0.5, seed=0, include_empty=True)
    order = __import__("numpy").random.default_rng(0).permutation(4)
    expected_val = {stems[i] for i in order[:2]}
    got_val = {p.stem for p in (tmp_path / "out" / "images" / "val").glob("*.png")}
    got_train = {p.stem for p in (tmp_path / "out" / "images" / "train").glob("*.png")}
    assert got_val == expected_val
    assert got_train == set(stems) - expected_val
