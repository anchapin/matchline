#!/usr/bin/env python3
"""Config-driven YOLO fine-tuning harness for the matchline detector track.

Baseline: YOLO11n (COCO-pretrained), fine-tuned on CubiCasa5K door/window
boxes. CPU-friendly: small model, modest epochs, few workers.

Usage:
    python3 train.py --data configs/cubicasa.yaml --epochs 3 --subset-train 400
    python3 train.py --data configs/cubicasa.yaml --epochs 25 --name cubi_full

Outputs runs under ~/workspace/datasets/detector_runs/<name>/ (never in the repo).
"""
import argparse
import os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True, help='Ultralytics data yaml')
    ap.add_argument('--model', default='yolo11n.pt')
    ap.add_argument('--epochs', type=int, default=3)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--batch', type=int, default=8)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--device', default='cpu')
    ap.add_argument('--name', default='harness_check')
    ap.add_argument('--project', default=os.path.expanduser(
        '~/workspace/datasets/detector_runs'))
    ap.add_argument('--subset-train', type=int, default=0,
                    help='use only first N train images (0 = all)')
    ap.add_argument('--subset-val', type=int, default=0,
                    help='use only first N val images (0 = all)')
    ap.add_argument('--resume', default=None)
    ap.add_argument('--seed', type=int, default=0,
                    help='random seed for python/numpy/torch (default 0)')
    ap.add_argument('--pretrained', default=None,
                    help='checkpoint to partially load into --model; use for the '
                         'ECA A/B (e.g. --model configs/yolo11n_eca.yaml '
                         '--pretrained yolo11n.pt) so both arms start from the '
                         'same COCO weights. Params with no counterpart in the '
                         'checkpoint (e.g. ECA) keep their init.')
    args = ap.parse_args()

    import random

    import numpy as np

    random.seed(args.seed)
    np.random.seed(args.seed)
    print(f'[train] seed={args.seed}')
    try:
        import torch
        torch.manual_seed(args.seed)
    except ImportError:
        pass

    from ultralytics import YOLO

    # A model yaml may reference custom modules (e.g. ECA). parse_model resolves
    # names through ultralytics.nn.tasks' globals, so register before building.
    _register_custom_modules(args.model)

    data = _resolve_data_yaml(args.data, os.path.join(args.project, args.name))
    if args.subset_train or args.subset_val:
        data = _subset_yaml(data, args.subset_train, args.subset_val,
                            os.path.join(args.project, args.name))

    model = YOLO(args.resume) if args.resume else YOLO(args.model)
    if args.pretrained:
        if args.resume:
            ap.error('--pretrained and --resume are mutually exclusive')
        print(f'[train] aligned partial load of {args.pretrained} into {args.model}')
        _load_pretrained(model, args.pretrained)
    results = model.train(
        data=data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        device=args.device,
        # Must be forwarded: ultralytics re-seeds random/numpy/torch from its own
        # `seed` argument at the start of training, which OVERRIDES the
        # random.seed/np.random.seed/torch.manual_seed calls above. Omitting it
        # silently pinned every run to ultralytics' default of 0, so --seed 1 and
        # --seed 2 produced byte-identical results.csv to --seed 0 and a
        # multi-seed A/B was measuring a single sample three times.
        seed=args.seed,
        project=args.project,
        name=args.name,
        exist_ok=True,
        verbose=True,
        plots=False,
        save_period=-1,
    )
    print('TRAIN DONE. best:', results.save_dir if hasattr(results, 'save_dir') else '')


def _register_custom_modules(model_path):
    """Register any custom modules a model yaml may reference.

    Kept best-effort: a stock ultralytics yaml needs nothing, and failing to
    import the optional ECA module must not break a plain yolo11n run.
    """
    if 'eca' not in os.path.basename(str(model_path)).lower():
        return
    try:
        from eca import register_eca
    except ImportError:
        return
    register_eca()


def _load_pretrained(model, weights):
    """Load *weights* into *model* by structure, not by parameter name.

    Ultralytics' YOLO.load() matches parameters by name, so an architecture with
    layers inserted into the backbone (see configs/yolo11n_eca.yaml) matches
    almost nothing and silently trains from scratch. Use the aligned transfer so
    both arms of an A/B start from the same COCO weights.
    """
    try:
        from eca import transfer_aligned
    except ImportError:
        model.load(weights)
        return
    import ultralytics.nn.tasks as tasks

    loaded = tasks.load_checkpoint(weights)
    ckpt = loaded[0] if isinstance(loaded, (tuple, list)) else loaded
    if isinstance(ckpt, dict):
        ckpt = ckpt.get('ema') or ckpt.get('model')
    copied, mismatched = transfer_aligned(ckpt.float(), model.model)
    total = sum(p.numel() for p in model.model.parameters())
    print(f'[train] aligned transfer: {copied:,}/{total:,} params '
          f'({100 * copied / total:.1f}%), {mismatched} tensors shape-mismatched '
          f'(expected: Detect head, nc 80 -> 2)')


