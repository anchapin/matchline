# Quantity takeoff → priced estimate (`takeoff_estimate.py`)

Implements #1418 (spike) as packaged in #1419. Code: `takeoff_estimate.py` ·
tests: `tests/test_takeoff_estimate.py` · fixtures:
`tests/fixtures/takeoff_estimate/`.

## What an estimate is, and is not

An estimate here is a **deterministic read-off of the canonical model's
geometry, priced against a separate, versioned price book**. Quantities are
computed from the `BuildingModel` alone; prices come only from the price
book; a finishes schedule (a versioned side input, because the canonical
model carries no finish assignment) says which finish each surface gets.
Re-running the same model + book + schedule produces byte-identical JSON.

An estimate is **not** a bid, a market price, or a design tool:

- The vendored price books are labelled FIXTURE prices, invented for the
  spike. They are not market data and must never be presented as such.
- Material layers inside a construction are **not** quantities: the
  canonical `Construction` record has no layer list, so assemblies price as
  a single line per construction (e.g. $/m² of net envelope). Layer-level
  takeoff waits on a model change, which is out of scope here.
- Anything the inputs do not determine becomes an **unpriced or
  unquantified line with a machine-readable reason** (`no_price_item`,
  `no_construction`, `no_finish_assigned`, `no_height_source`, …), never a
  silent default. The total is labelled `priced_lines_only` whenever any
  line is unpriced, so a partial total cannot be mistaken for a complete
  one.

## Rules worth knowing

- **Two wall bases, never conflated.** Per-space wall area (both faces of a
  partition counted, once per space) drives paint. Envelope net area (each
  facade counted once, openings deducted by cumulative interval matching
  along the facade; an opening straddling a segment boundary is split
  pro-rata and the split is recorded per segment) drives wall assemblies.
- **Partitions** are derived, not stored: space-polygon edges are paired
  (exact/collinear for the tiling convention, offset ≤ 0.30 m for
  inner-face islands; general straight segments, diagonals included).
  Unmatched interior edge length is reported per level and flagged above
  5%. Under the default `partition_basis="faces"` partitions are
  reconciliation quantities, not priced lines. With
  `partition_basis="assembly"` they price as assemblies and the
  corresponding faces are excluded from per-space paint quantities, with
  the exclusion recorded on each affected line. The basis is stated on the
  estimate and on every line; a mixed or unstated basis is a validation
  error (`validate_estimate_dict`).
- **Roof (D2)** reports both `area_union_m2` (space-polygon union) and
  `area_gross_m2` (footprint including the wall band, from the closed
  envelope ring). Priced roof lines use the gross figure. When the gross
  figure cannot be derived, the roof line is unquantified with a reason —
  it never falls back to the union silently.
- **Labour (D5)** is priced per the price book's own `labour_basis` field:
  `priced_unit` (hours per purchasable unit — litres bought, m² bought) or
  `quantity_unit` (hours per measured quantity unit). Every priced line
  names the basis used and the quantity it was applied to. An item carrying
  labour hours without a basis is refused at load. Missing hours or rate
  leaves the line material-only, flagged `no_labour_rate`.
- **Loaders refuse, they do not guess.** A price book or finishes schedule
  with a missing source or source date (header or any item), an unknown
  key anywhere, a duplicate item/row, an unknown unit, or a labour item
  without `labour_basis` fails to load with a named error.
- **Provenance** is on every quantity and every priced line: source element
  ids, the rule that produced it, the price-book item and version. Stored
  `area_m2` that disagrees with the recomputed polygon area by more than
  1% is flagged on the quantity, not hidden. Wall heights record their
  source (`ceiling_height_m` → `height_m` → level `wall_height_m`); a level
  height equal to the dataclass default 3.0 is flagged as
  indistinguishable from "never stated".
- Line ids are model-relative (`paint:ground-101:wall`), not prefixed by a
  case name as in the spike scripts.

## Limitations

- Flat roofs only unless the model carries `RoofPlane`s; sloped area then
  comes from the stored planes (3D polygon area). The gross footprint ring
  is chained from envelope segments per level and summed by absolute area
  across loops — a single-loop footprint is assumed per level; courtyards
  and disjoint wings on one level are not area-corrected.
- Openings without a `host_interval_m` cannot be deducted from envelope
  segments; they are reported in `envelope_unplaced_openings` with a
  reason and still deduct from their host space's wall net.
- Interior openings stored on a space without `adjacent_space_id` deduct
  from that space only (the canonical model stores an interior opening
  once, on the lower-id space, with the far side named).
- Paint purchase rounds up to whole containers; flooring/tile add the
  book's waste percentage; spare stock is a separate material-only line.
  No tax, no contingency, no escalation — those are estimating-policy
  choices for a real price book, not geometry.
