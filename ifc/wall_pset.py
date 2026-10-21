"""Authored wall-exterior pset data shared by the IFC pipeline (#1219, #1226).

``Pset_WallCommon.IsExternal`` is authored data and outranks every geometric
probe: a pset-exterior wall is never dropped as an interior partition (#1219)
and never dropped as a lining (#1226). The envelope wall's provenance note
carries ``PSET_EXTERNAL_MARK`` so the passes that run after import can honour
the flag without re-reading the file.
"""

from __future__ import annotations


def _wall_marked_interior(wall):
    """Pset_WallCommon.IsExternal explicitly False (#747).

    Exported interior partitions (semi-exterior and vestibule boundaries)
    carry this pset, so a re-imported matchline file does not count them
    as envelope. A file without the pset, or with it True, is unchanged.
    """
    try:
        import ifcopenshell.util.element as _El

        psets = _El.get_psets(wall) or {}
    except (ImportError, AttributeError, RuntimeError):
        return False
    ext = (psets.get("Pset_WallCommon") or {}).get("IsExternal")
    if ext is None or ext is True:
        return False
    if ext is False:
        return True
    return str(ext).strip().lower() in ("false", ".f.", "0")


PSET_EXTERNAL_MARK = "; pset IsExternal"


def _wall_pset_external(wall):
    """Pset_WallCommon.IsExternal explicitly True (#1219).

    Authored exterior data outranks the Tier 1 space probe: space polygons
    can bleed past a perimeter wall's outer face and an atrium can be
    modelled as a space, so a wall the pset calls exterior stays in the
    envelope even when the probe finds spaces on both sides.
    """
    try:
        import ifcopenshell.util.element as _El

        psets = _El.get_psets(wall) or {}
    except (ImportError, AttributeError, RuntimeError):
        return False
    ext = (psets.get("Pset_WallCommon") or {}).get("IsExternal")
    if ext is None or ext is False:
        return False
    if ext is True:
        return True
    return str(ext).strip().lower() in ("true", ".t.", "1")
