"""Hand-built deterministic BuildingModels for validation tests.

A 10 m x 6 m single-level building, two 5x6 rooms, 3 m walls. Everything
is internally consistent by construction; ``break_*`` mutators inject one
defect each so tests can assert the right check fires with the right
severity.
"""

from building_model import (  # noqa: E402
    BuildingModel,
    ComponentRef,
    EnvelopeWall,
    FixtureInstance,
    Level,
    Provenance,
    Space,
    SpaceOpening,
    Zone,
)

W, D, H = 10.0, 6.0, 3.0


def P(sheet="arch_A101", method="test", conf=1.0, note=""):
    return Provenance(sheet_id=sheet, revision=1, method=method, confidence=conf, note=note)


def make_clean_model() -> BuildingModel:
    m = BuildingModel(name="test_bldg")
    m.levels.append(Level(id="L1", name="Level 1", wall_height_m=H))

    def space(sid, num, name, poly):
        area = 5.0 * 6.0
        return Space(
            id=sid,
            level_id="L1",
            name=name,
            number=num,
            polygon_m=[list(p) for p in poly],
            area_m2=area,
            volume_m3=area * H,
            core_provenance=P(note=f"{name} {num}"),
            label_confidence=1.0,
        )

    s1 = space("L1-101", "101", "OFFICE", [[0, 0], [5, 0], [5, 6], [0, 6]])
    s2 = space("L1-102", "102", "CONF", [[5, 0], [10, 0], [10, 6], [5, 6]])

    # envelope: south/north run 10 m, east/west run 6 m
    runs = [
        ("L1-EW1", "south", [0, 6], [10, 6]),
        ("L1-EW2", "north", [10, 0], [0, 0]),
        ("L1-EW3", "east", [10, 0], [10, 6]),
        ("L1-EW4", "west", [0, 6], [0, 0]),
    ]
    for wid, fac, a, b in runs:
        import math

        L = math.dist(a, b)
        m.envelope.append(
            EnvelopeWall(
                id=wid,
                facade=fac,
                from_m=list(a),
                to_m=list(b),
                length_m=L,
                height_m=H,
                area_m2=L * H,
                provenance=P(method="arch_plan_parse"),
            )
        )

    # schedules: window tag A = 1.5 x 1.2; lighting T84 = 64 W
    m.schedules["A"] = {
        "tag": "A",
        "category": "window",
        "width_m": 1.5,
        "height_m": 1.2,
        "note": "",
        "watts": None,
        "description": "",
        "lamp_type": "",
    }
    m.schedules["T84"] = {
        "tag": "T84",
        "category": "lighting",
        "width_m": None,
        "height_m": None,
        "note": "",
        "watts": 64.0,
        "description": "2x4 troffer",
        "lamp_type": "LED",
    }

    # windows: two tag-A on the south facade of room 101
    for i, (s0, s1_) in enumerate([(1.0, 2.5), (3.0, 4.5)]):
        s1.openings.append(
            SpaceOpening(
                id=f"south-W{i + 1}",
                tag="A",
                category="window",
                width_m=1.5,
                height_m=1.2,
                sill_m=0.9,
                head_m=2.1,
                host_facade="south",
                host_interval_m=[s0, s1_],
                s_center_m=(s0 + s1_) / 2,
                area_m2=1.8,
                provenance=P("elev_A201", "grid_registration", 0.95),
            )
        )

    # lighting: 2x64 W in 101 (LPD 4.27), 1x64 W in 102 (LPD 2.13)
    for i in range(2):
        s1.lighting.fixtures.append(
            FixtureInstance(
                id=f"F{i + 1}",
                tag="T84",
                fixture_class="Troffer 2x4",
                x_m=1.0 + i * 2.0,
                y_m=3.0,
                watts=64.0,
                provenance=P("light_E101", "point_in_polygon", 0.95),
            )
        )
    s2.lighting.fixtures.append(
        FixtureInstance(
            id="F3",
            tag="T84",
            fixture_class="Troffer 2x4",
            x_m=7.5,
            y_m=3.0,
            watts=64.0,
            provenance=P("light_E101", "point_in_polygon", 0.95),
        )
    )
    for sp in (s1, s2):
        w = sum(f.watts for f in sp.lighting.fixtures)
        sp.lighting.total_w = w
        sp.lighting.lpd_w_m2 = w / sp.area_m2
        sp.lighting.lpd_w_ft2 = sp.lighting.lpd_w_m2 / 10.7639
        sp.lighting.provenance = P("light_E101", "schedule_join", 0.95)

    # one zone serving room 101
    z = Zone(
        id="L1-Z1",
        level_id="L1",
        space_ids=["L1-101"],
        duct_length_m=12.0,
        provenance=P("mech_M101", "duct_tracing", 0.9),
    )
    z.terminal_unit = ComponentRef(
        id="VAV-1",
        type="vav",
        x_m=2.5,
        y_m=5.0,
        tag="VAV-1",
        provenance=P("mech_M101", "symbol_detection", 0.9),
    )
    d = ComponentRef(
        id="D1",
        type="diffuser",
        x_m=2.5,
        y_m=3.0,
        tag="D1",
        provenance=P("mech_M101", "symbol_detection", 0.9),
    )
    s_ = ComponentRef(
        id="T1",
        type="sensor",
        x_m=1.0,
        y_m=1.0,
        tag="T1",
        provenance=P("mech_M101", "symbol_detection", 0.9),
    )
    z.diffusers.append(d)
    z.sensors.append(s_)
    s1.hvac.zone_ids.append("L1-Z1")
    s1.hvac.diffusers.append(d)
    s1.hvac.sensors.append(s_)
    s1.hvac.terminal_units.append(z.terminal_unit)
    s1.hvac.provenance = P("mech_M101", "duct_tracing", 0.9)
    m.zones["L1-Z1"] = z

    m.spaces["L1-101"] = s1
    m.spaces["L1-102"] = s2
    m.log_revision("arch_A101", 1, "ingest", "2 spaces from arch plan")
    m.log_revision("light_E101", 1, "ingest", "3 fixtures assigned")
    m.log_revision("mech_M101", 1, "ingest", "1 zone attached")
    m.log_revision("elev_A201", 1, "ingest", "2 windows linked via grid")
    return m


