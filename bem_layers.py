"""Layered constructions for the gbXML export (#765).

A gbXML ``Construction`` with only a ``U-value`` imports into OpenStudio as a
construction with no layers, and EnergyPlus refuses it ("Missing required
property 'outside_layer'"). Each opaque construction therefore also carries
one ``Layer`` and its ``Material`` so the model simulates as exported.

No value is invented here:

* An envelope construction keeps the U-value the model states (or the
  documented generic placeholder, as before). Its single no-mass layer has the
  resistance that reproduces that U-value once EnergyPlus adds the air films:
  ``R_layer = 1/U - R_films``.
* The air-film resistances are the ones openstudio-standards uses for the same
  job (``OpenstudioStandards::Constructions.film_coefficients_r_value`` with
  both films on, openstudio-standards 0.8.5 bundled with OpenStudio 3.11.0),
  which are the ASHRAE 90.1 Appendix A air films.
* The interior wall between two spaces is the DOE prototype partition
  ("LargeHotel Interior Wall" in openstudio-standards 90.1 data): two layers of
  "G01 13mm gypsum board". Its properties are converted here from the IP
  values in that data file.
"""

from __future__ import annotations

from bem_helpers import _el, _fmt

FILM_SOURCE = (
    "openstudio-standards 0.8.5 Constructions.film_coefficients_r_value "
    "(ASHRAE 90.1 Appendix A air films), interior + exterior"
)

# m2-K/W, inside + outside film for the intended surface type
FILM_R_SI = {
    "ExteriorWall": 0.14969365612996,
    "Roof": 0.1373659432721986,
    "SlabOnGrade": 0.1620213689877214,  # GroundContactFloor: inside film only
    "InteriorWall": 0.239509849807936,
}

# EnergyPlus rejects a Material:NoMass thinner than this resistance (m2-K/W)
MIN_LAYER_R_SI = 0.001

# "G01 13mm gypsum board" from openstudio-standards 90.1 materials (IP units):
# thickness 0.5 in, conductivity 1.10957004 Btu-in/h-ft2-F,
# density 49.9424 lb/ft3, specific heat 0.260516252 Btu/lb-F
GYPSUM_13MM = {
    "name": "G01 13mm gypsum board",
    "thickness_m": 0.5 * 0.0254,
    "conductivity_w_mk": 1.10957004 * 0.144227889,
    "density_kg_m3": 49.9424 * 16.01846337,
    "specific_heat_j_kgk": 0.260516252 * 4186.8,
}
PARTITION_SOURCE = (
    "openstudio-standards 0.8.5 90.1 data, construction 'LargeHotel Interior "
    "Wall' (2 x G01 13mm gypsum board)"
)


def layer_r_si(u_w_m2k: float, surface_type: str) -> float | None:
    """Resistance of the one no-mass layer that gives ``u_w_m2k`` with films.

    ``None`` when the stated U-value is too high for any layer to fit under
    the films (the caller keeps the U-value and reports the gap).
    """
    r = 1.0 / float(u_w_m2k) - FILM_R_SI[surface_type]
    return r if r >= MIN_LAYER_R_SI else None


def write_layered(root, cid: str, name: str, u_text: str, surface_type: str) -> str | None:
    """Construction ``cid`` with its U-value plus one matching no-mass layer.

    Returns a note when no layer could be written, else ``None``.
    """
    co = _el(root, "Construction", id=cid)
    _el(co, "Name", name)
    _el(co, "U-value", u_text, unit="WPerSquareMeterK")
    r = layer_r_si(float(u_text), surface_type)
    if r is None:
        return (
            f"{cid}: U={u_text} W/m2-K is at or above the air films alone; "
            f"no layer written, so this construction will not simulate"
        )
    _el(co, "LayerId", layerIdRef=f"lay-{cid}")
    lay = _el(root, "Layer", id=f"lay-{cid}")
    _el(lay, "MaterialId", materialIdRef=f"mat-{cid}")
    mat = _el(root, "Material", id=f"mat-{cid}")
    _el(mat, "Name", f"{name} (no-mass layer, R = 1/U - air films)")
    _el(mat, "R-value", _fmt(r), unit="SquareMeterKPerW")
    return None


def write_partition(root, cid: str = "const-intwall") -> None:
    """The interior wall construction: two layers of 13 mm gypsum board."""
    co = _el(root, "Construction", id=cid)
    _el(co, "Name", "Interior wall (DOE prototype partition, 2 x 13 mm gypsum)")
    _el(co, "LayerId", layerIdRef=f"lay-{cid}")
    lay = _el(root, "Layer", id=f"lay-{cid}")
    # outside to inside: the same board on both faces
    _el(lay, "MaterialId", materialIdRef="mat-gypsum-13mm")
    _el(lay, "MaterialId", materialIdRef="mat-gypsum-13mm")
    g = GYPSUM_13MM
    mat = _el(root, "Material", id="mat-gypsum-13mm")
    _el(mat, "Name", g["name"])
    _el(mat, "Thickness", _fmt(g["thickness_m"]), unit="Meters")
    _el(mat, "Conductivity", _fmt(g["conductivity_w_mk"]), unit="WPerMeterK")
    _el(mat, "Density", _fmt(g["density_kg_m3"]), unit="KgPerCubicM")
    _el(mat, "SpecificHeat", _fmt(g["specific_heat_j_kgk"]), unit="JPerKgK")
