"""Space conditioning category per ASHRAE 90.1-2019 Section 3.2 (#747).

The category of a space comes from the HVAC that serves it, never from its
name: the heating and cooling output per floor area, compared with the
Section 3.2 definitions (Alex, #747 issuecomment-6092786749):

* cooled: sensible cooling output > 3.4 Btu/h-ft2
* heated: heating output at or above Table 3.2 for the climate zone
* indirectly conditioned: neither, but heat flows mostly to conditioned
  neighbours, or > 3 ach of conditioned air is transferred in
* conditioned: cooled, heated or indirectly conditioned
* semiheated: heating output >= 3.4 Btu/h-ft2 but not conditioned
* unconditioned: everything else

Nothing is defaulted. A test that the data can't settle leaves the space in
``review`` with the reason. A value only settles a test when every reading of
the unknowns gives the same answer: heating under 3.4 Btu/h-ft2 is not heated
in any climate zone, and heating at or above 19 is heated in all of them.

This module is the classifier only; it reads no schedules and writes nothing
to the model. Capacities are passed in Btu/h, area in ft2.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

COOLED_BTUH_FT2 = 3.4  # sensible output must be greater than this
SEMIHEATED_BTUH_FT2 = 3.4  # heating output at or above this

# 90.1-2019 Table 3.2, heating output (Btu/h-ft2) at or above which a space is
# heated. Keys are climate zone numbers with an optional letter.
TABLE_3_2 = {
    "0": 5.0,
    "1": 5.0,
    "2": 5.0,
    "3A": 9.0,
    "3B": 9.0,
    "3C": 7.0,
    "4A": 10.0,
    "4B": 10.0,
    "4C": 8.0,
    "5": 12.0,
    "6": 14.0,
    "7": 16.0,
    "8": 19.0,
}

FT2_PER_M2 = 1.0 / 0.3048**2
BTUH_PER_W = 3.412141633
# heating/cooling capacity unit -> Btu/h
CAPACITY_BTUH = {
    "btuh": 1.0,
    "mbh": 1000.0,
    "w": BTUH_PER_W,
    "kw": 1000.0 * BTUH_PER_W,
    "ton": 12000.0,
}

CATEGORIES = ("conditioned", "semiheated", "unconditioned", "review")


def to_btuh(value: float, unit: str) -> float:
    """``value`` in ``unit`` (Btu/h, MBH, W, kW, tons) as Btu/h.

    Raises ``ValueError`` for any other unit; a capacity whose units the
    schedule doesn't state is not converted by guess.
    """
    u = re.sub(r"[\s/\-.]", "", (unit or "").lower())
    u = {"btuhr": "btuh", "btu": "btuh", "kbtuh": "mbh", "tons": "ton", "tr": "ton"}.get(u, u)
    if u not in CAPACITY_BTUH:
        raise ValueError(f"unknown capacity unit: {unit!r}")
    return float(value) * CAPACITY_BTUH[u]


def heated_thresholds(climate_zone: str) -> Tuple[float, ...]:
    """Table 3.2 values a climate zone could mean, lowest first.

    ``"4A"`` gives ``(10.0,)``; ``"3"`` without a letter gives ``(7.0, 9.0)``
    (3C or 3A/3B); ``""`` gives every value in the table. Raises
    ``ValueError`` for text that is not a climate zone.
    """
    cz = (climate_zone or "").strip().upper()
    if not cz:
        return tuple(sorted(set(TABLE_3_2.values())))
    m = re.fullmatch(r"(?:CZ\s*)?([0-8])\s*([ABC])?", cz)
    if not m:
        raise ValueError(f"not an ASHRAE climate zone: {climate_zone!r}")
    num, letter = m.group(1), m.group(2) or ""
    if num in TABLE_3_2:  # the threshold doesn't depend on the letter
        return (TABLE_3_2[num],)
    if letter:
        return (TABLE_3_2[num + letter],)
    return tuple(sorted({v for k, v in TABLE_3_2.items() if k.startswith(num)}))


@dataclass
class SpaceCategory:
    """One space's Section 3.2 result. ``cooled``/``heated``/``semiheated``
    are None when the data can't settle that test."""

    category: str
    reasons: List[str] = field(default_factory=list)
    cooling_btuh_ft2: Optional[float] = None
    heating_btuh_ft2: Optional[float] = None
    heated_threshold: Optional[float] = None
    cooled: Optional[bool] = None
    heated: Optional[bool] = None
    indirect: Optional[bool] = None
    semiheated: Optional[bool] = None

    def to_dict(self) -> dict:
        return asdict(self)


