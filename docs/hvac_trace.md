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

Limits: this checks room assignment on real room shapes only. The detection half below renders sheets from the same model. At exact positions every terminal is more than 0.15 m inside its room, so the #684 boundary and tie rules only come into play once jitter is added.

## Real-model detection check (#707)

`scripts/validate_clinic_hvac_detection.py` draws one mechanical sheet per storey from the same Clinic model and runs `detect_components` on it. `scripts/clinic_sheet_data.py` first extracts a JSON cache from the IFC: room footprints and names, all 440 air terminals (Supply Air becomes `diffuser`, Return and Exhaust Air become `grille`), and the plan footprint of every duct segment (pipes excluded). Neither the IFC nor the cache is committed.

The layout is real: room shapes and labels, terminal positions and types, and the duct runs, including diagonal flex ducts. The symbols are not. Terminals are drawn with matchline's own glyphs, so this tests detection in a real layout's density and clutter, not another firm's symbology. Terminals are matched within 0.5 m (25 px), the same tolerance the synthetic validation uses.

Results (two storeys, 440 terminals: 234 diffusers and 206 grilles. End to end covers the 437 the designer placed in a space):

| Storey | Diffuser P / R | Grille P / R | End to end (room correct / missed) |
|--------|----------------|--------------|------------------------------------|
| First Floor | 0.49 / 0.17 | 0.73 / 0.07 | 32 / 231 |
| Second Floor | 0.64 / 0.15 | 0.67 / 0.02 | 16 / 158 |

Every terminal that was detected landed in the right room (0 wrong, 0 review). Detection is the bottleneck, and the script's stage check shows where terminals are lost:

- **NCC proposals are fine.** 437 of the 440 terminals have a proposal of their own class within 0.5 m.
- **WiSARD rejects most real terminals as background.** At the true position of each terminal, the classifier answers `background` for 358 of 440. It also calls 33 grilles diffusers.

The classifier was trained on crops from synthetic sheets, where every terminal hangs off a vertical drop with clear space around it. On the Clinic sheets, ducts reach terminals from any side, flex ducts come in on diagonals, and room labels sit close by. Those crops fall outside what WiSARD learned. The likely fix is training-side (crops with drops from all four sides and diagonal flex, text nearby, more background drawn from real-layout clutter). That changes the detector's synthetic results, so it's tracked separately for review.

## Context-augmented classifier training (#719)

The #718 check showed the NCC proposals finding almost every Clinic terminal, then WiSARD rejecting most of them as `background`. On the synthetic sheets, every terminal hangs off a vertical drop with clear space around it, so the classifier had learned "clean context" as part of the class. `synth/mech.py::context_crops` adds diffuser and grille crops with the glyph drawn in context: 0–4 drops entering from random sides, some on a flex diagonal, plus walls, passing ducts and room labels. It also adds background crops with the same clutter and no symbol, half of them centred on a duct junction. Background crops are cut only at terminal sizes, because VAV-size cluttered crops taught the classifier that a duct through a box is background, and VAV recall fell. The detector recipe in `hvac_trace.main` and the Clinic harness now appends `CONTEXT_PER_CLASS = 600` symbol crops per class and `CONTEXT_BG = 600` background crops. With `context_per_class=0` the training set is unchanged. The Clinic sheets are a held-out test set and are never used in training.

Clinic (BSI Medical-Dental, 440 terminals), precision / recall:

| Storey | Class | Before | After |
|---|---|---|---|
| First Floor | diffuser | 0.49 / 0.17 | 0.71 / 0.99 |
| First Floor | grille | 0.73 / 0.07 | 0.86 / 0.65 |
| Second Floor | diffuser | 0.64 / 0.15 | 0.83 / 1.00 |
| Second Floor | grille | 0.67 / 0.02 | 0.97 / 0.87 |

End to end, terminals placed in the correct room went from 48 to 382 of the 437 that sit inside a room (First Floor 32 → 219, Second Floor 16 → 163). Zero went to a wrong room, before or after. Grilles called diffusers went from 33 to 50, and these are now most of the remaining false positives.

Synthetic validation (seeds 11/22/33/44, tp/fp/fn summed):

| Class | Before | After |
|---|---|---|
| vav | 8/13/2 | 8/3/2 |
| ahu | 4/0/0 | 4/0/0 |
| diffuser | 38/0/10 | 40/6/8 |
| grille | 14/0/10 | 16/0/8 |
| sensor | 17/0/7 | 17/0/7 |

Room Rand index stays at 1.0 on all four seeds. Sensor-to-zone accuracy on seed 11 dropped from 4/5 to 2/5. Synthetic diffusers pick up 6 false positives. #721 traced the seed-11 drop: the true VAV there had always been deleted by the VAV suppression rule, and before this change a false VAV happened to cover the same rooms (see below).

