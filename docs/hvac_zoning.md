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
5. **Rooms served:** Diffusers in outlet → room via point-in-polygon.
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