def classify_space(
    area_ft2: Optional[float],
    sensible_cooling_btuh: Optional[float] = None,
    heating_btuh: Optional[float] = None,
    climate_zone: str = "",
    total_cooling_btuh: Optional[float] = None,
    indirect: Optional[bool] = None,
) -> SpaceCategory:
    """Section 3.2 category of one space.

    ``sensible_cooling_btuh`` / ``heating_btuh``: output serving the space;
    ``0`` means no such equipment, ``None`` means not known.
    ``total_cooling_btuh``: total (not sensible) cooling when only that is
    scheduled; it never makes a space cooled (#747 issuecomment-6092815093),
    so a space it would decide goes to review.
    ``indirect``: the indirectly-conditioned test, when it was run; ``None``
    means not checked.
    """
    reasons: List[str] = []
    if not area_ft2 or area_ft2 <= 0:
        return SpaceCategory("review", ["no floor area"])
    for name, v in (
        ("sensible cooling", sensible_cooling_btuh),
        ("heating", heating_btuh),
        ("total cooling", total_cooling_btuh),
    ):
        if v is not None and v < 0:
            return SpaceCategory("review", [f"{name} output is negative"])

    out = SpaceCategory("review", reasons, indirect=indirect)

    # cooled
    if sensible_cooling_btuh is not None:
        c = sensible_cooling_btuh / area_ft2
        out.cooling_btuh_ft2 = c
        out.cooled = c > COOLED_BTUH_FT2
        reasons.append(
            f"sensible cooling {c:.2f} Btu/h-ft2 {'>' if out.cooled else '<='} {COOLED_BTUH_FT2}"
        )
    elif total_cooling_btuh is not None:
        c = total_cooling_btuh / area_ft2
        reasons.append(f"only total cooling is known ({c:.2f} Btu/h-ft2), not sensible")
    else:
        reasons.append("cooling output not known")

    # heated, against every Table 3.2 value the climate zone could mean
    try:
        th = heated_thresholds(climate_zone)
    except ValueError as e:
        return SpaceCategory("review", [str(e)])
    if heating_btuh is not None:
        h = heating_btuh / area_ft2
        out.heating_btuh_ft2 = h
        out.semiheated = None
        if len(th) == 1:
            out.heated_threshold = th[0]
        if h >= max(th):
            out.heated = True
            reasons.append(f"heating {h:.2f} Btu/h-ft2 >= Table 3.2 {max(th):g}")
        elif h < min(th):
            out.heated = False
            reasons.append(f"heating {h:.2f} Btu/h-ft2 < Table 3.2 {min(th):g}")
        else:
            reasons.append(
                f"heating {h:.2f} Btu/h-ft2: heated or not depends on the climate zone "
                f"({climate_zone or 'not given'})"
            )
    else:
        reasons.append("heating output not known")

    if out.cooled or out.heated:
        out.category = "conditioned"
        out.semiheated = False
        return out
    if indirect:
        out.category = "conditioned"
        out.semiheated = False
        reasons.append("indirectly conditioned")
        return out
    if out.cooled is None or out.heated is None:
        return out  # review: a test that could make it conditioned is open
    if indirect is None:
        reasons.append("indirectly conditioned test not run")
        return out
    h = out.heating_btuh_ft2
    out.semiheated = h >= SEMIHEATED_BTUH_FT2
    if out.semiheated:
        out.category = "semiheated"
        reasons.append(f"heating {h:.2f} Btu/h-ft2 >= {SEMIHEATED_BTUH_FT2}, not conditioned")
    else:
        out.category = "unconditioned"
    return out