# --- defect mutators (each breaks exactly one invariant) ------------------


def break_area(m: BuildingModel):
    """Room 101's recorded area no longer matches its polygon/footprint."""
    m.spaces["L1-101"].area_m2 = 20.0
    m.spaces["L1-101"].volume_m3 = 60.0


def break_provenance(m: BuildingModel):
    m.spaces["L1-101"].openings[0].provenance = None


def break_space_core_provenance(m: BuildingModel):
    m.spaces["L1-101"].core_provenance = None


def break_opening_provenance(m: BuildingModel):
    m.spaces["L1-101"].openings[0].provenance = None


def break_lighting_fixture_provenance(m: BuildingModel):
    m.spaces["L1-101"].lighting.fixtures[0].provenance = None


def break_hvac_diffuser_provenance(m: BuildingModel):
    m.spaces["L1-101"].hvac.diffusers[0].provenance = None


def break_hvac_sensor_provenance(m: BuildingModel):
    m.spaces["L1-101"].hvac.sensors[0].provenance = None


def break_hvac_terminal_unit_provenance(m: BuildingModel):
    m.spaces["L1-101"].hvac.terminal_units[0].provenance = None


def break_zone_provenance(m: BuildingModel):
    m.zones["L1-Z1"].provenance = None


def break_envelope_wall_provenance(m: BuildingModel):
    m.envelope[0].provenance = None


def break_zone_empty(m: BuildingModel):
    m.zones["L1-Z1"].diffusers.clear()
    m.zones["L1-Z1"].space_ids.clear()


def break_lpd_absurd(m: BuildingModel):
    for f in m.spaces["L1-101"].lighting.fixtures:
        f.watts = 2000.0
    sp = m.spaces["L1-101"]
    w = sum(f.watts for f in sp.lighting.fixtures)
    sp.lighting.total_w = w
    sp.lighting.lpd_w_m2 = w / sp.area_m2
    sp.lighting.lpd_w_ft2 = sp.lighting.lpd_w_m2 / 10.7639


