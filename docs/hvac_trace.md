# HVAC Zoning from Mechanical Plans

## Netlist-to-ductwork analogy

A PCB netlist describes components (resistors, ICs) connected by copper traces. A mechanical HVAC plan describes equipment (VAV boxes, AHU, diffusers, sensors) connected by ductwork. The zoning task — which rooms are served by which VAV — is analogous to netlist extraction: find the components, trace the connections, group by connectivity.

Key differences from PCBs:
- Ducts are wide filled bars (not thin traces); we skeletonize to centerlines.
- Crossings use gap symbols (not jumpers); the generator renders physical gaps.
- Supply and return are separate networks (like power and ground planes).
- Equipment tags (VAV-1, T-1) are text, not silkscreen.

## Localization and classification

**Hybrid NCC + WiSARD:**
- Normalized cross-correlation (NCC) with synthetic templates proposes candidates.
- Templates include duct stubs (spine through VAV, tap, trunk into AHU) because sheet ducts fill template-white regions; stub-less templates score <0.5 at true sites.
- WiSARD (trained on sheet-cut crops) confirms VAV/diffuser/grille and rejects background.
- Sensors use NCC-only (WiSARD unreliable on tiny symbols).
- AHU uses NCC ≥ 0.55 plus non-background; top-1 per sheet (one AHU per plan).

**Measured (8 seeds):** VAV recall 0.50–1.00, precision 0.11–0.50. False positives fire on diffusers/grilles (empty black square resembles VAV box). A 25–60px small-symbol suppression removes FPs near detected diffusers/grilles/sensors, but true VAVs can coincide with sensors (generator places them independently).

**Critical:** VAV false positives do NOT create ghost zones. The zone extractor requires diffusers in the outlet; FPs on grilles (return side) or isolated FPs yield no diffusers and form no zone.

## Graph extraction

