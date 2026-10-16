"""Tier-1 synthetic data generators for the Jesse-Vision prototype.

- symbols: procedural CAD-style symbol renderer (classifier training crops).
- sheets: compositional synthetic floor-plan sheet generator (end-to-end GT).
- make_dataset: CLI that writes sheets + crops + manifests into synth/out/.
- gap_experiment: sim-to-real test (train synthetic, test real AEC crops).
- e2e smoke lives in scripts/synth_e2e_test.py (kept out of the wheel, #1081).
"""
