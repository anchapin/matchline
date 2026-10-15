"""matchline-ui: a one-page Gradio web app over run_pipeline (#982).

The app shells out to ``run_pipeline`` (the same stages, the same defaults)
so web-app numbers and command-line numbers can never drift. One screen,
plain language: drop a drawing-set PDF, pick how aggressively similar
surfaces merge, press Run, and download the gbXML, the building model JSON,
and the review page.

Launch with ``matchline-ui`` (installed via the ``ui`` extra)::

    pip install "matchline[ui]"
    matchline-ui          # opens http://127.0.0.1:7860

The server binds to 127.0.0.1 by default; files stay on the machine that
runs it. Gradio is imported lazily so the core install never needs it.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_SIMPLIFY_TOL = 0.02
LAUNCH_HOST = "127.0.0.1"
LAUNCH_PORT = 7860

# Stage files the pipeline writes -> plain-language progress lines, in stage
# order. ``stage_06_bem`` is the export directory; a directory counts once it
# holds anything. Written in user language on purpose: rooms, windows, doors.
STAGE_WATCH: tuple[tuple[str, str], ...] = (
    ("stage_00_set_report.json", "Opening the drawing set..."),
    ("stage_01_building.json", "Reading the sheets: scale, walls, rooms..."),
    ("stage_02_model.json", "Finding rooms, walls, doors and windows..."),
    ("stage_03_simplified.json", "Simplifying the building outline..."),
    ("stage_03b_constructions.json", "Picking wall and roof assemblies..."),
    ("stage_04_validation.json", "Checking the model..."),
    ("review/review.html", "Writing the review page..."),
    ("stage_06_bem", "Exporting gbXML and IFC..."),
)

OPENING_LABELS = {"window": "windows", "door": "doors", "skylight": "skylights"}


def repo_root() -> Path:
    """The repo (or install) root, so ``python -m run_pipeline`` resolves."""
    return Path(__file__).resolve().parents[1]


def build_command(pdf_path: Path | None, seed: int | None, tol: float, out_dir: Path) -> list[str]:
    """The exact command the UI runs, derived the same way the CLI does it."""
    cmd = [
        sys.executable,
        "-m",
        "run_pipeline",
        "--out-dir",
        str(out_dir),
        "--simplify-tol",
        f"{float(tol):g}",
    ]
    if pdf_path is not None:
        cmd += ["--set", str(pdf_path)]
    else:
        cmd += ["--seed", str(int(seed))]
    return cmd


def next_stage_lines(out_dir: Path, emitted: set[str]) -> list[tuple[str, str]]:
    """Watch entries newly present in ``out_dir``, in declared stage order."""
    out: list[tuple[str, str]] = []
    for name, line in STAGE_WATCH:
        if name in emitted:
            continue
        path = out_dir / name
        present = path.is_file() or (path.is_dir() and any(path.iterdir()))
        if present:
            out.append((name, line))
    return out


def load_stage_model(out_dir: Path) -> dict | None:
    """The building-model dict from stage_02_model.json, or None."""
    path = out_dir / "stage_02_model.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    model = data.get("model") if isinstance(data, dict) else None
    # Stage 2 has two shapes: the drawing-set path stores the model dict
    # directly; the synthetic path stores the to_json() wrapper, whose real
    # model sits one "model" key deeper. Unwrap until the spaces map shows.
    while (
        isinstance(model, dict) and "spaces" not in model and isinstance(model.get("model"), dict)
    ):
        model = model["model"]
    return model if isinstance(model, dict) else None


def opening_counts(model: dict) -> dict[str, int]:
    """Windows/doors/skylights across every space's openings."""
    counts: dict[str, int] = {}
    for space in (model.get("spaces") or {}).values():
        for opening in space.get("openings") or []:
            category = str(opening.get("category", "")).lower()
            if category:
                counts[category] = counts.get(category, 0) + 1
    return counts


def summarize(model: dict) -> str:
    """Plain-language results paragraph for one finished run."""
    name = model.get("name") or "the building"
    lines = [f"Done. {name} exported."]
    lines.append(f"Levels: {len(model.get('levels') or [])}")
    lines.append(f"Rooms: {len(model.get('spaces') or {})}")
    counts = opening_counts(model)
    for category, count in sorted(counts.items()):
        label = OPENING_LABELS.get(category, category + "s")
        lines.append(f"{label.capitalize()}: {count}")
    review = model.get("review_queue") or []
    lines.append(f"Items to review: {len(review)}")
    return "\n".join(lines)