Variants tried: 300 per class with the default background count tripled Clinic diffuser recall but left grilles near 0.1. 600 per class with the default 1200 background collapsed synthetic VAV/AHU recall (VAV R 0.20, AHU 0) and room Rand to 0.0 on three seeds. Drawing the drops as wide as the terminal hurt both classes.

## VAV suppression no longer counts sensors (#721)

`trace_sheet` drops a VAV detection whose nearest confirmed small symbol is 25–60 px away, because the VAV template fires on diffusers and grilles. Sensors used to count as small symbols too. But a thermostat 25–60 px from its VAV box is ordinary, and on synthetic seeds 11 (42 px) and 33 (55 px) that rule deleted a VAV that `detect_components` had found at 0–1 px. Its rooms were left unzoned. Before #720, seed 11 scored 4/5 only because a false VAV at (7.0, 7.4) m happened to serve the same rooms. The rule (`suppress_vav_near_terminals`) now counts diffusers and grilles only.

Synthetic, seeds 11/22/33/44, with the #720 classifier:

| Metric | Before | After |
|---|---|---|
| VAV tp/fp/fn | 8/3/2 | 10/3/0 |
| Sensor-to-zone, seed 11 | 2/5 | 4/5 |
| Sensor-to-zone, seed 33 | 2/6 | 4/6 |
| Room Rand (all seeds) | 1.0 | 1.0 |

Other classes are unchanged. The Clinic detection check scores `detect_components` output, which comes before this rule, so its numbers are unchanged.

Room Rand only counts room pairs where both rooms got a zone, so it stayed at 1.0 while half of seed 11 was unzoned. Read it together with sensor-to-zone accuracy or zone coverage.

## Realistic synthetic sheets (#721, part 2)

`generate_mech_sheet(seed, realistic=True)` draws the same layout and ground
truth as the default sheet, with three habits seen on the BSI Clinic plans:

- about 60% of return grilles are fed from the side: the drop jogs beside the
  grille and enters its side face;
- about 50% of supply diffusers take a diagonal flex connection into a side
  face for the last 0.7 m;
- about 70% of terminals get an air-device tag (for example `SD-1 150`) beside
  them.

The tweaks use their own random stream (`[seed, 721]`), so component ids,
positions and zones are identical to the default sheet for the same seed. The
default sheets, and every existing benchmark number, are unchanged.
`validate_hvac_solution(..., realistic=True)` and
`training_crops_from_sheets(..., realistic=True)` pass the flag through.

Benchmark with the current detector (seeds 11/22/33/44, tp/fp/fn summed):

| | default sheets | realistic sheets |
|---|---|---|
| diffuser | 40/6/8 | 40/6/8 |
| grille | 16/0/8 | 13/0/11 |
| sensor-to-zone | 4/5, 4/4, 4/6, 2/2 | 2/5, 3/4, 4/6, 2/2 |
| room Rand | 1.0 all | 1.0 all |

Findings:

- **Diagonal flex breaks zoning.** Turning off only the flex tweak restores
  sensor-to-zone to 4/5, 4/4, 4/6, 2/2; turning off the side feeds or the tags
  changes nothing. The tracer's 13 px square opening (`OPEN_PX`) cannot fit
  inside a diagonal bar narrower than about 18 px, so a flex run is erased and
  its diffuser drops out of the zone. Drawing the flex at full drop width
  (15 px) does not help.
- **Training on realistic sheets does not close the gap.** Training crops from
  realistic sheets (with the #720 context crops) moved realistic grille recall
  from 13 to 14 of 24 and cost 10 correct terminals on the Clinic (382 to 372
  of 437). Without context crops, grille recall collapsed to 1 of 24.

### Fix: keep diagonal flex in the duct skeleton

`duct_skeleton` now ORs the 13 px square opening with a 9 px disk opening
(`FLEX_OPEN_PX`). A disk is the same width at every angle, so a flex run at any
slope survives, and everything the square kept is still kept. Walls, symbol
outlines and tag text (about 3 px strokes) are still removed.

Sensor-to-zone over 12 seeds (11, 22, 33, 44, 55, 66, 77, 88, 99, 111, 222,
333):

| | square only | square + disk |
|---|---|---|
| default sheets | 4/5 4/4 4/6 2/2 3/4 3/4 4/4 4/5 4/5 0/3 6/6 3/4 | identical |
| realistic sheets | 2/5 3/4 4/6 2/2 3/4 3/4 4/4 2/5 4/5 0/3 6/6 3/4 | 4/5 4/4 4/6 2/2 3/4 3/4 4/4 3/5 4/5 1/3 6/6 3/4 |

Realistic seed 111 room Rand also goes from 0.0 to 1.0; every other seed stays
1.0 on both sheet types. Detection does not use the skeleton, so detection
counts and the Clinic results are unchanged. A 9 px disk alone gave the same
sensor numbers; the union was kept because it can only add duct pixels.
