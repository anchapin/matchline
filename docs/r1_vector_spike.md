# R1 vector spike (#973)

Date: 2026-10-15. Data: local scratch drawing sets only (`/workspace/scratch/realdwg/`,
2006-era PDFs; used for spike probing, never committed). Issue: #973.

## What the spike ran

`scripts/r1_vector_spike.py` runs three probes on one PDF page:

1. **Primitive inventory** (pdfplumber): what a plan sheet actually exposes as
   vector primitives.
2. **Vector rooms**: a stroke mask built from those primitives, dilated and
   closed into wall thickness, flood-filled; regions whose area is 0.1-12% of
   the page are counted as plausible room interiors. This is the "rooms as
   closed loops" prototype.
3. **Raster counterpart**: the shipped `door_detect.detect_door_swings` run on
   the rendered page at the two common paper scales (px/m at 144 dpi:
   ~59 at 1/8"=1'-0", ~118 at 1/4"=1'-0").

Repro:

```
python scripts/r1_vector_spike.py --pdf /workspace/scratch/realdwg/tosv.pdf --page 3
```

## Findings

- **Real legacy sheets are vector-native.** tosv p3 carries 8,108 lines,
  1,145 rects, 1,495 curves and 4,233 chars; isnrv p2 carries 5,582 lines and
  4,122 curves; andover p5 carries 6,484 lines and 7,402 curves. The input
  alphabet R1 postulated is present on 2006-era PDFs, no OCR-only fallback
  needed for geometry.
- **The shipped vector pipeline fails on routing, not extraction.** Running
  `run_pipeline.py --set tosv.pdf` ends with "no floor plan produced a room":
  the sheet indexer finds no sheet number in the 2006 title blocks, so every
  sheet is skipped as "not a floor plan for takeoff" before geometry is ever
  parsed. Filed as the follow-up issue (#1016); this is the near-term fix that
  unblocks the vector path on legacy sets.
- **Rooms as closed loops works on the primitives.** Flood fill over the
  stroke mask yields 22 plausible room regions on tosv p3 and 31 on isnrv p2
  after filtering hatching noise. The primitives alone support direct room
  extraction; the work is in classifying regions (rooms vs shafts vs stair
  cores) and joining them to wall topology.
- **The raster door sweep is scale-sensitive.** tosv p3: 61 door candidates at
  1/8" scale, 24 at 1/4"; isnrv p2: 28 and 26. Without labeled truth on these
  scratch sheets these are counts, not scores; they establish only that the
  raster path's answer moves a lot with scale, which is exactly the weakness
  vector parsing avoids.
- **VecFormer stays deferred.** VecFormer (NeurIPS 2025) is a panoptic *symbol
  spotter* over line primitives (91.1 PQ on FloorPlanCAD), not a wall/room
  parser. It does not solve the geometry-extraction question this spike asks,
  and if symbol spotting from primitives ever becomes a need it slots in
  behind the R2 DetectionProvider interface, not as a pipeline rewrite.
- **The 0.929 benchmark target stays open.** A head-to-head object F1 against
  Kamai's number needs labeled truth. AEC-Geometric-Bench is CC BY-NC 4.0
  (evaluation only, weights stay quarantined per #751) and the scratch sheets
  carry no annotations. Quantitative replication is a separate later step; this
  spike is deliberately qualitative.

## Outcome and decision

**Verdict: tie, leaning vector-for-geometry.** The geometry lives in the
primitives and parses directly; symbols are still the raster path's job today,
and the vector pipeline's real blocker on legacy sets is sheet routing, not
parsing.

Decision:

- The raster YOLO+SAHI path stays the production path. Nothing in this spike
  justifies a switch, and the door sweep's scale sensitivity argues for
  finishing the raster hardening already on the roadmap.
- Vector-native geometry parsing stays an open research track, now unblocked:
  the next concrete step on it is the #1016 routing fix, then region
  classification and wall-topology assembly on the flood-fill prototype.
- Learned vector models (VecFormer and successors) are not pursued now; any
  future use enters through the R2 DetectionProvider interface.
- The meet-or-beat-0.929 F1 target remains recorded in ROADMAP R1 and waits on
  a labeled evaluation set.