def find_outputs(out_dir: Path) -> dict[str, Path | None]:
    """Downloadable artifacts of a finished run, if they exist."""
    bem = out_dir / "stage_06_bem"
    gbxml = None
    ifc = None
    if bem.is_dir():
        xmls = sorted(bem.glob("*.xml"))
        ifcs = sorted(bem.glob("*.ifc"))
        gbxml = xmls[0] if xmls else None
        ifc = ifcs[0] if ifcs else None
    review = out_dir / "review" / "review.html"
    model_json = out_dir / "stage_02_model.json"
    return {
        "gbxml": gbxml,
        "ifc": ifc,
        "review_html": review if review.is_file() else None,
        "model_json": model_json if model_json.is_file() else None,
    }


def run_flow(pdf, use_demo: bool, seed_val, tol):
    """Gradio handler: stream plain-language progress, then results.

    Yields (log, summary, gbxml, model_json, review_html) as Gradio updates.
    """
    tol = DEFAULT_SIMPLIFY_TOL if tol is None else float(tol)
    if pdf is not None:
        pdf_path = Path(pdf.name if hasattr(pdf, "name") else pdf)
        opening = "Running matchline on your drawing set..."
        seed = None
    elif use_demo:
        seed = int(seed_val) if seed_val not in (None, "") else random.randint(1, 9999)
        pdf_path = None
        opening = f"Running a demo building (number {seed})..."
    else:
        yield "Drop a drawing-set PDF first, or tick the demo box.", "", None, None, None
        return

    workdir = Path(tempfile.mkdtemp(prefix="matchline-ui-"))
    out_dir = workdir / "out"
    lines = [opening]

    def snapshot():
        model = load_stage_model(out_dir)
        outputs = find_outputs(out_dir)
        pick = lambda p: str(p) if p else None  # noqa: E731
        return (
            "\n".join(lines),
            summarize(model) if model else "",
            pick(outputs["gbxml"]),
            pick(outputs["model_json"]),
            pick(outputs["review_html"]),
        )

    if pdf_path is not None:
        cmd = build_command(pdf_path, None, tol, out_dir)
    else:
        cmd = build_command(None, seed, tol, out_dir)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=repo_root(),
    )
    emitted: set[str] = set()
    tail: list[str] = []
    assert proc.stdout is not None

    # Poll stage files while the pipeline runs; stdout is drained at the end
    # so a failure can show the real error rather than a silent stop.
    while proc.poll() is None:
        for name, line in next_stage_lines(out_dir, emitted):
            emitted.add(name)
            lines.append(line)
        time.sleep(0.5)
    out, _ = proc.communicate()
    tail = [ln for ln in (out or "").splitlines() if ln.strip()]

    ok = proc.returncode == 0 and load_stage_model(out_dir) is not None
    if ok:
        lines.append("Finished.")
    else:
        lines.append("Something went wrong. The pipeline said:")
        lines.extend(tail[-6:] or ["(no output)"])
    yield snapshot()

    if not ok:
        # Keep the summary block empty on failure so nobody downloads a
        # half-finished export by mistake; the log above says why it failed.
        log, _summary, gbxml, model_json, review_html = snapshot()
        yield (log, "", gbxml, model_json, review_html)


def build_demo():
    """Build the Gradio Blocks app (gradio imported lazily)."""
    import gradio as gr

    with gr.Blocks(title="Matchline") as demo:
        gr.Markdown(
            "# Drawings in, energy model out\n"
            "Drop a drawing-set PDF, then press Run. Matchline reads the "
            "sheets, finds the rooms and openings, and writes a gbXML model "
            "plus a review page."
        )
        with gr.Row():
            pdf = gr.File(label="Drawing-set PDF", file_types=[".pdf"])
            with gr.Column():
                use_demo = gr.Checkbox(label="Try a demo building instead", value=False)
                seed_val = gr.Number(label="Demo building number (blank = random)", precision=0)
        tol = gr.Slider(
            minimum=0.0,
            maximum=0.10,
            value=DEFAULT_SIMPLIFY_TOL,
            step=0.005,
            label="How aggressively to merge similar surfaces",
        )
        run_btn = gr.Button("Run")
        log = gr.Textbox(label="Progress", lines=8)
        summary = gr.Textbox(label="Results", lines=8)
        with gr.Row():
            out_gbxml = gr.File(label="gbXML model")
            out_model = gr.File(label="Building model (JSON)")
            out_review = gr.File(label="Review page (HTML)")

        run_btn.click(
            run_flow,
            inputs=[pdf, use_demo, seed_val, tol],
            outputs=[log, summary, out_gbxml, out_model, out_review],
        )
    return demo


def main(argv=None) -> None:
    try:
        import gradio as gr  # noqa: F401
    except ImportError:
        sys.exit('matchline-ui needs the UI extra: pip install "matchline[ui]"')
    demo = build_demo()
    demo.launch(server_name=LAUNCH_HOST, server_port=LAUNCH_PORT, inbrowser=True)


if __name__ == "__main__":
    main()
