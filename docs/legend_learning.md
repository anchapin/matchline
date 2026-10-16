# Learned legend tags (#995)

`hvac_legend.classify` maps a legend row's description to an HVAC detector
class from its words alone (`CLASS_RX`), so a firm's own phrasing that names
no class ("SUPPLY GRILLE", "CEILING OUTLET") keeps `cls=None` and lands in
review on every sheet that repeats the legend. Per-project learning closes
that gap without touching the built-in rules: the mappings live in a sidecar
beside the drawing set, and the run reads them when they are there.

## Sidecar

`legend_tags.json`, next to the drawing set the pipeline reads:

```json
{
  "schema": "matchline.legend_tags/1",
  "tags": {"SUPPLY GRILLE": "grille", "CEILING OUTLET": "diffuser"}
}
```

Descriptions are stored normalized (upper case, whitespace collapsed), the
same key `classify` looks a description up under. Classes are the HVAC
detector classes (`vav`, `ahu`, `diffuser`, `grille`, `sensor`); anything
else is refused on load, and a malformed sidecar warns and is ignored rather
than failing the run. The sidecar is never committed to this repository: it
belongs to the project, not to matchline defaults.

## Learning and applying

`matchline learn-legend <stage_00_set_report.json|out_dir> [--tag DESC=CLASS ...] [--out PATH]`
reads the report's `hvac_legends` rows and writes the sidecar:

* every (description, class) pair the built-in rules already resolved is
  kept, so the project's phrasing survives a rules change;
* rows the rules could not map are listed, not guessed; map them with
  `--tag 'SUPPLY GRILLE=grille'` (repeatable), which may also override a
  built-in mapping when a firm's usage differs;
* one description mapped to two classes in the report is a conflict: the tag
  is dropped rather than guessed, and `--tag` settles it.

`run --set <pdf>` picks the sidecar up automatically from beside the set
(`--legend-tags PATH` overrides). For a multi-member `.zip` set the sidecar
is not found automatically (the set is concatenated into `out_dir/_sets/`),
so pass `--legend-tags` pointing at the original's sidecar. A learned tag wins over the word rules;
a description the sidecar does not know falls back to them. Because mapped
rows feed `legend_templates`, newly mapped symbols are cut as one-shot
templates like the rest.

## Before/after eval

`matchline legend-eval <before.json> <after.json>` compares two stage set
reports and prints what the tags changed: rows and mapped counts, newly
mapped rows, and every lost or flipped mapping. It exits 1 on a regression:
a row the baseline mapped that the tagged run leaves unmapped or maps to a
different class. Learned tags only add mappings by construction, so a
regression says a manual `--tag` overrode a rule or the rules changed
underneath; both are worth reading before re-issuing the sidecar.