def _resolve_data_yaml(data_yaml, run_dir):
    """Return a yaml whose `path:` is an absolute, existing dataset directory.

    `detector/configs/*.yaml` must stay free of machine paths (AGENTS.md
    never-list), so the dataset root is resolved here instead. Three forms are
    accepted, in order of precedence:

      1. MATCHLINE_DATASETS set  -> `path` is treated as relative to it.
      2. `path` starting with `~` -> expanded to the home directory.
      3. `path` relative           -> resolved against this file's directory.

    The resolved copy is written under *run_dir* so the repo config is never
    mutated. Raises with the offending value if the directory is missing, rather
    than letting Ultralytics fail later with an opaque path error.
    """
    import yaml
    from pathlib import Path

    with open(data_yaml) as f:
        d = yaml.safe_load(f)

    raw = d.get('path', '')
    root_override = os.environ.get('MATCHLINE_DATASETS')
    if root_override:
        p = Path(raw).expanduser()
        resolved = p if p.is_absolute() else Path(root_override).expanduser() / p
    else:
        p = Path(raw).expanduser()
        resolved = p if p.is_absolute() else (Path(data_yaml).parent / p)

    resolved = resolved.resolve()
    if not resolved.is_dir():
        raise SystemExit(
            f'[train] dataset dir not found: {resolved}\n'
            f'  from path={raw!r} in {data_yaml}\n'
            f'  set MATCHLINE_DATASETS=<datasets root> or point --data at a '
            f'resolvable path. See detector/QUICKSTART.md for the converters.'
        )
    d['path'] = str(resolved)
    print(f'[train] dataset root: {resolved}')

    os.makedirs(run_dir, exist_ok=True)
    out = os.path.join(run_dir, 'data_resolved.yaml')
    with open(out, 'w') as f:
        yaml.safe_dump(d, f)
    return out


def _safe_rmtree(path, must_live_under):
    """Delete *path* only if it is a plausible run subdirectory.

    Refuses when the resolved path is `/`, the home directory, or anything
    outside *must_live_under* — a wrong `--project`/`--run-dir` must never
    wipe unrelated data. Prints what is being deleted.
    """
    import shutil
    from pathlib import Path
    target = Path(path).resolve()
    anchor = Path(must_live_under).resolve()
    home = Path.home().resolve()
    if target == Path('/') or target == home:
        raise ValueError(f'refusing to delete {target}: unsafe target')
    if anchor not in target.parents:
        raise ValueError(
            f'refusing to delete {target}: outside run dir {anchor}')
    print(f'[train] removing previous subset dir: {target}')
    shutil.rmtree(target)


def _subset_yaml(data_yaml, n_train, n_val, run_dir):
    """Build a temp data yaml pointing at subset file lists."""
    import yaml
    os.makedirs(run_dir, exist_ok=True)
    with open(data_yaml) as f:
        d = yaml.safe_load(f)
    base = d['path']
    sub = dict(d)
    subset_root = os.path.join(run_dir, 'subset')
    if os.path.exists(subset_root):
        _safe_rmtree(subset_root, run_dir)
    for split, n in (('train', n_train), ('val', n_val)):
        if not n:
            continue
        src_img = os.path.join(base, 'images', split)
        src_lbl = os.path.join(base, 'labels', split)
        dst_img = os.path.join(run_dir, 'subset', 'images', split)
        dst_lbl = os.path.join(run_dir, 'subset', 'labels', split)
        os.makedirs(dst_img, exist_ok=True)
        os.makedirs(dst_lbl, exist_ok=True)
        names = sorted(os.listdir(src_img))[:n]
        for nm in names:
            os.symlink(os.path.join(src_img, nm), os.path.join(dst_img, nm))
            lb = os.path.join(src_lbl, os.path.splitext(nm)[0] + '.txt')
            if os.path.exists(lb):
                os.symlink(lb, os.path.join(dst_lbl, os.path.basename(lb)))
        sub[split] = os.path.join(run_dir, 'subset', 'images', split)
    sub['path'] = base  # keep names relative lookup simple; use abs paths above
    out = os.path.join(run_dir, 'subset.yaml')
    with open(out, 'w') as f:
        yaml.safe_dump(sub, f)
    return out


if __name__ == '__main__':
    main()