def break_dangling_zone(m: BuildingModel):
    m.spaces["L1-102"].hvac.zone_ids.append("L1-Z9")


def break_dupe_fixture(m: BuildingModel):
    f = m.spaces["L1-101"].lighting.fixtures[0]
    m.spaces["L1-102"].lighting.fixtures.append(f)


def break_opening_oversize(m: BuildingModel):
    """Add south windows whose area exceeds the south gross wall area."""
    sp = m.spaces["L1-101"]
    for i in range(20):
        sp.openings.append(
            SpaceOpening(
                id=f"south-WX{i}",
                tag="A",
                category="window",
                width_m=1.5,
                height_m=1.2,
                sill_m=0.9,
                head_m=2.1,
                host_facade="south",
                area_m2=1.8,
                provenance=P("elev_A201", "grid_registration", 0.95),
            )
        )


def break_negative_area(m: BuildingModel):
    m.spaces["L1-102"].area_m2 = -30.0


def break_untagged_opening(m: BuildingModel):
    m.spaces["L1-101"].openings[0].tag = ""


def break_window_double_link(m: BuildingModel):
    """Inject a duplicate window: same tag + s_center within 0.15 m of an existing window."""
    sp = m.spaces["L1-101"]
    sp.openings.append(
        SpaceOpening(
            id="south-W3",
            tag="A",
            category="window",
            width_m=1.5,
            height_m=1.2,
            sill_m=0.9,
            head_m=2.1,
            host_facade="south",
            host_interval_m=[1.6, 3.1],
            s_center_m=1.85,
            area_m2=1.8,
            provenance=P("elev_A202", "grid_registration", 0.95),
        )
    )


def break_fixture_no_schedule(m: BuildingModel):
    m.spaces["L1-102"].lighting.fixtures.append(
        FixtureInstance(
            id="F9",
            tag="ZZZ",
            fixture_class="Mystery",
            x_m=7.0,
            y_m=4.0,
            watts=None,
            provenance=P("light_E101", "point_in_polygon", 0.95),
        )
    )


def break_fixture_no_schedule_flagged(m: BuildingModel):
    break_fixture_no_schedule(m)
    m.flag_for_review(
        "fixture_schedule",
        "fixture F9 tag 'ZZZ' has no schedule entry",
        0.5,
        P("light_E101", "schedule_join", 0.5),
    )


def break_lpd_unit_slip(m: BuildingModel):
    """Simulate a ft^2 value leaking into the m^2 field (10.76x slip)."""
    sp = m.spaces["L1-101"]
    sp.lighting.lpd_w_ft2 = sp.lighting.lpd_w_m2  # forgot the conversion


def break_space_volume_matches_area_height(m: BuildingModel):
    """Space 101 volume no longer equals area x wall height."""
    m.spaces["L1-101"].volume_m3 = 15.0


def break_volume_conservation(m: BuildingModel):
    """Sum of space volumes no longer equals footprint x wall height."""
    m.spaces["L1-101"].volume_m3 = 15.0


def break_envelope_area_matches_perimeter(m: BuildingModel):
    """Envelope wall area no longer matches perimeter x height."""
    m.envelope[0].area_m2 = 100.0


def break_simplify_budget(m: BuildingModel):
    """Simplifier result marks area_delta_pct outside its own budget."""

    class _FakeSres:
        area_delta_pct = 8.0
        tol = 0.02
        valid = True

    m._sres = _FakeSres()


def break_simplify_invalid(m: BuildingModel):
    """Simplifier result marked invalid – simplification was rejected."""

    class _FakeSres:
        area_delta_pct = 0.0
        tol = 0.02
        valid = False

    m._sres = _FakeSres()


def break_takeoff_counts_reconcile(m: BuildingModel):
    """Count x schedule dims disagrees with recorded opening areas."""
    m.spaces["L1-101"].openings[0].area_m2 = 99.0


