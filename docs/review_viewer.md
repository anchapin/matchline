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

``review/review_viewer.py`` holds the whole render core: ``build_scene`` reads
the canonical model into a JSON scene, and ``page_html`` (CLI) and
``fragment`` (the export package) render it. The export package embeds
the fragment in ``share_back.html`` (#999), so what a reviewer opens in
the zip and what a modeler opens from the CLI are the same renderer.

## Correcting from the viewer (#1002)

The viewer stays read-only over the model: the Correct toggle collects
corrections instead of applying them. Pick a level, click a room or an
opening in the plan, and the panel offers the four edits the acceptance
names (rename a space by name or number, reclassify an opening between
door and window, merge one room into a neighbor, split a room along two
boundary points) plus revert-to-automatic for any field a recorded
correction changed. Skylight openings are not door/window reclass
candidates. Queued ops can be removed one by one and download as one
``viewer_corrections.json``.

Applying is the CLI's job, through the SAME decisions workflow the
review report and flags use (#996, #997):

```
matchline viewer-apply review/model.json viewer_corrections.json
```

``matchline viewer-apply`` maps each op to a review item (an existing
one when the pipeline already made it, a ``viewer_correction`` item
otherwise) and writes its decision, so every accepted correction lands
in the review queue and in ``corrections.json`` (#997) with before and
after values, source ``human`` and a timestamp. Reverts go through the
existing ``--revert-to-auto`` path in ``corrections``. Ops apply in
listed order; a replay of the same file changes nothing and logs
nothing. Merges use the #997 adjacency rules (a merge must be adjacent
and both sides room-class); splits must cut into exactly two usable
pieces, the larger keeps the id, zone and lighting, and openings move
to the piece that contains them. Nothing changes unless every op
applies.

## The invariant

The viewer renders the final simplified model FROM the canonical model
the export writes, never a second geometry copy: ``build_scene`` copies
the model's polygons, wall endpoints, heights and confidences verbatim
into the scene, re-derives nothing, and writes no geometry file of its
own. The browser draws exactly what the model states. Correct mode
(#1002) never forks a private model copy: the ops file is the only
thing the viewer writes, and the CLI applies it to the canonical model
through the decisions workflow.

## MEP overlays (#1004)

The toolbar gains two overlays next to the review marks: **MEP symbols**
and **Air loops**. Both read the same canonical model the viewer already
renders; nothing is re-derived in the browser or by ``build_scene``.

- **MEP symbols** plots every component instance the model records
  (diffusers, terminal units, sensors, grilles, air handlers) at the
  detected position, tagged with its schedule tag or id and colored by
  type (diffusers green, VAVs orange, AHUs red, sensors blue). Instances
  listed twice (a space and its zone both name the same diffuser) plot
  once. Counts in the legend and status bar aggregate these instances;
  the overlay carries each instance's detection provenance (sheet,
  confidence) in the scene.
- **Air loops** draws the trace the model records: each air loop with
  its zone list, joined through the zones already in the scene, with the
  loop tag labeled and the loop's provenance carried through. The
  **Zones** toggle remains the zone-assignment trace: spaces tinted by
  zone, and zone rows now carry their tracing provenance (sheet,
  confidence).

## The invariant
