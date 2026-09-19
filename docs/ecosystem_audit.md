# Ecosystem audit — detection / vectorization front-ends

Date: 2026-09-19. Purpose: decide what existing open-source work to ADOPT for
the crowded portion (symbol detection / plan vectorization) so Jesse-Vision
effort concentrates on the novel layers (count×schedule takeoffs, HVAC zoning,
area-budgeted simplification, gbXML/IFC export).

Method: read each repo's README, LICENSE, file tree, and commit history via
the GitHub API (2026-09-19). License SPDX IDs below are from the license file
text, not the GitHub sidebar guess.

---

## 1. BreakingCAD — rivu-nyu/BreakingCAD

- **What it is:** PNG/JPEG → DXF converter for floor plans. README advertises an
  "AI pipeline": M-LSD line detection + quantized YOLOv8-Nano symbols +
  PaddleOCR + GNN junction snapping.
- **What the code actually does:** classical OpenCV only. `local_png_to_dxf.py`
  uses `cv2.LSD` (the classic Line Segment Detector — *not* the neural M-LSD),
  HoughLinesP fallback, MSER text masking, axis snapping, collinear merging,
  then DXF export. `src/vision.py` is a **mock returning hardcoded
  primitives** ("ML Engineer 1 replaces this mock data"). No YOLO, no OCR, no
  GNN anywhere in the repo.
- **Maturity:** single-commit dump (created 2025-09-18, never touched again),
  1 star, 0 issues. Dead on arrival.
- **License:** MIT (Copyright 2025 PaperCAD Edge Team). Commercial use OK.
- **Weights:** none shipped.
- **Reuse value:** the LSD → snap → merge → DXF chain is a reasonable classical
  line-vectorization reference, but it is ~500 lines of standard OpenCV, not a
  detector. Nothing to adopt for symbol detection.
- **Red flags:** README substantially misrepresents the implementation
  (aspirational "AI pipeline" section). Do not cite its claimed architecture.

## 2. PID_Symbol_Detection — mgupta70/PID_Symbol_Detection

- **What it is:** semi-supervised symbol detection for P&ID (piping &
  instrumentation) drawings. Published: Gupta, Wei, Czerniawski, *Automation in
  Construction* 159 (2024). Two stages:
  1. Train a **class-agnostic** YOLO detector ("is there a symbol here?") on
     tiled overlapping patches, inference via SAHI for large sheets.
  2. **One-shot label transfer**: classify each detection from a single labeled
     example per class taken from the drawing's own legend (Siamese/prototypical
     or augmented classifier).
- **Maturity:** 59 stars, active through 2025-04 (demo stage-2 model + training
  history uploaded). Research repo, not a maintained product, but the paper is
  peer-reviewed and the code is complete enough to run.
- **License:** MIT (Copyright 2024 Mohit Gupta). Commercial use OK.
- **Weights:** ships Ultralytics base weights (`yolo11n.pt`, `yolov8n.pt`,
  COCO-pretrained — fine to reuse) plus a demo few-shot P&ID classifier
  (`best_fewshot_model.pth`, 95 MB). **No pretrained architectural-symbol
  detector** — stage 1 must be trained on your own drawings; the repo includes
  only 5 sample P&ID images.
- **Reuse value (high, conceptual + partial code):** the stage-2 idea is the
  single best fit found for our schedule pipeline — **learn the symbol from the
  sheet's own legend/schedule** instead of pre-training every window/light/
  fixture class. SAHI tiling for huge sheets is directly reusable. Stage-1
  retraining on commercial architectural drawings is required work, not
  drop-in.
- **Limitations:** P&ID domain (industrial schematics), not architecture;
  class list is whatever you train; needs GPU for training (inference is
  YOLO-nano-class, CPU-feasible).
- **Red flags:** none on license; main cost is the retraining step.

## 3. ifc-overlay — akshatgupta-dev/ifc-overlay

- **What it is:** browser demo overlaying 2D plan PDFs onto 3D IFC models
  (Three.js + ONNX Runtime YOLO in browser + least-squares 2D→3D calibration).
- **Maturity:** 0 stars, created 2026-02, last touched 2026-03. Demo-grade.
- **License:** README states "This project is proprietary. All rights
  reserved." No LICENSE file. **Hard blocker — cannot reuse code.**
- **Reuse value:** none for code. The least-squares plan↔model calibration idea
  is a useful reference for later validating our IFC/gbXML exports against
  source drawings, but reimplement from scratch.
- **Red flags:** proprietary license; inactive.

## 4. plan-to-glb — faisu/plan-to-glb

- **What it is:** Next.js app converting DXF/DWG plans to GLB via a chat agent
  that proposes floor semantics and a human confirms/corrects per floor.
  Requires OpenRouter API key + optional Supabase.
