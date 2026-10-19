# Platform support

The 1.0 support matrix (#1163). Wheel facts below were read from PyPI on
2026-10-19; the matrix jobs in CI keep the tested claims true rather than
relying on this page going stale.

## Python versions

| Python | Status | CI coverage |
|--------|--------|-------------|
| 3.10 | Supported | wheel install + demo on Linux, every PR and push |
| 3.11 | Supported | wheel install + demo on Linux, every PR and push |
| 3.12 | Supported | full matrix: Linux, macOS, Windows |
| 3.13 | Supported | wheel install + demo on Linux, every PR and push |
| 3.14+ | Not supported yet | no CI job; revisit each October release |

`requires-python` stays `>=3.10` with no upper pin. Unsupported versions are
policy backed by CI, not a pip exclusion: on a newer Python, `pip install`
still tries, and a gap surfaces as a missing wheel rather than a silent
downgrade.

## Operating systems

| OS | Tier | CI coverage |
|----|------|-------------|
| Linux x86_64 | Tier 1 | every PR and push (`python-matrix`) |
| macOS arm64 | Tier 1 | every push to develop/main, dispatch, `platform`-labeled PRs (`platform-matrix`) |
| Windows x86_64 | Tier 1 | every push to develop/main, dispatch, `platform`-labeled PRs (`platform-matrix`) |
| Linux aarch64 | Best-effort | wheels exist, no CI runner |
| macOS x86_64 (Intel) | Best-effort | wheels exist, no CI runner |
| Windows arm64 | Best-effort | ifcopenshell wheel exists, no CI runner |

Every matrix job builds the wheel, installs it, imports the shipped entry
modules from outside the checkout, and runs `matchline demo --no-open` with
the trust and review reports checked.

## Known gaps

- **openstudio extra**: Windows wheels exist for cp310-cp313 only; on 3.14+
  Windows there is no openstudio wheel, so the round-trip extra is
  unavailable there. The macOS and Linux wheels are `py3-none` (they install
  on any supported Python) but ship no version-specific binding wheels.
- **detector extra** (torch, ultralytics): Linux-first. torch wheel
  availability governs; the GPU training path is Linux-only in practice.
- **OCR extras**: `rapidocr_onnxruntime` has wheels for all Tier 1 OSes;
  `ocr-tesseract` needs a tesseract binary the OS supplies.
- Best-effort tiers are wheel-available but untested; issues are welcome,
  support is not promised.
- The `ui` extra (gradio) is a desktop web app and needs nothing beyond the
  core install; it is exercised by unit tests, not the platform matrix.

## Adding a Python version

One change touches four places: the `python-matrix` list in
`.github/workflows/ci.yml`, the table above, the classifiers in
`pyproject.toml`, and a wheel-availability check for every dependency
(ifcopenshell 0.9.0.post1 ships cp310-cp315; openstudio stops at cp313 on
Windows).
