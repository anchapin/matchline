# BuildingSync mapping — spike findings (#1235)

What a BuildingSync export from matchline must look like so the DOE Audit
Template imports it. This is the spike's evidence and the contract for the
writer (#1236); it records the pinned schema version, the structure the
Audit Template actually emits and reads, and the mapping from matchline's
model. Reference data is never committed; how to regenerate it is below.

## Pinned target: BuildingSync 2.6.0

The Audit Template (Release 2026.01.01, verified live 2026-10-21) both
exports and imports **BuildingSync 2.6.0**. Evidence, both from the tool
itself at buildingenergyscore.energy.gov:

- Export: the Demo ASHRAE L2 example building duplicated into a scratch
  account, then `GET /rp/buildings/<id>/report.xml`, downloaded
  211,941 bytes, root
  `<auc:BuildingSync version="2.6.0" xmlns:auc="http://buildingsync.net/schemas/bedes-auc/2019">`.
- Import: that exact file uploaded at `/rp/buildings/upload` →
  "Imported 1 Audit Template Building … The uploaded file was successfully
  imported", creating building 53693 from 53692. So the writer should emit
  2.6.0; a 2.7.0 target is wrong for this import path today.

The v2.6.0 XSD (911,212 bytes) is the release asset at
`github.com/BuildingSync/schema` tag `v2.6.0` — same pattern as the gbXML
8.01 XSD in `assets/schemas/` for validation (#1239), fetched with lxml.

## Structure the Audit Template emits

Top level (per the reference XML, element counts in parens):
`Facilities/Facility > (Contacts (2), Measures (9), Reports (1),
Sites (1), Systems)`. The Systems subtree is facility-level, not inside
Building: WallSystems (1), RoofSystems (1), FenestrationSystems (2),
FoundationSystems (2), ExteriorFloorSystems (2), LightingSystems (142),
PlugLoads (42), DomesticHotWaterSystems (62), CriticalITSystems (66),
OnsiteStorageTransmissionGenerationSystems (46), AirInfiltrationSystems (5),
FanSystems (8), HVACSystems (3).

Building: `Sites/Site/Buildings/Building` with PremisesName,
PremisesNotes, PremisesIdentifiers (3), Address, eGRIDSubregionCodes,
FloorAreas (Gross/Net/Conditioned/Treated), ConditionedFloorsAboveGrade /
BelowGrade, TotalExteriorAboveGradeWallArea,
TotalCommonConditionedAboveGradeWallArea, OverallWindowToWallRatio,
YearOfConstruction, PercentOccupiedByOwner / PercentLeasedByOwner,
MultiTenant, BuildingAutomationSystem, HistoricalLandmark, and Sections (2).

Sections are the ATT's premises units for areas: the demo has one
`SectionType=Whole building` (FootprintShape=Rectangular, Sides referencing
WallID / RoofID / FoundationID / DoorID) and one
`SectionType=Space function` carrying OccupancyClassification
(Multifamily), occupant quantities (898 peak, 168 h/week, 52 weeks/year),
SpatialUnits (Apartment units, 272, 90% occupied) and per-section
FloorAreas (Gross and Conditioned, 248,800 ft²).

HVAC in the demo export is simple: one boiler-ish `HVACSystem`
(BoilerType Hot water, OutputCapacity 3135 kBtu/hr, AFUE 86, Quantity 1,
PrimaryFuel Natural gas), one radiant `HVACSystem` whose `Delivery/
DeliveryType/ZoneEquipment` carries RadiantType Radiator, one cooling
`HVACSystem` (EER 11, 900 units, AirDeliveryType Local fan,
VentilationType None). A FanSystem carries FanControlType
(Variable Volume). The 2.6.0 **schema** supports much more than the demo
demonstrates: under `HVACSystemType` there are
`Plants/HeatingPlants/HeatingPlant/Boiler`,
`Plants/CoolingPlants/CoolingPlant/Chiller`, `DuctSystems/DuctSystem`, and
`Deliveries/Delivery/DeliveryType` with `ZoneEquipment` and
`CentralAirDistribution/TerminalUnit`. There are **no** AirHandlingUnit,
Pump or AirLoop elements in 2.6.0 — air-side systems must be modeled with
the pieces above. Zoning lives under
`Building/Sections/Section/ThermalZones/ThermalZone`, which contains
`Spaces` (the demo export has none; Sections alone carry its areas).

Audit-report furniture: `Reports/Report` has Scenarios (917 elements:
ScenarioName/Notes, TemporalStatus, ScenarioType, AllResourceTotals,
LinkedPremises), AuditDates, AuditFilingStatus, EarlyCompliance,
Qualifications, AuditorContactID, LinkedPremisesOrSystem.
`Contacts` carry role/address/email/phone referenced by AuditorContactID.

## The UserDefinedFields round-trip channel

The reference file carries **456 UserDefinedFields (250 distinct names)**.
The ATT round-trips every editor value with no canonical element this way
(e.g. Section UDF "Miscellaneous Electric Load" = 200.0, plus "X Is Not
Applicable" flags, "Linked Time Series ID", 179D/rebate metadata).
Implication: anything matchline carries that 2.6.0 has no slot for should
go into a UserDefinedField with a stable, namespaced FieldName rather than
be dropped — that is what keeps an ATT re-import lossless.

## Mapping from matchline's model

| matchline | BuildingSync 2.6.0 | notes |
|---|---|---|
| building metadata (name, address, year, floors) | Building PremisesName/Address/FloorAreas/YearOfConstruction/ConditionedFloorsAboveGrade | direct |
| Space | ThermalZone > Spaces (new Section per story or use) | schema-only in the demo; writer constructs per XSD |
| Zone (many-to-many with spaces) | ThermalZone | zone.space_ids → ThermalZone/Spaces; many-to-many means one Space can appear in several ThermalZones |
| EnvelopeWall + Construction | WallSystems/WallSystem (+ Sides/WallID refs from Sections) | ATT puts envelope systems at Facility level, referenced by ID |
| SpaceOpening (glazing) | FenestrationSystems/FenestrationSystem | frame/glass/layers enum values come from the ATT export vocabulary |
| roof planes | RoofSystems/RoofSystem | construction + R-value; RoofID referenced from Section Sides |
| slab / below-grade | FoundationSystems/FoundationSystem (SlabRValue seen) | |
| SpaceLighting / FixtureInstance | LightingSystems/LightingSystem | LampLabel, BallastType, ControlStrategy (repeated), FloorAreaType + FloorAreaPercentage to attach to a section |
| SpaceUse / occupant loads | Section OccupancyClassification + occupant quantities + SpatialUnits | the demo's multifamily section is the pattern |
| MechSystem (scheduled equipment) | HVACSystem (+ Plants/HeatingPlant/Boiler, CoolingPlant/Chiller) | capacities as stated, units in CapacityUnits; never converted |
| AirLoop (AHU/RTU, #746) | HVACSystem + FanSystem + Delivery/DuctSystem | no AirLoop element in 2.6.0; fan airflow → FanSystem, coils → plant children, terminals → Delivery/DeliveryType |
| Zone.terminal_unit / diffusers | Delivery/CentralAirDistribution/TerminalUnit | |
| extras with no slot | UserDefinedField | namespaced FieldName, stable naming |

Geometry is the known lossy area: 2.6.0 carries Sections with
FootprintShape/Sides and orientation, not gbXML-style per-surface
polyloops. A full geometric export stays gbXML's job; BuildingSync is the
audit-report interchange (areas, constructions, systems, loads, measures).

## Recommended shape for #1236

- New exporter `bem/buildingsync_export.py` alongside `bem_export.py`,
  emitting BuildingSync 2.6.0 (root version attr fixed at 2.6.0), with the
  same unit-clean intermediate model `model_from_takeoff()` already
  produces; meters stay the authoring unit, CapacityUnits carries units.
- Validate with lxml against the v2.6.0 XSD added to `assets/schemas/`
  (same local-fix discipline as the gbXML 8.01 XSD: keep a test that the
  rest is byte-identical to the published file).
- Reference document for tests: regenerate per the next section; the XML
  itself is never committed.
- Acceptance: a matchline-exported building imports into the Audit
  Template at Release 2026.01.01 with its areas, constructions, systems
  and loads intact (the round-trip above is the harness pattern).

## Regenerating the reference XML

1. Sign in at buildingenergyscore.energy.gov (sign-up is free; no real
   personal data needed beyond an email).
2. Buildings → the "Demo ASHRAE L2 Example Building" example, Duplicate.
3. Download dropdown → XML; the URL pattern is
   `/rp/buildings/<id>/report.xml`.
4. To smoke-test an import, `/rp/buildings/upload` accepts the exported
   XML and lists each imported building by its `@ID`
   (`/BuildingSync/Facilities/Facility/Sites/Site/Buildings/Building`).

## Use-case validation in CI (#1239)

The #1238 writer's XSD round-trip proves shape; it cannot prove that an
export carries what a use case requires. The CI gate therefore layers a
Schematron pass on top, against the ASHRAE 211 use cases published in the
BuildingSync/use-cases repository:

- `assets/schemas/BuildingSync_v2_6_0.xsd` — structure, validated by
  `bem.buildingsync_export.validate_buildingsync` on every fixture export
  (unchanged from #1238).
- `assets/schematron/L000_Audit-1.0.0.sch` — the ASHRAE 211 L000
  "Preliminary Energy-Use Analysis" use case, committed byte-identical
  from upstream (see assets/schematron/README.md for provenance) and
  validated by `bem.buildingsync_export.validate_buildingsync_use_case`.
  Its asserted element names all bind against 2.6.0 documents, which is
  what matchline writes and the Audit Template speaks, despite the
  upstream schemaVersion metadata reading 1.0.0-2.7.0.
- `assets/examples/matchline_l000_example.xml` — a committed example
  export passing both gates, regenerated byte-for-byte by a test so it
  cannot drift from the writer.

### What L000 requires and where it comes from

L000 requires building facts (classification, occupancy, year of
construction, plus a climate zone or an address), a Measured scenario
with SiteEnergyUseIntensity and EnergyCostIndex, a Benchmark scenario
(tool + year), and the report's ASHRAEAuditLevel. The writer never
invents any of these: an optional `AuditContext` on `write_buildingsync`
carries caller-supplied values, and only what is supplied is emitted.
Unset fields produce honest absence — the export stays the #1238
equipment-interchange shape, the L000 gate fails on the missing pieces,
and the failure is the truth rather than a fabricated number.

Use-case choice rationale: L000 is the only ASHRAE 211 use case whose
required facts match what a matchline model plus a caller can honestly
supply. L100/L200 require surveyed premises details, utility billing
tables and target/measure packages that a schedule-truth pipeline does
not produce; asserting them would force fabrication.

The remaining acceptance step outside CI is the manual Audit Template
round-trip (buildings 53692/53693, Release 2026.01.01): upload the
export, confirm the import list, and keep at least one file that passes
both gates loadable in the Audit Template.