- **Maturity:** 0 stars, personal project, updated 2026-09-06. No test suite
  evident.
- **License:** **no LICENSE file and no `license` field in package.json** —
  treat as all-rights-reserved / unclear. Cannot depend on it without asking the
  author.
- **Weights/models:** none; it's an LLM-agent wrapper, not a detector.
- **Reuse value:** not a detection front-end at all. Two ideas worth noting:
  (a) the agent-proposes/human-corrects UX pattern for a future review step;
  (b) its README's DWG licensing note is a useful warning — LibreDWG is GPL,
  so any DWG ingestion we build must stick to DXF or a commercial parser.
- **Red flags:** unclear license; GPL contamination risk on the DWG path;
  LLM-in-the-loop cost/latency makes it unsuitable as a batch takeoff engine.

## 5. SymPoint — nicehuster/SymPoint ("Symbol as Points")

- **What it is:** panoptic symbol spotting via point-based representation.
  ICLR 2024 paper (arXiv 2401.10556). The repo **does exist publicly**.
- **Maturity:** 61 stars, dormant since 2024-03 (paper release). Research code:
  torch 1.10, detectron2 0.6, custom CUDA pointops — a heavy, era-locked stack.
- **License:** `LICENSE.txt` is an **IDEA "non-commercial scientific research
  only"** license — commercial use explicitly prohibited. **Blocked for our
  purposes** (we intend commercial tooling).
- **Weights:** released via OneDrive link in README (availability not verified).
- **Technical fit (even ignoring license):** operates on **vector SVG
  primitives**, not raster. It needs DWG/DXF/SVG input; it does not solve our
  PDF-raster detection problem.
- **Reuse value:** research reference only — the point-based treatment of CAD
  primitives is interesting if we ever ingest native vector drawings.
- **Red flags:** non-commercial license; vector-only input; stale dependency
  stack.

## 6. cadbuteaas dataset roadmap (reference only)

`silverenternal/cadbuteaas`, doc `docs/cadstruct/archive/cadstruct-moe-dataset-roadmap.md`
(2026-04-30). Not code we would use — but their dataset curation is a useful
independent cross-check on our dataset choices:

| Their pick | Their use | Note for us |
|---|---|---|
| FloorPlanCAD | wall/opening CAD target domain | aligns with our worker's list |
| CVC-FP | cross-source robustness | same |
| CubiCasa5K | rooms, 80+ symbol classes, SVG polygons | P0 for them; matches our plan |
| DeepFloorplan / R2V / R3D | room-boundary + room-type hierarchy | masks, not graphs — P1 |
| RPLAN | room topology priors | layout corpus, not detection |
| ResPlan (2025, 17k vector plans) | vector graph + connectivity | they flag "verify license" — we should too |
| SESYD / GREC | symbol-spotting stress tests | synthetic, small — P2 |
| DocLayNet / PubLayNet | sheet/table/title-block layout pretraining | relevant to our schedule-table parsing gap |

Their architecture note is also sane: modular experts per element family with
an auditable deterministic router before any learned router — philosophically
close to our auditable-pipeline stance.

---

## Ranked recommendation

**Evaluate first on Monday (against real commercial drawings):**

1. **The PID_Symbol_Detection pattern (MIT).** Not the P&ID weights — the
   *method*: class-agnostic YOLO + SAHI tiling for detection, then one-shot
   classification from the sheet's own legend/schedule. This is the only
   candidate whose core trick maps onto our schedule-driven takeoffs
   (window/light/fixture schedules ARE legends). Concrete Monday test:
   fine-tune `yolo11n`/`yolov8n` (both shipped in the repo) as a
   class-agnostic symbol proposer on AEC + Monday drawings, and prototype
   legend-crop one-shot classification for schedule tags.
2. **A plain fine-tuned Ultralytics YOLO as the pragmatic baseline.** Honest
   assessment: for "find symbols on clean commercial drawings," a YOLOv8/v11
   fine-tuned on Monday's drawings will likely beat every exotic option here.
   PID gives us the tiling + legend-learning patterns to wrap around it.

**Do not adopt:** BreakingCAD (README misrepresents the code; only classical
CV inside; dead repo — borrow LSD-vectorization idioms at most), ifc-overlay
(proprietary), plan-to-glb (no license, not a detector, GPL/DWG hazard),
SymPoint (non-commercial license + vector-only input).

**Watch / reference:** cadbuteaas roadmap for dataset curation cross-checks
(esp. ResPlan license verification); plan-to-glb's agent-verified-semantics UX
for a future human-review step; ifc-overlay's calibration math if we ever
validate exports against drawings.

**Net:** the "crowded portion" is crowded with *papers*, not with maintained
commercial-grade detectors. Adopt the PID two-stage *pattern* (MIT, published)
around a fine-tuned YOLO baseline; keep our build effort on the novel layers.
