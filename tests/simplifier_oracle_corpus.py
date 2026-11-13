"""Fixed corpus of simplifier inputs for the differential oracle (#1393).

This module holds the INPUT side of the oracle: a frozen list of cases that
feed the two simplifiers of the repo -- the envelope/footprint simplifier
(``geometry/geometry_simplify.py``) and the roof simplifier
(``geometry/roof_simplify.py``). The OUTPUT side (the expected results) is
recorded, not hand-written: ``tests/make_simplifier_oracle.py`` runs the
current simplifier over every case and serialises the results to
``tests/fixtures/simplifier_corpus/expected.json``.

The corpus is deliberately varied so that a refactor of either simplifier
has to keep behaving the same on all of these shapes:

* ``empty``    -- no input at all (the no-op paths),
* ``degenerate`` -- inputs that are invalid or have no area (the guard paths),
* ``small``    -- a handful of vertices (nothing to simplify),
* ``typical``  -- the shapes a real drawing produces (jogs, notches, hips,
  dormers, courtyards, several storeys),
* ``high-vertex`` -- over-segmented outlines and roofs with dozens of
  facets, where the greedy passes actually do work.

Every case is compared by the harness (``tests/test_simplifier_oracle.py``)
inside a stated tolerance, so the corpus is not a byte-equality pin: see
``REL_TOL`` / ``ABS_TOL`` in that module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from building_model import Provenance
from geometry.roof_geometry import roof_plane

LAT = 40.0

PROV = Provenance(
    sheet_id="oracle.ifc",
    revision=1,
    method="ifc_import:tier0:roof_plane",
    confidence=0.9,
)

#: Tags every case must carry at least once (checked by the harness).
REQUIRED_TAGS = ("empty", "degenerate", "small", "typical", "high-vertex")


@dataclass(frozen=True)
class OracleCase:
    """One corpus entry: an id, the simplifier to run, its input and its tags."""

    cid: str
    kind: str  # "ring" | "multipart" | "roof"
    tags: tuple = field(default=())
    params: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Ring (envelope / footprint) input shapes
# ---------------------------------------------------------------------------


def _square(side: float = 10.0) -> list:
    return [[0.0, 0.0], [side, 0.0], [side, side], [0.0, side]]


def _collinear_rect() -> list:
    """A rectangle with a midpoint on every edge (free collinear merges)."""
    return [[0.0, 0.0], [5.0, 0.0], [10.0, 0.0], [10.0, 10.0], [5.0, 10.0], [0.0, 10.0]]


def _l_shape() -> list:
    """A typical L-shaped footprint (one reflex corner)."""
    return [[0.0, 0.0], [12.0, 0.0], [12.0, 5.0], [5.0, 5.0], [5.0, 9.0], [0.0, 9.0]]


def _jagged_notches() -> list:
    """A rectangle whose top edge carries ten small notches (CAD noise)."""
    pts = [[0.0, 0.0], [20.0, 0.0]]
    for i in range(10):
        x = 20.0 - 2.0 * i
        y = 10.0 if i % 2 == 0 else 10.25
        pts.append([x, y])
    pts.append([0.0, 10.0])
    return pts


def _ngon(n: int, r: float = 10.0) -> list:
    """A convex regular polygon -- an over-segmented curved outline."""
    return [
        [
            round(r * math.cos(2.0 * math.pi * i / n), 9),
            round(r * math.sin(2.0 * math.pi * i / n), 9),
        ]
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Roof input shapes
# ---------------------------------------------------------------------------


def _on(tilt_deg: float, facing: str, z0: float, pts) -> list:
    """Lift plan points onto a plane rising away from ``facing``.

    The plan is canonical (y down): 'S' falls to +y, 'N' to -y, 'E' to +x,
    'W' to -x. Same helper shape as ``tests/test_roof_simplify.py``.
    """
    t = math.tan(math.radians(tilt_deg))
    rise = {
        "S": lambda x, y: -y,
        "N": lambda x, y: y + 6,
        "E": lambda x, y: 10 - x,
        "W": lambda x, y: x,
    }[facing]
    return [[x, y, z0 + t * rise(x, y)] for x, y in pts]


def _plane(cid: str, verts, level: str = "L1") -> dict:
    return {"id": cid, "level": level, "verts": [list(v) for v in verts]}


def _hip_facets(notch: bool = False) -> list:
    s_pts = [(0, 0), (10, 0), (7, -3), (3, -3)]
    if notch:
        s_pts = [(0, 0), (4, 0), (4, -1), (6, -1), (6, 0), (10, 0), (7, -3), (3, -3)]
    return [
        _plane("S", _on(30, "S", 3, s_pts)),
        _plane("N", _on(30, "N", 3, [(10, -6), (0, -6), (3, -3), (7, -3)])),
        _plane("E", _on(30, "E", 3, [(10, 0), (10, -6), (7, -3)])),
        _plane("W", _on(30, "W", 3, [(0, -6), (0, 0), (3, -3)])),
    ]


def _dormer_facet() -> dict:
    """A small shed facing south at 15 deg, meeting the host south face."""
    z_top = 3 + math.tan(math.radians(30))
    t15 = math.tan(math.radians(15))
    return _plane(
        "D",
        [(4, 0, z_top - t15), (6, 0, z_top - t15), (6, -1, z_top), (4, -1, z_top)],
    )


def _pitch_break_facets(t2: float = 32.0) -> list:
    """A south slope with a slight pitch break: 30 deg up to y=-1.5, then t2."""
    z1 = 3 + 1.5 * math.tan(math.radians(30))
    z2 = z1 + 1.5 * math.tan(math.radians(t2))
    return [
        _plane("A", [(0, 0, 3), (10, 0, 3), (10, -1.5, z1), (0, -1.5, z1)]),
        _plane("B", [(0, -1.5, z1), (10, -1.5, z1), (10, -3, z2), (0, -3, z2)]),
    ]


def _flat_tiles(rows: int = 6, cols: int = 6, size: float = 5.0) -> list:
    """A flat roof cut into rows x cols coplanar tiles (many-facet case)."""
    planes = []
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * size, r * size
            x1, y1 = x0 + size, y0 + size
            planes.append(
                _plane(
                    f"T{r}_{c}",
                    [(x0, y0, 3.0), (x1, y0, 3.0), (x1, y1, 3.0), (x0, y1, 3.0)],
                )
            )
    return planes


def _two_wing_regions() -> list:
    return [
        [[0.0, 0.0], [20.0, 0.0], [20.0, 10.0], [0.0, 10.0]],
        [[25.0, 0.0], [40.0, 0.0], [40.0, 12.0], [25.0, 12.0]],
    ]


def _courtyard_regions() -> list:
    """Four slabs around a courtyard: the union has one interior ring."""
    return [
        [[0.0, 0.0], [20.0, 0.0], [20.0, 6.0], [0.0, 6.0]],
        [[0.0, 14.0], [20.0, 14.0], [20.0, 20.0], [0.0, 20.0]],
        [[0.0, 6.0], [6.0, 6.0], [6.0, 14.0], [0.0, 14.0]],
        [[14.0, 6.0], [20.0, 6.0], [20.0, 14.0], [14.0, 14.0]],
    ]


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

CORPUS: tuple = (
    # -- envelope / footprint rings -------------------------------------
    OracleCase(
        cid="ring_empty",
        kind="ring",
        tags=("empty", "degenerate"),
        params={"tol": 0.02},
        payload={"ring": []},
    ),
    OracleCase(
        cid="ring_three_vertices",
        kind="ring",
        tags=("degenerate", "small"),
        params={"tol": 0.02},
        payload={"ring": [[0.0, 0.0], [10.0, 0.0], [0.0, 10.0]]},
    ),
    OracleCase(
        cid="ring_two_identical_points",
        kind="ring",
        tags=("degenerate",),
        params={"tol": 0.02},
        payload={"ring": [[0.0, 0.0], [0.0, 0.0]], "expect_error": "ValueError"},
    ),
    OracleCase(
        cid="ring_zero_area",
        kind="ring",
        tags=("degenerate",),
        params={"tol": 0.02},
        payload={
            "ring": [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]],
            "expect_error": "ValueError",
        },
    ),
    OracleCase(
        cid="ring_square",
        kind="ring",
        tags=("small", "typical"),
        params={"tol": 0.02},
        payload={"ring": _square()},
    ),
    OracleCase(
        cid="ring_square_tight_tol",
        kind="ring",
        tags=("small",),
        params={"tol": 0.001},
        payload={"ring": _square()},
    ),
    OracleCase(
        cid="ring_collinear_rect",
        kind="ring",
        tags=("small", "typical"),
        params={"tol": 0.02},
        payload={"ring": _collinear_rect()},
    ),
    OracleCase(
        cid="ring_l_shape",
        kind="ring",
        tags=("typical",),
        params={"tol": 0.02},
        payload={"ring": _l_shape()},
    ),
    OracleCase(
        cid="ring_jagged_notches",
        kind="ring",
        tags=("typical",),
        params={"tol": 0.05},
        payload={"ring": _jagged_notches()},
    ),
    OracleCase(
        cid="ring_wall_height",
        kind="ring",
        tags=("typical",),
        params={"tol": 0.02, "wall_height": 3.0},
        payload={"ring": _square(12.0)},
    ),
    OracleCase(
        cid="ring_convex_ngon_64",
        kind="ring",
        tags=("high-vertex",),
        params={"tol": 0.02},
        payload={"ring": _ngon(64)},
    ),
    OracleCase(
        cid="ring_curve_douglas_peucker",
        kind="ring",
        tags=("high-vertex",),
        params={"tol": 0.02, "dp_eps": 0.05},
        payload={"ring": _ngon(128)},
    ),
    # -- multi-part footprints ------------------------------------------
    OracleCase(
        cid="multipart_two_wings",
        kind="multipart",
        tags=("typical",),
        params={"tol": 0.02},
        payload={"regions": _two_wing_regions()},
    ),
    OracleCase(
        cid="multipart_courtyard",
        kind="multipart",
        tags=("typical",),
        params={"tol": 0.02},
        payload={"regions": _courtyard_regions()},
    ),
    # -- roofs -----------------------------------------------------------
    OracleCase(
        cid="roof_empty",
        kind="roof",
        tags=("empty", "degenerate"),
        params={},
        payload={"planes": []},
    ),
    OracleCase(
        cid="roof_single_plane",
        kind="roof",
        tags=("small",),
        params={},
        payload={"planes": [_plane("A", _on(30, "S", 3, [(0, 0), (10, 0), (10, -6), (0, -6)]))]},
    ),
    OracleCase(
        cid="roof_pitch_break_merges",
        kind="roof",
        tags=("typical",),
        params={},
        payload={"planes": _pitch_break_facets()},
    ),
    OracleCase(
        cid="roof_hip_with_dormer",
        kind="roof",
        tags=("typical",),
        params={},
        payload={"planes": _hip_facets(notch=True) + [_dormer_facet()]},
    ),
    OracleCase(
        cid="roof_two_levels",
        kind="roof",
        tags=("typical",),
        params={},
        payload={
            "planes": [
                _plane("A", _on(30, "S", 3, [(0, 0), (5, 0), (5, -3), (0, -3)])),
                _plane("B", _on(30, "S", 3, [(5, 0), (10, 0), (10, -3), (5, -3)]), level="L2"),
            ]
        },
    ),
    OracleCase(
        cid="roof_area_budget_refusal",
        kind="roof",
        tags=("typical",),
        params={"angle_tol_deg": 5, "area_tol": 1e-6, "aperture_tol": 0.5},
        payload={"planes": _pitch_break_facets(34.0)},
    ),
    OracleCase(
        cid="roof_many_coplanar_tiles",
        kind="roof",
        tags=("high-vertex",),
        params={},
        payload={"planes": _flat_tiles()},
    ),
)


# ---------------------------------------------------------------------------
# Running a case (shared by the recorder and the harness)
# ---------------------------------------------------------------------------


def _ring_summary(res) -> dict:
    return {
        "original_count": res.original_count,
        "simplified_count": res.simplified_count,
        "original_area": res.original_area,
        "simplified_area": res.simplified_area,
        "area_delta_pct": res.area_delta_pct,
        "tol": res.tol,
        "method": res.method,
        "confidence": res.confidence,
        "valid": res.valid,
        "ring": [[float(x), float(y)] for x, y in res.ring],
        "provenance": res.provenance,
        "skipped": res.skipped,
    }


def _multipart_summary(res) -> dict:
    return {
        "method": res.method,
        "tol": res.tol,
        "valid": res.valid,
        "n_parts": len(res.parts),
        "original_count": res.original_count,
        "simplified_count": res.simplified_count,
        "total_original_area": res.total_original_area,
        "total_simplified_area": res.total_simplified_area,
        "area_delta_pct": res.area_delta_pct,
        "budget_area": res.budget_area,
        "spent_area": res.spent_area,
        "part_budget": res.part_budget,
        "part_spent": res.part_spent,
        "rings": [[[float(x), float(y)] for x, y in r.ring] for r in res.parts],
        "holes": [[[[float(x), float(y)] for x, y in h.ring] for h in hs] for hs in res.holes],
        "per_surface_mapping": res.per_surface_mapping,
    }


def _run_ring(case: OracleCase) -> dict:
    from geometry.geometry_simplify import simplify_ring

    ring = [tuple(float(c) for c in v) for v in case.payload["ring"]]
    expect_error = case.payload.get("expect_error")
    try:
        res = simplify_ring(ring, **case.params)
    except Exception as exc:  # noqa: BLE001 - the corpus pins the guard paths
        if not expect_error:
            raise
        return {"kind": "ring", "error": type(exc).__name__, "message": str(exc)}
    if expect_error:
        return {"kind": "ring", "error": None, "message": None}
    return {"kind": "ring", **_ring_summary(res)}


def _run_multipart(case: OracleCase) -> dict:
    from geometry.geometry_simplify import (
        footprint_multipart_from_regions,
        simplify_multipart_footprint,
    )

    parts = footprint_multipart_from_regions(case.payload["regions"])
    res = simplify_multipart_footprint(parts, **case.params)
    return {"kind": "multipart", **_multipart_summary(res)}


def _run_roof(case: OracleCase) -> dict:
    from geometry.roof_simplify import roof_simplify_report, simplify_roof

    planes = [
        roof_plane(
            p["id"],
            p["verts"],
            level_id=p["level"],
            host_global_id=p["id"],
            provenance=PROV,
        )
        for p in case.payload["planes"]
    ]
    res = simplify_roof(planes, LAT, **case.params)
    return {"kind": "roof", **roof_simplify_report(res)}


_RUNNERS = {"ring": _run_ring, "multipart": _run_multipart, "roof": _run_roof}


def run_case(case: OracleCase) -> dict:
    """Run one corpus case through the current simplifier and serialise it."""
    return _RUNNERS[case.kind](case)


def corpus_inputs() -> dict:
    """The corpus INPUT side, JSON-serialisable (for the committed artefact)."""
    return {
        case.cid: {
            "kind": case.kind,
            "tags": list(case.tags),
            "params": case.params,
            "payload": case.payload,
        }
        for case in CORPUS
    }
