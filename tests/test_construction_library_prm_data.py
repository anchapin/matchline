"""Data-consistency tests for construction_library_prm_data (#1128).

The PRM table is generated (scripts/build_prm_library.py from ASHRAE
90.1-2019 Table G3.4 via openstudio-standards), so these tests pin the
contract construction_library.py relies on rather than any one value:
every row loads with the fields the lookup reads, keys stay unique, the
percent-of-surface bands tile their range, and the SI conversion matches
the documented factor.
"""

import pytest

from construction_library import prm_rows
from construction_library_prm_data import (
    EDITION,
    PRM_LIBRARY,
    SOURCE,
    SOURCE_URL,
    SOURCE_VERSION,
)

ROW_FIELDS = (
    "source_construction",
    "pct_min",
    "pct_max",
    "u_ip",
    "u_si",
    "f_ip",
    "c_ip",
    "shgc",
    "vt",
)


class TestTable:
    def test_nonempty_with_metadata(self):
        assert len(PRM_LIBRARY) > 300
        assert SOURCE and EDITION and SOURCE_VERSION and SOURCE_URL.startswith("https://")

    def test_keys_are_unique_under_casefold(self):
        seen = set()
        for key in PRM_LIBRARY:
            assert len(key) == 4 and all(isinstance(v, str) and v for v in key)
            folded = tuple(v.casefold() for v in key)
            assert folded not in seen, f"duplicate key under casefold: {key}"
            seen.add(folded)

    def test_every_row_has_the_fields_the_lookup_reads(self):
        for key, rows in PRM_LIBRARY.items():
            assert rows, key
            for row in rows:
                for field in ROW_FIELDS:
                    assert field in row, (key, field)
                assert isinstance(row["source_construction"], str) and row["source_construction"]

    def test_each_row_carries_exactly_one_thermal_property(self):
        # opaque assemblies use U, below-grade use F (or C on slab edge),
        # so u_ip/f_ip/c_ip never co-set and never all None
        for key, rows in PRM_LIBRARY.items():
            for row in rows:
                set_count = sum(row[f] is not None for f in ("u_ip", "f_ip", "c_ip"))
                assert set_count == 1, key

    def test_si_conversion_matches_the_documented_factor(self):
        for rows in PRM_LIBRARY.values():
            for row in rows:
                if row["u_ip"] is not None:
                    assert abs(row["u_si"] - row["u_ip"] * 5.678263) < 6e-5
                else:
                    assert row["u_si"] is None

    def test_percent_bands_tile_their_range(self):
        for key, rows in PRM_LIBRARY.items():
            if len(rows) == 1:
                assert rows[0]["pct_min"] is None
                continue
            assert rows[0]["pct_min"] == 0, key
            for prev, row in zip(rows, rows[1:]):
                assert row["pct_min"] == prev["pct_max"] + 0.001, key
                assert row["pct_max"] > row["pct_min"], key
            # bands tile upward contiguously; the last band's cap is the
            # surface's G3.4 cap (40 for vertical glazing, 100 for roofs), not
            # always 100, so only bound it here
            assert rows[-1]["pct_max"] <= 100, key

    def test_surfaces_match_table_g34(self):
        surfaces = {key[2] for key in PRM_LIBRARY}
        assert surfaces == {
            "ExteriorDoor",
            "ExteriorFloor",
            "ExteriorRoof",
            "ExteriorWall",
            "ExteriorWindow",
            "GlassDoor",
            "GroundContactFloor",
            "GroundContactWall",
            "Skylight",
        }


class TestLookupWiring:
    def test_prm_rows_resolves_zone_aliases(self):
        # zone "CZ3A" normalises to the "3A" key the table stores
        rows, zk = prm_rows("ExteriorWall", "SteelFramed", "CZ3A", "Nonresidential")
        assert zk == "3A"
        assert rows and rows[0]["u_ip"] == 0.124

    def test_prm_rows_refuses_a_malformed_zone(self):
        # a zone the table cannot parse is an error, never a silent guess
        with pytest.raises(ValueError, match="not an ASHRAE climate zone"):
            prm_rows("ExteriorWall", "SteelFramed", "CZ99", "Nonresidential")

    def test_prm_rows_unknown_construction_returns_none(self):
        rows, zk = prm_rows("ExteriorWall", "StrawBale", "CZ3A", "Nonresidential")
        assert rows is None and zk is None
