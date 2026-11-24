# Takeoff -> priced estimate (overview)

`takeoff_estimate.py` turns the canonical `BuildingModel` into a **quantity
takeoff** and then into a **priced estimate**: one line per priced element,
each line carrying its provenance (element ids, pricing rule, price item,
price book version).

```python
import takeoff_estimate as te

model = te.load_model("model.json")
book = te.load_price_book("price_book.json")
finishes = te.load_finishes("finishes.json", book=book)
quantities = te.compute_quantities(model)
estimate = te.build_estimate("case-1", model, quantities, book, finishes)
print(estimate["total"], estimate["total_label"])
```

## What it computes

| Quantity | Rule |
|---|---|
| Floor area | stored `Space.area_m2` **and** the polygon recomputed by the shoelace formula; the drift between the two is reported (`floor_area_drift_pct`) |
| Ceiling area | the recomputed floor area |
| Wall area | perimeter x height, gross; net of the openings hosted by the space (and of interior openings stored on the neighbour) |
| Trim length | perimeter, less the width of the openings that reach the floor (`sill_m == 0`) |
| Interior partitions | axis-aligned edge pairing between neighbouring spaces (collinear or facing, offset <= 0.30 m); length, area and the pairing kind (`exact` / `offset`) |
| Envelope | gross and net area per segment and per facade, with the hosted openings |
| Roof | stored roof-plane areas, else the union of the space polygons; the basis is always named |
| Openings | per-opening records plus a roll-up by category and tag |

Pricing then applies a *price book* (materials, labour, waste, spare stock,
container sizes) and a *finishes schedule* (which finish sits on which face
of which space) to produce the estimate lines: paint by litres (container
rounded up), flooring/tile by square metre with waste and spare stock,
openings by unit, trim by metre, and the envelope wall assembly by net
square metre of each construction.

## The rules that matter

* **No silent defaults.** A missing input never becomes a guess. It becomes
  an `unpriced` line (quantity known, no money) or an unquantified quantity
  (`None`) with a machine-readable reason: `no_price_item`, `no_coverage`,
  `no_coats`, `no_container_size`, `no_waste_pct`, `no_construction`,
  `no_height_source`, `no_area_or_dimensions`, `no_roof_plane_areas`,
  `non_axis_aligned_polygon`. The loaders refuse an incomplete price book
  or finishes schedule outright (`PriceBookError` / `FinishesError`, each
  with a `code`).
* **Deterministic.** The same inputs give byte-identical output: fixed
  rounding, sorted collections, no clock, no RNG, no network.
* **Traceability.** Every priced line names the elements it came from, the
  rule that priced it, the price item and the price book version.
* **Fixture prices.** The price books in this repo are labelled `FIXTURE`
  and carry invented numbers. They are test data; no market cost accuracy is
  claimed or implied.

## Design decisions D1-D6

| # | Decision |
|---|---|
| D1 | The Phase 1 fixtures (models, price books, finishes, expected estimates) are vendored under `tests/fixtures/takeoff_estimate/`, so the gates run in-repo against checked-in data. |
| D2 | The roof basis is explicit: `roof_planes_stored_area` or `flat_roof_union_of_space_polygons`, pinnable via `compute_quantities(..., roof_basis=...)`. The inner-face union does not cover the wall-thickness band; the output says so. A basis that cannot be quantified returns `area_m2: None` with a reason. |
| D3 | Envelope opening deductions use cumulative interval matching along the facade axis (origin and direction derived from the facade's reference segment and recorded). An opening that straddles two segments deducts proportionally from both; the legacy "same facade + same space" fallback is used only when the interval is missing, and it is reported as such. |
| D4 | An interior edge the pairing rule cannot handle (a diagonal) is listed in `quantities["unmatched_edges"]` with its length and a reason, and counts towards the level's unmatched-edge budget. It is never dropped. |
| D5 | A price item may declare `labour_basis` (`unit` or `m2`). When it is absent the legacy per-unit fields are used and the line records that the basis was inferred. Labour is never applied to a quantity it does not belong to (a trim line priced per m2 gets `labour_flag: no_area_basis_for_labour`). |
| D6 | Partitions are reported as quantities and are **not** priced: the per-space face lines already cover the partition faces. The estimate states the basis in `partition_pricing_basis`, and any partition price item in the book is listed in `not_priced_items` with the reason instead of being silently ignored. |

## Limitations

* **Not a market estimate.** The shipped books are fixtures; a real book
  needs a real price source, and the estimate's `total` is only as good as
  that book.
* **Material layers are out of scope.** Wall and finish build-ups are not
  quantified; only the finished quantities a book prices are.
* **Interior partitions are geometric only.** The pairing rule handles
  axis-aligned edges; a diagonal edge is reported, not paired.
* **Height hierarchy.** `ceiling_height_m` -> `height_m` ->
  `Level.wall_height_m`; a level value equal to the 3.0 m dataclass default
  is flagged, because on its own it cannot be told apart from a default.
* **Roof basis.** The flat-roof union of the inner-face polygons excludes
  the wall-thickness band, so it is smaller than the centreline footprint.
* **Not public API.** The module is internal to the repo for now: it is not
  registered in `PUBLIC_MODULES`, so it carries no compatibility promise.

## Tests

`tests/test_takeoff_estimate.py` re-registers the Phase 1 gates G1-G7
(quantities against hand ground truth, opening attribution, byte-identical
rebuild, price/quantity separation, the change test, the unmatched-edge
budget, line provenance) as automated tests against the vendored fixtures,
plus the loader refusal tests and the D2-D6 tests.
