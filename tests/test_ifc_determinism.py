"""IFC import is deterministic across processes (#586).

Pascal found that random ids made one file import differently run to run,
because near-ties were broken by id order (``ids.ts``, MIT, Copyright (c)
2026 Pascal Group Inc., commit 67f8041). matchline breaks ties by GlobalId,
never by object id or set order. This imports each IFC fixture in separate
interpreters with different ``PYTHONHASHSEED`` values and requires
byte-identical model JSON.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("ifcopenshell")

REPO = Path(__file__).resolve().parents[1]
SEEDS = ("0", "1", "4242")
_IMPORT = (
    "import hashlib, sys; from ifc_import import import_ifc; "
    "print(hashlib.sha256(import_ifc(sys.argv[1]).to_json().encode()).hexdigest())"
)


def _base(tmp_path):
    from tests.test_ifc_import import make_ifc_fixture

    return make_ifc_fixture(tmp_path / "base.ifc")


def _closet(tmp_path):
    from tests.test_ifc_closet_merge import _build

    return _build(tmp_path / "closet.ifc")


def _fragments(tmp_path):
    from tests.ifc_defects import fixture, fragment_south_wall

    return fragment_south_wall(fixture(tmp_path / "frag.ifc"), [0, 7, 14, 20], [3, 2, 3])


def _doubled(tmp_path):
    from tests.ifc_defects import double_a_window, fixture

    return double_a_window(fixture(tmp_path / "dbl.ifc"))


def _doors(tmp_path):
    from tests.test_ifc_door_semantics import _door_ifc

    return _door_ifc(tmp_path, op="DOUBLE_DOOR_SINGLE_SWING", frac=0.4)


def _shaft(tmp_path):
    from tests.test_ifc_wall_loops import _ifc_with_shaft

    return _ifc_with_shaft(tmp_path, with_void=True)


def _partition(tmp_path):
    from tests.test_ifc_wall_split import _ifc_with

    return _ifc_with(tmp_path, [("Part", (10, 0.1, 0), (0, 1, 0), 7.9, 0.1)])


FIXTURES = [_base, _closet, _fragments, _doubled, _doors, _shaft, _partition]


def _digest(path, seed):
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(REPO))
    out = subprocess.run(
        [sys.executable, "-c", _IMPORT, str(path)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip()


@pytest.mark.parametrize("build", FIXTURES, ids=lambda b: b.__name__.strip("_"))
def test_import_is_identical_across_hash_seeds(tmp_path, build):
    path = build(tmp_path)
    digests = {seed: _digest(path, seed) for seed in SEEDS}
    assert len(set(digests.values())) == 1, digests


def test_in_process_matches_subprocess(tmp_path):
    from ifc_import import import_ifc

    path = _base(tmp_path)
    here = hashlib.sha256(import_ifc(path).to_json().encode()).hexdigest()
    assert here == _digest(path, "7")
