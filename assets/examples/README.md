# matchline_l000_example.xml

Committed example BuildingSync export passing the ASHRAE 211 L000
("Preliminary Energy-Use Analysis") use case, in the style of the
BuildingSync use-cases repository's ASHRAE_211/examples files. Synthetic
building ("Test Building"); the energy totals are example numbers, not
measurements from any real building.

Regenerated from the writer itself by
`tests/test_buildingsync_use_case.py::test_committed_example_is_current`
(the test rebuilds it byte-for-byte from the same model and audit context,
then XSD- and L000-validates it), so this file cannot drift from the
writer. All values the L000 rules require come from the caller-supplied
`AuditContext`; the writer invents none of them:

- building facts: Commercial / Office / 1998 / climate zone 6A
- measured scenario: SiteEnergyUseIntensity 55.0 kBtu/ft2, EnergyCostIndex 1.85 $/ft2
- benchmark scenario: Portfolio Manager, year 2024, value 75.0

Recipe:

```python
from bem.buildingsync_export import AuditContext, write_buildingsync

ctx = AuditContext(
    building_classification="Commercial",
    occupancy_classification="Office",
    year_of_construction=1998,
    climate_zone="6A",
    site_energy_use_intensity=55.0,
    energy_cost_index=1.85,
    benchmark_tool="Portfolio Manager",
    benchmark_year=2024,
    benchmark_value=75.0,
)
write_buildingsync(model, "example.xml", audit=ctx)
```
