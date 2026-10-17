# Noise-conditioned schedule evaluation (KR/EM/VF1), 2026-10



Deliverable for [#1114](https://github.com/anchapin/matchline/issues/1114): the schedule benchmark under degraded-text conditions, scored with the grouped/flat pair (#1112) plus KR/EM/VF1 (arXiv:2609.17538 + arXiv:2603.18652). Truth rows come from a mechanical schedule table rendered into a vector PDF and parsed clean; each condition degrades the extracted cell text with `scripts/schedule_noise.py` (seeded, base seed 2026, per-condition indices in row order) and re-parses.



| condition | EM (grouped) | flat F1 | KR | VF1 | crossed | missing | extra |
| --- | --- | --- | --- | --- | --- | --- | --- |
| clean | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 0 | 0 |
| ocr_confusion @5% | 0.800 | 0.947 | 0.900 | 0.947 | 1 | 0 | 0 |
| ocr_confusion @15% | 0.600 | 0.737 | 0.900 | 0.737 | 1 | 1 | 1 |
| glyph_dropout @5% | 0.000 | 0.400 | 0.400 | 0.400 | 3 | 1 | 1 |
| glyph_dropout @15% | 0.000 | 0.000 | 0.000 | 0.000 | 0 | 5 | 0 |



## What the numbers say



- OCR-confusion substitutions degrade gradually: at 15% the exact-match row rate is down to 0.600 while KR holds at 0.900 — the values are mostly recovered even where identity matching already fails, which is exactly the gap the KR/EM pair is meant to expose.

- Glyph dropout is catastrophic to the whole-row score: a single dropped character in a tag or the CAPACITY header breaks identity or column detection, so EM reads 0.000 at both rates. Headers are in noise scope by design; column detection failing under header corruption is a real robustness gap, not a metric artifact.

- The flat score flatters all of this relative to EM, as expected.



## Scope notes



- The image-side degradations (scan noise, thickened strokes) stay in `detector/degrade.py` per detector-robustness.md; this doc covers the schedule text path only.

- The OCR round-trip named in the issue needs an OCR engine ([ocr] extra) and is out of scope here; the OCR-confusion substitutions stand in for what that round-trip does to cell text.

- No LLM judge is included: the issue made it optional, and nothing hosted may gate CI.

- Re-run with `python3 scripts/eval_schedule_noise.py` (writes this doc).
