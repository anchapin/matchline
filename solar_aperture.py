"""Solar-weighted roof aperture (roadmap item 2, #615).

The roof simplifier and the roof validation checks need one number that says
how much sun a set of roof planes collects, so that collapsing a hip into a
single slope can be judged by what BEM will see rather than by area alone:

    aperture = sum over sun positions of  area x max(0, cos incidence)

Sun positions are a fixed, documented set so the number is deterministic:
every hour on the hour (solar time) on four days, 21 Mar, 21 Jun, 21 Sep and
21 Dec, with the sun above the horizon. Declinations are fixed at 0, +23.44,
0 and -23.44 deg; no equation of time, no refraction. The unit is m^2 x
sun-position (an area-weighted count of sunny samples), meant for comparing
two roofs at one latitude, not as an energy figure.

There is no default latitude: pass one, or take it from the model
(IfcSite RefLatitude on IFC import, ``BuildingModel.site_latitude_deg``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Sequence, Tuple

SUN_DAYS: Tuple[Tuple[str, float], ...] = (
    ("mar21", 0.0),
    ("jun21", 23.44),
    ("sep21", 0.0),
    ("dec21", -23.44),
)
SUN_HOURS: Tuple[int, ...] = tuple(range(24))  # solar time, on the hour
FLAT_TILT_DEG = 0.5  # same as roof_geometry: flatter than this has no azimuth
ORIENTATIONS = ("flat", "N", "E", "S", "W")


@dataclass
class SolarAperture:
    total: float
    by_orientation: Dict[str, float] = field(default_factory=dict)
    latitude_deg: float = 0.0
    n_sun_positions: int = 0


def sun_vectors(latitude_deg: float) -> list:
    """Unit vectors to the sun, canonical frame (x east, y down, z up).

    Only positions with the sun above the horizon are returned.
    """
    if latitude_deg is None or not -90.0 <= latitude_deg <= 90.0:
        raise ValueError(f"latitude must be in [-90, 90] degrees, got {latitude_deg!r}")
    phi = math.radians(latitude_deg)
    out = []
    for _, dec in SUN_DAYS:
        d = math.radians(dec)
        for h in SUN_HOURS:
            w = math.radians(15.0 * (h - 12))
            up = math.sin(phi) * math.sin(d) + math.cos(phi) * math.cos(d) * math.cos(w)
            if up <= 1e-12:
                continue
            east = -math.cos(d) * math.sin(w)
            north = math.cos(phi) * math.sin(d) - math.sin(phi) * math.cos(d) * math.cos(w)
            out.append((east, -north, up))
    return out


def plane_normal(tilt_deg: Optional[float], azimuth_deg: Optional[float]):
    """Upward unit normal in the canonical frame from tilt and compass azimuth."""
    t = math.radians(tilt_deg or 0.0)
    a = math.radians(azimuth_deg or 0.0)
    east, north = math.sin(t) * math.sin(a), math.sin(t) * math.cos(a)
    return (east, -north, math.cos(t))


def orientation_bucket(tilt_deg: Optional[float], azimuth_deg: Optional[float]) -> str:
    """flat, or the compass quarter (N/E/S/W, 90 deg wide) the plane faces."""
    if azimuth_deg is None or (tilt_deg or 0.0) < FLAT_TILT_DEG:
        return "flat"
    return ("N", "E", "S", "W")[int(((azimuth_deg % 360.0) + 45.0) // 90.0) % 4]


def _cos_sum(normal, suns) -> float:
    return sum(max(0.0, normal[0] * s[0] + normal[1] * s[1] + normal[2] * s[2]) for s in suns)


def solar_aperture(planes: Iterable, latitude_deg: float) -> SolarAperture:
    """Solar-weighted aperture of ``planes`` (RoofPlanes, or anything with
    ``area_m2``, ``tilt_deg`` and ``azimuth_deg``) at ``latitude_deg``."""
    suns = sun_vectors(latitude_deg)
    by = {k: 0.0 for k in ORIENTATIONS}
    for p in planes:
        a = float(p.area_m2 or 0.0) * _cos_sum(plane_normal(p.tilt_deg, p.azimuth_deg), suns)
        by[orientation_bucket(p.tilt_deg, p.azimuth_deg)] += a
    return SolarAperture(
        total=sum(by.values()),
        by_orientation=by,
        latitude_deg=float(latitude_deg),
        n_sun_positions=len(suns),
    )


def facet_aperture(vertices: Sequence[Sequence[float]], latitude_deg: float) -> float:
    """Aperture of one planar facet straight from its corners (canonical
    frame). Uses the Newell normal, so it does not depend on how the facet is
    split into triangles."""
    from roof_geometry import newell_normal

    n = newell_normal(vertices)
    ln = math.sqrt(sum(x * x for x in n))
    if ln == 0:
        return 0.0
    if n[2] < 0:
        n = tuple(-x for x in n)
    unit = tuple(x / ln for x in n)
    return (ln / 2.0) * _cos_sum(unit, sun_vectors(latitude_deg))


def model_aperture(model, latitude_deg: Optional[float] = None) -> SolarAperture:
    """Aperture of ``model.roof_planes``; latitude from the caller, else the
    model's site. Raises ValueError when neither gives one."""
    lat = latitude_deg if latitude_deg is not None else getattr(model, "site_latitude_deg", None)
    if lat is None:
        raise ValueError("no latitude: pass latitude_deg or set model.site_latitude_deg")
    return solar_aperture(model.roof_planes, lat)


def compound_angle_deg(parts) -> Optional[float]:
    """Decimal degrees from an IfcCompoundPlaneAngleMeasure
    (degrees, minutes, seconds[, millionths of a second]); None if absent."""
    if not parts:
        return None
    vals = [float(x) for x in parts] + [0.0] * 4
    return vals[0] + vals[1] / 60.0 + vals[2] / 3600.0 + vals[3] / 3.6e9