def break_opening_schedule_join(m: BuildingModel):
    """Opening has no inline dims and its schedule entry has no dims either."""
    m.spaces["L1-101"].openings[0].width_m = None
    m.spaces["L1-101"].openings[0].height_m = None
    m.schedules["A"]["width_m"] = None
    m.schedules["A"]["height_m"] = None


def break_sill_head_sanity(m: BuildingModel):
    """Opening sill is below floor or head exceeds wall height."""
    m.spaces["L1-101"].openings[0].sill_m = -0.5


def break_space_id_hygiene(m: BuildingModel):
    """Space id is malformed (no hyphen)."""
    m.spaces["BADID"] = m.spaces.pop("L1-101")


def break_elevation_placement_consistency(m: BuildingModel):
    """Opening has exact placement but head != sill + height."""
    m.spaces["L1-101"].openings[0].head_m = 1.0


def break_review_queue_sound(m: BuildingModel):
    """Review queue item is missing kind."""
    from building_model import ReviewItem

    m.review_queue.append(
        ReviewItem(
            id="RX",
            kind="",
            description="malformed",
            status="open",
            confidence=0.5,
            provenance=P(),
        )
    )


def break_review_queue_acknowledged(m: BuildingModel):
    """Review queue item needs_review=True but not acknowledged."""
    from building_model import ReviewItem

    m.review_queue.append(
        ReviewItem(
            id="RX-ACK",
            kind="window_room_link",
            description="unacknowledged review item",
            status="open",
            confidence=0.5,
            provenance=P(),
            needs_review=True,
            acknowledged=False,
        )
    )


def break_revision_log_present(m: BuildingModel):
    """Revision log is empty."""
    m.revision_log.clear()


def break_gbxml_spaces(m: BuildingModel):
    """gbXML parse finds wrong number of spaces."""
    import os
    import tempfile

    root = """<?xml version="1.0" encoding="UTF-8"?>
<gbXML xmlns="http://www.gbxml.org/schema" temperatureUnit="C">
  <Campus id="c1" name="test">
    <Building id="b1" name="test_bldg" buildingType="Office">
      <Space id="sp1" name="S1"><Area>30</Area><Volume>90</Volume></Space>
      <Space id="sp2" name="S2"><Area>30</Area><Volume>90</Volume></Space>
      <Space id="sp3" name="S3"><Area>30</Area><Volume>90</Volume></Space>
    </Building>
  </Campus>
</gbXML>"""
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.write(fd, root.encode())
    os.close(fd)
    m._gbxml_path = path


def break_gbxml_opening_refs(m: BuildingModel):
    """gbXML has openings not hosted on a surface with an id."""
    import os
    import tempfile

    root = """<?xml version="1.0" encoding="UTF-8"?>
<gbXML xmlns="http://www.gbxml.org/schema" temperatureUnit="C">
  <Campus id="c1" name="test">
    <Building id="b1" name="test_bldg" buildingType="Office">
      <Space id="sp1" name="S1"><Area>30</Area><Volume>90</Volume></Space>
      <Surface id="s1" surfaceType="Wall">
        <Opening id="o1"><Width>1.5</Width><Height>2.1</Height></Opening>
      </Surface>
      <Surface id="">
        <Opening id="o2"><Width>1.5</Width><Height>2.1</Height></Opening>
      </Surface>
    </Building>
  </Campus>
</gbXML>"""
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.write(fd, root.encode())
    os.close(fd)
    m._gbxml_path = path


