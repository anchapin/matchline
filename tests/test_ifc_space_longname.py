"""IfcSpace naming: LongName is the room name, Name is the room number (#580)."""

import pytest

pytest.importorskip("ifcopenshell")

from ifc_import import _space_name_number, import_ifc  # noqa: E402
from tests.test_ifc_closet_merge import _build  # noqa: E402


class _Sp:
    def __init__(self, name=None, long_name=None):
        self.Name, self.LongName = name, long_name


@pytest.mark.parametrize(
    "name,long_name,expected",
    [
        ("101", "Open Office", ("Open Office", "101", "ifc_longname")),
        ("B12a", "Lab", ("Lab", "B12a", "ifc_longname")),
        ("OPEN OFFICE 101", None, ("OPEN OFFICE", "101", "name_parse")),
        ("OPEN OFFICE 101", "Open Office", ("Open Office", "101", "ifc_longname+name_parse")),
        ("Lobby", "Main Lobby", ("Main Lobby", None, "ifc_longname+name_parse")),
        (None, "Corridor", ("Corridor", None, "ifc_longname+name_parse")),
        (None, None, ("", None, "name_parse")),
    ],
)
def test_space_name_number(name, long_name, expected):
    assert _space_name_number(_Sp(name, long_name)) == expected


def test_revit_style_space_keeps_its_name(tmp_path):
    model = import_ifc(_build(tmp_path / "t.ifc", closet_name="102", closet_long="Copy Room"))
    sp = model.spaces["L1-102"]
    assert (sp.name, sp.number) == ("Copy Room", "102")
    assert sp.label_confidence == 0.95
    assert "ifc_longname" in sp.core_provenance.note
    assert sp.poly_type == "room"