1. **Skeletonize:** 7×7 opening removes text/symbols, Zhang-Suen thinning gives 1-px duct centerlines.
2. **Cut at VAVs:** Mask the physical VAV box (60×30 px + 10 px dilation). This severs the tap (inlet) and spine (outlet).
3. **Find adjacent components:** Ring around the cut box (box/2+4 to box/2+30).
4. **Select outlet:** Adjacent components containing diffusers within 30 px. (Inlet/tap components have no diffusers.)
5. **Rooms served:** Diffusers in outlet → room (#684). A diffuser more than 0.15 m (`ROOM_EDGE_MARGIN_M`) inside exactly one room is assigned to it outright. The rest (on or near a wall line, or outside every room) take the rooms they sit in or within 0.5 m (`NEAREST_ROOM_MARGIN_M`) of as candidates: one candidate wins; with several, the one room this zone already feeds through an interior diffuser wins (duct connectivity); otherwise the room the diffuser sits deepest in wins if it leads by more than 0.1 m (`ROOM_TIE_M`). Anything else gets no room and a `diffuser_room_ambiguous` item in the zone's `review` list (also collected in `trace_sheet(...)["review"]`), never a guess. Each zone carries `diffuser_rooms`, parallel to `diffusers`. Rooms are measured against their `polygon_m` outline when the room carries one (L-shaped and angled rooms), else their `rect_m` box; depth is the signed distance to the outline's nearest edge (#697).
6. **Sensors:** Assigned by room co-location (sensor's room → zone serving that room).
7. **Duct length:** Skeleton pixels in outlet × 0.02 m/px.

## Crossings vs junctions

The generator renders crossing gaps: when a drop crosses the trunk, the trunk is split with a physical gap (white space). The gap half-width is `kept_width/2 + 0.10 m` — it must clear the kept duct, otherwise the skeleton bridges through.

**Bug found and fixed:** The original fixed `GAP_HALF_M=0.18` (0.36 m total) was barely wider than a drop (0.30 m). The 1.5 px clearance per side was insufficient; the skeleton connected trunk segments through the drop. Widening the gap to clear the kept duct fixed the topology.

**Rendering bug found and fixed:** Duct rectangles were padded with `hw` on ALL sides, including along the axis. This filled the crossing gaps (18 px gap < 40 px end-cap overlap). Padding is now perpendicular-only.

## Validation (8 seeds: 11, 22, 33, 44, 55, 66, 77, 88)

| Metric | Result |
|---|---|
| Room-zone Rand index | **1.00 on all 8 seeds** (perfect) |
| Duct length rel. error | 0.19–0.87 (mean ~0.48) |
| Sensor-zone accuracy | 0.33–1.00 |
| VAV recall | 0.50–1.00 |
| VAV precision | 0.11–0.50 (FPs don't form zones) |
| AHU | P=1.00 R=1.00 (top-1) |
| Diffuser | P=1.00, R=0.71–0.93 |
| NCC/WiSARD agreement | 100% |

## Auditability

Each zone reports:
- VAV detection (position, NCC score, WiSARD margin)
- Number of skeleton components touching the VAV box
- Outlet skeleton pixels → duct length
- Each diffuser → room assignment
- Each sensor → room → zone

## Limitations (not production-ready)

- **Synthetic only:** Validated on generated sheets with filled duct bars. Real drawings use double-line ducts (two parallel lines); centerline extraction needs a different method (morphological or vector-based).
- **Overlapping systems:** Supply/return/exhaust drawn overlapping (not just crossing) will merge in the skeleton. Real plans use line types/colors to distinguish.
- **Leaders/text:** Equipment tags with leader lines can bridge gaps. The 7×7 opening removes most text, but long leaders survive.
- **Risers/multistorey:** Not modeled; the generator is single-storey.
- **VAV precision:** 0.11–0.50 due to diffuser/grille confusers. Zoning is robust (FPs don't form zones), but the detection needs work for a clean equipment schedule.
- **Duct length:** 19–87% error. Skeleton pixel count is approximate; real takeoffs need centerline vectorization and fitting.

## Files

- `synth/mech.py`: Generator (rooms, VAVs, ducts, gaps, GT)
- `hvac_trace.py`: Tracer (NCC+WiSARD detection, skeleton, cut-vertex zoning)
- `synth/out/mech_trace_results.json`: Latest run results

## Real-model room assignment check (#707)

`scripts/validate_clinic_hvac.py` scores diffuser-to-room assignment against a real HVAC design: buildingSMART's Medical-Dental Clinic (`Clinic_HVAC.ifc`, IFC2X3, Revit MEP 2013). It's licensed CC BY 4.0, with attribution: BSI (2020) "Medical-Dental Test Files," buildingSMART International. The 27 MB file is not committed. Download it from https://github.com/buildingsmart-community/Community-Sample-Test-Files and set `MATCHLINE_CLINIC_HVAC` to its path to run `tests/test_clinic_hvac_validation.py::test_clinic_assignment_exact_positions`. Without that variable, the test is skipped.

The designer's own containment is the ground truth: 437 of the 440 air terminals sit in an `IfcSpace`. Matchline sees only each space's floor footprint (as `polygon_m`) and each terminal's plan position, and runs them through `_assign_diffuser_room`. `--jitter` moves every terminal a fixed distance in a random direction, to mimic detector localisation error.

Results (seed 0, 257 rooms on two floors):

| Jitter | Correct | Wrong | Review |
|--------|---------|-------|--------|
| 0 m | 437 | 0 | 0 |
| 0.15 m | 437 | 0 | 0 |
| 0.30 m | 437 | 0 | 0 |
| 0.50 m | 435 | 0 | 2 (`ambiguous`) |

Limits: this checks room assignment on real room shapes only. Symbol detection is still validated on synthetic sheets, because no mechanical sheet has been rendered from this model yet. At exact positions every terminal is more than 0.15 m inside its room, so the #684 boundary and tie rules only come into play once jitter is added.
