# Vendored takeoff-estimate fixtures (D1)

These are the Phase 1 fixtures (report #1418), vendored in-repo so the
takeoff-estimate gates run against checked-in data instead of a scratch
directory outside the repository.

| File | What it is |
|---|---|
| `model_fixture1.json` | The two-room Phase 1 model (living + bedroom), reconstructed in canonical `BuildingModel` shape from the Phase 1 mapped model |
| `model_case2.json` | The three-room tiling model (kitchen / bath / hall), hand-built in canonical shape |
| `price_book_fixture.json`, `price_book_case2.json` | The Phase 1 price books. **FIXTURE prices - invented numbers, not market data** |
| `finishes_fixture.json`, `finishes_case2.json` | The Phase 1 finishes schedules (which finish sits on which face of which space) |
| `expected_estimate_fixture1.json`, `expected_estimate_case2.json` | The Phase 1 expected estimates, used as the independent reference the module is checked against |

The price books carry the `FIXTURE` label in their headers (`kind`,
`region`, `source`) on purpose: they are test data, and no market cost
accuracy is claimed or implied.
