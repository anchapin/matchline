"""Unified detection taxonomy for the matchline detector track.

Monday-critical classes are door + window (they drive takeoffs). The taxonomy
is intentionally small: a fine-tuned YOLO baseline on clean drawings only
needs the symbols that feed quantity takeoffs. Fixture classes (sink, toilet,
...) can be added later by extending CLASS_IDS and the per-dataset maps.

Class ids are stable across datasets so a model trained on CubiCasa5K can be
evaluated zero-shot on AEC Geometric Bench and FloorPlanCAD.
"""

# Unified classes
CLASS_IDS = {
    "door": 0,
    "window": 1,
}
CLASS_NAMES = ["door", "window"]

# --- Per-dataset label -> unified class ------------------------------------

# CubiCasa5K model.svg `class` attribute values (leaf symbol groups).
# Door variants (Beside/Opposite = placement; Slide/Zfold/RollUp/Fold/ParallelSlide
# = door type) all collapse to "door" for detection. "Doors" (plural) is the
# container group and is intentionally excluded.
CUBICASA_MAP = {
    "Door Swing Beside": "door",
    "Door Swing Opposite": "door",
    "Door None Beside": "door",
    "Door Slide Beside": "door",
    "Door Zfold Beside": "door",
    "Door RollUp Beside": "door",
    "Door ParallelSlide Beside": "door",
    "Door Fold Beside": "door",
    "Window Regular": "window",
    "Window Sauna": "window",
}

# AEC Geometric Bench CVAT labels (annotations_15_scoring_ready.xml).
AEC_MAP = {
    "Single Swing Door": "door",
    "Double Swing Door": "door",
    "Window": "window",
    # Fixture classes below are not in the baseline taxonomy; kept here so the
    # converter can report how many it skips.
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

# FloorPlanCAD parquet `category` values.
FLOORPLANCAD_MAP = {
    "single_door": "door",
    "double_door": "door",
    "sliding_door": "door",
    "window": "window",
    "bay_window": "window",
    "blind_window": "window",
}