def break_gbxml_wall_areas(m: BuildingModel):
    """Canonical wall areas differ from what gbXML export would produce."""
    import os
    import tempfile

    canonical_area = sum(w.area_m2 for w in m.envelope if getattr(w, "area_m2", 0) > 0)
    target_gbxml_area = canonical_area * 0.1
    n_walls = max(1, len([w for w in m.envelope if getattr(w, "area_m2", 0) > 0]))
    per_wall_area = target_gbxml_area / n_walls
    ns_uri = "http://www.gbxml.org/schema"
    space_ids = [f"space-{i}" for i in range(len(m.spaces))]
    sp_map = {}
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<gbXML xmlns="{ns_uri}">',
        "  <Campus>",
    ]
    for idx, (k, sp) in enumerate(m.spaces.items()):
        sid = f"space-{idx}"
        wh = getattr(sp, "wall_height_m", None) or 3.0
        sp.wall_height_m = wh
        sp.gbxml_id = sid
        sp_map[sid] = wh
        lines.append(f'    <Space id="{sid}" spaceName="{sp.name or k}"/>')
    lines.append("  </Campus>")
    for i, w in enumerate(w for w in m.envelope if getattr(w, "area_m2", 0) > 0):
        h = w.height_m or 3.0
        target_len = per_wall_area / h
        lines.append(f'    <Surface surfaceType="ExteriorWall" id="wall-{i:03d}">')
        lines.append(f'      <AdjacentSpaceId spaceIdRef="{space_ids[0]}"/>')
        lines.append("      <RectangularGeometry>")
        lines.append("        <Azimuth>0</Azimuth>")
        lines.append("        <Tilt>90</Tilt>")
        lines.append(
            "        <CartesianPoint><Coordinate>0.0</Coordinate><Coordinate>0.0</Coordinate><Coordinate>0.0</Coordinate></CartesianPoint>"
        )
        lines.append(
            f"        <CartesianPoint><Coordinate>{target_len:.6f}</Coordinate><Coordinate>0.0</Coordinate><Coordinate>0.0</Coordinate></CartesianPoint>"
        )
        lines.append("      </RectangularGeometry>")
        lines.append("      <PlanarGeometry><Polygon>")
        lines.append(
            "        <CartesianPoint><Coordinate>0.0</Coordinate><Coordinate>0.0</Coordinate><Coordinate>0.0</Coordinate></CartesianPoint>"
        )
        lines.append(
            f"        <CartesianPoint><Coordinate>{target_len:.6f}</Coordinate><Coordinate>0.0</Coordinate><Coordinate>0.0</Coordinate></CartesianPoint>"
        )
        lines.append(
            f"        <CartesianPoint><Coordinate>{target_len:.6f}</Coordinate><Coordinate>0.0</Coordinate><Coordinate>{h:.6f}</Coordinate></CartesianPoint>"
        )
        lines.append(
            f"        <CartesianPoint><Coordinate>0.0</Coordinate><Coordinate>0.0</Coordinate><Coordinate>{h:.6f}</Coordinate></CartesianPoint>"
        )
        lines.append("      </Polygon></PlanarGeometry>")
        lines.append("    </Surface>")
    lines.append("</gbXML>")
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.write(fd, "\n".join(lines).encode())
    os.close(fd)
    m._gbxml_path = path


def break_ifc_counts(m: BuildingModel):
    """IFC has fewer spaces than the model."""
    import os
    import tempfile

    ifc_content = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('test.ifc','','','','','','');
FILE_SCHEMA(('IFC2X3'));
ENDSEC;
DATA;
#1=IFCSITE('0',$,$,$,$,$,$,$,$,$,$,$,$,$,$);
#2=IFCBUILDING('0',$,$,$,$,$,$,$,$,$,$,$);
#3=IFCSPACESHELL('0',$,$,#2);
#4=IFCSPACESHELL('0',$,$,#2);
ENDSEC;
END-ISO-10303-21;
"""
    fd, path = tempfile.mkstemp(suffix=".ifc")
    os.write(fd, ifc_content.encode())
    os.close(fd)
    m._ifc_path = path


def break_room_number(m: BuildingModel):
    """Room 101's number is missing, so polygon_classify treats it as unassigned.

    When a room has no number, classify_polygons returns poly_type='unassigned'
    instead of 'room'.  This defect exercises the polygon_classify integration:
    the conservation law should fire when poly_type is wrong, but currently
    the conservation check does not filter by poly_type, so no error fires.
    """
    m.spaces["L1-101"].number = None


# -------------------------------------------------------------------------------------------------
# BEM conservation break helpers
