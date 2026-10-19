"""Wall-class aggregation helpers (#1113).

The auxiliary wall-class head (detector/train_scratch.py, --wall-head) makes
a per-grid-cell wall TYPE prediction on wall-covered cells. These helpers
turn that per-cell map into a per-wall-segment class for the wall record, so
#747 work can consume the coarse partition-vs-structural signal without
touching torch. Pure Python/numpy: importable from non-torch consumers.
"""

from __future__ import annotations

import numpy as np

# Head-class ids (the .wallcls.txt namespace). The per-cell rasterization in
# train_scratch.rasterize_wall_rows stores these + 1 (0 = no wall).
PARTITION = 0
STRUCTURAL = 1


def segment_cells(cx0, cy0, cx1, cy1, grid, samples=None):
    """Grid cells (i, j) a segment passes through, in visit order.

    (cx0, cy0) and (cx1, cy1) are the segment endpoints in [0, 1] coords
    normalized to the sheet/tile the map covers. Walks at `samples` evenly
    spaced points (default 4 per grid cell of length) and deduplicates.
    """
    if samples is None:
        samples = max(2, int(4 * grid))
    seen: set = set()
    out = []
    for k in range(samples + 1):
        t = k / samples
        j = int(np.clip((cx0 + (cx1 - cx0) * t) * grid, 0, grid - 1))
        i = int(np.clip((cy0 + (cy1 - cy0) * t) * grid, 0, grid - 1))
        if (i, j) not in seen:
            seen.add((i, j))
            out.append((i, j))
    return out


def majority_wall_class(cells, class_map):
    """Majority wall class along visited cells; None when no cell carries one.

    class_map: (grid, grid) array-like with 0 = no wall, 1 = partition,
    2 = structural (the rasterize_wall_rows convention, head cls + 1).
    Returns a head-class id (0 partition / 1 structural) or None. Ties go to
    structural: a cell that looks structural usually is.
    """
    counts: dict = {}
    m = np.asarray(class_map)
    for i, j in cells:
        v = int(m[i, j])
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] - 1
