"""BuildingSync 2.x writer (#1238): sibling of the gbXML export.

Emits the audit-report interchange the DOE Audit Template imports
(BuildingSync 2.6.0 is what Release 2026.01.01 exports and imports; the
mapping evidence lives in docs/buildingsync_mapping.md). The target schema
version is a parameter, not hardcoded.

Scope follows the issue: building, zones and areas, HVAC equipment with
the capacities and airflow the schedules state, zone equipment, serving
references, and envelope components. Geometry is not dumped: BuildingSync
wants far less of it than gbXML, and a full geometric export stays gbXML's
job. Records with no natural source in a drawing set (audit contacts,
audit reports) get minimal valid stand-ins rather than over-fitted data.

Values the 2.6.0 schema has no slot for, or would mislabel, travel in
UserDefinedFields with a stable ``matchline:`` FieldName instead of being
dropped or converted: scheduled capacities (Btu/h), fan airflow (L/s) and
U-values (W/m2K). Wall/roof/slab insulation R-values are NOT derived:
the schema and the Audit Template read R-values as IP, and a converted
SI number would claim a unit the file cannot carry.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from bem.bem_geometry import BEMModel

AUC_NS = "http://buildingsync.net/schemas/bedes-auc/2019"
DEFAULT_VERSION = "2.6.0"
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "assets" / "schemas"
UDF_PREFIX = "matchline:"

_NCNAME_BAD = re.compile(r"[^A-Za-z0-9_.-]")


def _el(parent, tag, text=None, **attrib):
    """AUC-namespaced child element; bem_helpers._el is pinned to gbXML."""
    el = ET.SubElement(parent, f"{{{AUC_NS}}}{tag}", attrib)
    if text is not None:
        el.text = str(text)
    return el


def _schema_path(version: str = DEFAULT_VERSION) -> Path:
    """The XSD shipped for ``version`` (2.6.0 ships in assets/schemas)."""
    return SCHEMA_DIR / f"BuildingSync_v{version.replace('.', '_')}.xsd"


def _id_safe(value: str) -> str:
    """Make a string safe as an xs:ID / xs:IDREF (NCName)."""
    cleaned = _NCNAME_BAD.sub("-", str(value)).strip("-")
    return cleaned or "x"


def _udf(parent, name: str, value) -> None:
    """UserDefinedField under the element's single UserDefinedFields wrapper.

    The schema allows one UserDefinedFields element per parent, so repeated
    calls fold into the same wrapper instead of emitting another.
    """
    udfs = parent.find(f"{{{AUC_NS}}}UserDefinedFields")
    if udfs is None:
        udfs = _el(parent, "UserDefinedFields")
    udf = _el(udfs, "UserDefinedField")
    _el(udf, "FieldName", name)
    _el(udf, "FieldValue", str(value))


def _zone_deliveries(model: BEMModel) -> tuple[dict, dict]:
    """Delivery ids per air loop and per zone, from the serving map.

    Zones follow the gbXML zone ids (_write_zones in bem_export.py):
    ``zone-{zid}`` for each model.zones entry, ``zone-1`` on the default
    zone. Returns (loop_id -> [(zone_key, delivery_id)], zone_key -> [delivery_id]).
    """
    zone_of: dict = {}
    zone_entries = list(getattr(model, "zones", None) or [])
    if zone_entries:
        for zid, _sids in zone_entries:
            zone_of[f"zone-{zid}"] = zid
    else:
        zone_of["zone-1"] = "1"
    serving = dict(getattr(model, "zone_air_loop", None) or {})
    loop_zones: dict = {}
    for zone_key, loop_id in serving.items():
        if zone_key in zone_of and loop_id:
            loop_zones.setdefault(loop_id, []).append(zone_key)
    deliveries: dict = {}
    by_zone: dict = {}
    for lp in getattr(model, "air_loops", None) or []:
        entries = []
        for zone_key in sorted(loop_zones.get(lp["id"], [])):
            did = f"DeliveryType-{_id_safe(lp['id'])}-{_id_safe(zone_key)}"
            entries.append((zone_key, did))
            by_zone.setdefault(zone_key, []).append(did)
        deliveries[lp["id"]] = entries
    return deliveries, by_zone


def _write_contacts(facility) -> None:
    """Minimal valid stand-in: a drawing set has no audit contacts (#1238)."""
    contacts = _el(facility, "Contacts")
    contact = _el(contacts, "Contact", ID="ContactType-1")
    roles = _el(contact, "ContactRoles")
    _el(roles, "ContactRole", "Energy Auditor")
    _el(contact, "ContactName", "Not specified")


def _write_reports(facility) -> None:
    """Minimal valid stand-in: matchline is not an audit report (#1238)."""
    reports = _el(facility, "Reports")
    report = _el(reports, "Report", ID="ReportType-1")
    scenarios = _el(report, "Scenarios")
    scenario = _el(scenarios, "Scenario", ID="ScenarioType-1")
    _el(scenario, "ScenarioName", "As-is")


def _write_site_building(facility, model: BEMModel, by_zone: dict) -> None:
    """Sites/Site/Buildings/Building with areas, spaces and thermal zones."""
    sites = _el(facility, "Sites")
    site = _el(sites, "Site", ID="SiteType-1")
    buildings = _el(site, "Buildings")
    building = _el(buildings, "Building", ID="BuildingType-1")
    _el(building, "PremisesName", model.building_name)
    gross = sum(sp.area_m2 for sp in model.spaces)
    if gross:
        floor_areas = _el(building, "FloorAreas")
        floor_area = _el(floor_areas, "FloorArea")
        _el(floor_area, "FloorAreaType", "Gross")
        _el(floor_area, "FloorAreaValue", f"{gross:.2f}")
    sections = _el(building, "Sections")
    section = _el(sections, "Section", ID="SectionType-1")
    _el(section, "SectionType", "Whole building")
    if gross:
        sec_areas = _el(section, "FloorAreas")
        sec_area = _el(sec_areas, "FloorArea")
        _el(sec_area, "FloorAreaType", "Gross")
        _el(sec_area, "FloorAreaValue", f"{gross:.2f}")
    zone_entries = list(getattr(model, "zones", None) or [])
    if not zone_entries:
        zone_entries = [("1", [sp.sid for sp in model.spaces])] if model.spaces else []
    if zone_entries:
        thermal_zones = _el(section, "ThermalZones")
        flow_per_area = dict(getattr(model, "zone_flow_per_area", None) or {})
        spaces_by_sid = {sp.sid: sp for sp in model.spaces}
        written_sids: set = set()
        for zid, sids in zone_entries:
            zone_key = f"zone-{zid}"
            thermal_zone = _el(thermal_zones, "ThermalZone", ID=f"ThermalZone-{_id_safe(zid)}")
            delivery_ids = by_zone.get(zone_key) or []
            if delivery_ids:
                ids_el = _el(thermal_zone, "DeliveryIDs")
                for did in delivery_ids:
                    _el(ids_el, "DeliveryID", IDref=did)
            fresh = [sid for sid in sids if sid in spaces_by_sid and sid not in written_sids]
            written_sids.update(fresh)
            if fresh:
                zone_spaces = _el(thermal_zone, "Spaces")
                for sid in fresh:
                    sp = spaces_by_sid[sid]
                    space = _el(zone_spaces, "Space", ID=f"Space-{_id_safe(sid)}")
                    _el(space, "PremisesName", sp.name)
                    sp_areas = _el(space, "FloorAreas")
                    sp_area = _el(sp_areas, "FloorArea")
                    _el(sp_area, "FloorAreaType", "Gross")
                    _el(sp_area, "FloorAreaValue", f"{sp.area_m2:.2f}")
            fpa = flow_per_area.get(zone_key)
            if fpa:
                _udf(
                    thermal_zone,
                    "matchline:zone_flow_per_area_m3s_per_m2",
                    f"{fpa:.6g}",
                )


def _write_envelope_systems(systems, model: BEMModel) -> None:
    """Envelope systems at facility level, values in namespaced UDFs.

    Systems children follow the schema order the Audit Template's own
    export uses: wall, roof, fenestration, foundation, then the mechanical
    systems after them.
    """
    wall_us = sorted({sp.wall_u_value_w_m2k for sp in model.spaces if sp.wall_u_value_w_m2k})
    if wall_us:
        walls_el = _el(systems, "WallSystems")
        for i, u in enumerate(wall_us, 1):
            wall = _el(walls_el, "WallSystem", ID=f"WallSystemType-{i}")
            _udf(wall, "matchline:wall_u_value_w_m2k", f"{u:.4f}")
    if model.roof_u_value_w_m2k:
        roofs_el = _el(systems, "RoofSystems")
        roof = _el(roofs_el, "RoofSystem", ID="RoofSystemType-1")
        _udf(roof, "matchline:roof_u_value_w_m2k", f"{model.roof_u_value_w_m2k:.4f}")
    window_constructions = [
        (cid, info)
        for cid, info in (getattr(model, "opening_constructions", None) or {}).items()
        if info.get("category") == "window"
    ]
    if window_constructions:
        fenestration_el = _el(systems, "FenestrationSystems")
        for cid, info in window_constructions:
            fen_id = f"FenestrationSystemType-{_id_safe(cid)}"
            fen = _el(fenestration_el, "FenestrationSystem", ID=fen_id)
            fenestration_type = _el(fen, "FenestrationType")
            _el(fenestration_type, "Window")
            if info.get("u"):
                _udf(fen, "matchline:u_value_w_m2k", f"{info['u']:.4f}")
            if info.get("shgc"):
                _udf(fen, "matchline:shgc", f"{info['shgc']:.3f}")
    if model.slab_u_value_w_m2k:
        foundations_el = _el(systems, "FoundationSystems")
        foundation = _el(foundations_el, "FoundationSystem", ID="FoundationSystemType-1")
        _udf(foundation, "matchline:slab_u_value_w_m2k", f"{model.slab_u_value_w_m2k:.4f}")


def _write_mechanical_systems(systems, model: BEMModel, deliveries: dict) -> None:
    """One FanSystem + HVACSystem per scheduled air loop, deliveries per zone.

    2.6.0 has no AirLoop, AirHandlingUnit or Pump element, so an air-side
    system is modeled with HVACSystem + FanSystem + Delivery, and scheduled
    capacities and types travel in UserDefinedFields rather than being
    squeezed into plant shapes the schedules do not state.
    """
    loops = list(getattr(model, "air_loops", None) or [])
    if not loops:
        return
    hvacs_el = _el(systems, "HVACSystems")
    fans_el = _el(systems, "FanSystems")
    terminals = dict(getattr(model, "zone_terminals", None) or {})
    # the schema sequence puts HVACSystems before FanSystems, so all
    # HVACSystem elements are written first
    hvac_els = []
    for lp in loops:
        lid = _id_safe(lp["id"])
        hvac = _el(hvacs_el, "HVACSystem", ID=f"HVACSystemType-{lid}")
        hvac_els.append(hvac)
        entries = deliveries.get(lp["id"]) or []
        if entries:
            # Deliveries nests under HeatingAndCoolingSystems, not directly
            # under HVACSystem (the XSD has no top-level Deliveries there).
            hcs = _el(hvac, "HeatingAndCoolingSystems")
            deliveries_el = _el(hcs, "Deliveries")
            for zone_key, did in entries:
                delivery = _el(deliveries_el, "Delivery", ID=did)
                delivery_type = _el(delivery, "DeliveryType")
                cad = _el(delivery_type, "CentralAirDistribution")
                _el(cad, "AirDeliveryType", "Central fan")
                zid = zone_key[len("zone-") :] if zone_key.startswith("zone-") else zone_key
                diffusers = terminals.get(zid) or []
                if diffusers:
                    # TerminalUnit carries no child content in 2.6.0; its
                    # presence marks the zone equipment, the counts travel
                    # on the HVACSystem's UDFs.
                    _el(cad, "TerminalUnit")
                    _udf(hvac, f"matchline:diffuser_count_{_id_safe(zid)}", str(len(diffusers)))
                    tags = sorted({t for _d, t, _x, _y in diffusers if t})
                    if tags:
                        _udf(hvac, f"matchline:diffuser_tags_{_id_safe(zid)}", ",".join(tags))
        if lp.get("sources"):
            _udf(hvac, "matchline:schedule_sources", "; ".join(lp["sources"]))
        if lp.get("system_type"):
            _udf(hvac, "matchline:system_type", lp["system_type"])
        for key in ("cooling_total_btuh", "cooling_sensible_btuh", "heating_btuh"):
            if lp.get(key):
                _udf(hvac, f"matchline:{key}", f"{float(lp[key]):.4f}")
    for lp in loops:
        lid = _id_safe(lp["id"])
        fan = _el(fans_el, "FanSystem", ID=f"FanSystemType-{lid}")
        linked = _el(fan, "LinkedSystemIDs")
        _el(linked, "LinkedSystemID", IDref=f"HVACSystemType-{lid}")
        if lp.get("fan_airflow_lps"):
            _udf(fan, "matchline:fan_airflow_lps", f"{lp['fan_airflow_lps']:.4f}")


def write_buildingsync(model: BEMModel, path: str | Path, version: str = DEFAULT_VERSION) -> Path:
    """Write ``model`` as BuildingSync ``version``; returns the path."""
    path = Path(path)
    ET.register_namespace("auc", AUC_NS)
    root = ET.Element(f"{{{AUC_NS}}}BuildingSync", {"version": version})
    facilities = _el(root, "Facilities")
    facility = _el(facilities, "Facility", ID="FacilityType-1")
    deliveries, by_zone = _zone_deliveries(model)
    _write_site_building(facility, model, by_zone)
    systems = _el(facility, "Systems")
    # Systems children follow the schema sequence: HVACSystems first, then
    # FanSystems, then the envelope systems (wall, roof, fenestration,
    # foundation).
    _write_mechanical_systems(systems, model, deliveries)
    _write_envelope_systems(systems, model)
    _write_reports(facility)
    _write_contacts(facility)
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(path, xml_declaration=True, encoding="utf-8")
    return path


_GEOMETRY_TAGS = {"PolyLoop", "PlanarCoordinate", "CartesianPoint", "Vertex"}


def _semantic_checks(root) -> list:
    """Beyond the XSD: IDrefs resolve, and no geometry got dumped."""
    errors: list = []
    ids = {el.get("ID") for el in root.iter() if el.get("ID")}
    for el in root.iter():
        for key, value in el.attrib.items():
            if key.endswith("IDref") and value not in ids:
                errors.append(f"dangling {key}={value} on {el.tag.split('}')[-1]}")
    for el in root.iter():
        local = el.tag.split("}")[-1]
        if local in _GEOMETRY_TAGS:
            errors.append(
                f"geometry element {local} present; BuildingSync export must not dump geometry"
            )
            break
    return errors


def _buildingsync_smoke_check(path: str, errors: list) -> tuple[bool, list]:
    """Well-formedness + key-element checks when lxml or the XSD is absent."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        return False, errors + [f"not well-formed: {e}"]
    root = tree.getroot()
    if not root.tag.endswith("BuildingSync"):
        errors.append("root element is not BuildingSync")
    if not root.get("version"):
        errors.append("missing version attribute on BuildingSync root")
    if not any(el.tag.endswith("Building") for el in root.iter()):
        errors.append("no Building element found")
    source = root.getroot() if hasattr(root, "getroot") else root
    return not errors, errors + _semantic_checks(source)


def validate_buildingsync(
    path: str | Path, xsd_path: str | Path | None = None
) -> tuple[bool, list]:
    """Validate a BuildingSync file against its XSD with lxml.

    The XSD is picked per the file's declared version when xsd_path is
    None. Falls back to well-formedness + key element checks if lxml or
    the XSD is unavailable.
    """
    errors: list = []
    try:
        from lxml import etree
    except ImportError:
        errors.append("lxml not installed; skipping XSD validation")
        return _buildingsync_smoke_check(str(path), errors)
    version = DEFAULT_VERSION
    try:
        parsed = ET.parse(str(path))
        declared = parsed.getroot().get("version")
        if declared:
            version = declared
    except ET.ParseError as e:
        return False, [f"not well-formed: {e}"]
    xsd_path = xsd_path or _schema_path(version)
    if not Path(xsd_path).exists():
        errors.append(f"XSD not found at {xsd_path}; skipping XSD validation")
        return _buildingsync_smoke_check(str(path), errors)
    try:
        # Harden against entity-expansion / XXE: same untrusted-input
        # discipline as the gbXML validator.
        safe_parser = etree.XMLParser(resolve_entities=False, no_network=True)
        schema = etree.XMLSchema(etree.parse(str(xsd_path), safe_parser))
        tree = etree.parse(str(path), safe_parser)
        root = tree.getroot()
        ok = schema.validate(root)
        for e in schema.error_log:
            errors.append(f"line {e.line}: {e.message}")
        if ok:
            errors.extend(_semantic_checks(root))
        return ok and not errors, errors
    except etree.XMLSyntaxError as e:
        return False, [f"not well-formed: {e}"]
