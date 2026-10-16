# Review viewer: a read-only 3D view of the model (#1001)

The review queue is a list; a wrong wall is a shape. The review viewer
renders the final simplified model as one self-contained HTML page so a
reviewer can look at the model and find where matchline may have made
mistakes before trusting the numbers.

``bash
matchline viewer review/model.json --out viewer.html
```

## What it shows

- The final simplified model in 3D: every space's stated polygon extruded
  over its level, every envelope wall run the model states, drawn with
  pan, rotate and zoom (drag rotates, shift-drag pans, the wheel zooms).
- Level-by-level isolation: the level dropdown draws only that level.
- A top-down orthographic view per level (the "Top" button) for
  space-layout checks, with the plans overlay drawing each room's label.
- Facade elevation views (South/North/East/West buttons) with the
  elevations overlay drawing each window and door rectangle placed by
  ``host_interval_m``, read as absolute coordinates along the facade axis
  from the axis minimum of that level and facade's envelope walls. If a
  run's intervals disagree with the wall spans, the overlay makes that
  visible; that is the point of a review viewer.
- Per-element confidence heat-mapping (the "Heat map" toggle): spaces
  colored by the weaker of their label and polygon-class confidence,
  envelope walls and openings by their provenance confidence. Green means
  a settled fact (0.9 or more), amber is worth a look (0.7 to 0.9), red is
  where errors most likely live. Zone assignment colors (the "Zones"
  toggle) come from the model's many-to-many space-zone links, with each
  zone's scheduled air handler in the legend. "Review marks" outlines the
  elements open review items are about.

## The tech choice, decided

Self-contained HTML (inline CSS and JS, no external assets, works from
the filesystem with no server), the same offline approach the review
report (#749) and the share-back preview (#999) already use. matchline-ui
(#987/#988) stays the interactive editor home; the review viewer
deliberately adds no editing and links into the review flow from there.

## One render core, two homes

``review_viewer.py`` holds the whole render core: ``build_scene`` reads
the canonical model into a JSON scene, and ``page_html`` (CLI) and
``fragment`` (the export package) render it. The export package embeds
the fragment in ``share_back.html`` (#999), so what a reviewer opens in
the zip and what a modeler opens from the CLI are the same renderer.

## The invariant

The viewer renders the final simplified model FROM the canonical model
the export writes, never a second geometry copy: ``build_scene`` copies
the model's polygons, wall endpoints, heights and confidences verbatim
into the scene, re-derives nothing, and writes no geometry file of its
own. The browser draws exactly what the model states.
