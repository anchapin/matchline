# open-issues.md — genuinely open questions

Currently open technical questions. Resolved issues are documented in
`docs/plans/designs/` with CFG identifiers, and completed delivery is
tracked on the project board.

---

## Resolved: IFC Tier 1: space attachment

Tier 1 completion (host intervals, facade review, `adjacency_ambiguous`
flags, stale-checklist cleanup) shipped under #681. `ifc_import.py`
attaches each opening to the space(s) either side of its host wall with
an along-wall `host_interval_m`, flags ambiguous attachments for the
review queue instead of guessing, and summarizes the result in
`opening_attachment_summary`. The along-wall direction derivation that
made the side test correct is documented in CFG-05.

**Status**: Closed (#681). There are currently no genuinely open
questions in this document.
