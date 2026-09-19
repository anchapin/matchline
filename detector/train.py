#!/usr/bin/env python3
"""Config-driven YOLO fine-tuning harness for the wisard-bem detector track.

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

    data = args.data
    if args.subset_train or args.subset_val:
        data = _subset_yaml(args.data, args.subset_train, args.subset_val,
                            os.path.join(args.project, args.name))

    model = YOLO(args.resume) if args.resume else YOLO(args.model)
    results = model.train(
        data=data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        device=args.device,
        project=args.project,
        name=args.name,
        exist_ok=True,
        verbose=True,
        plots=False,
        save_period=-1,
    )
    print('TRAIN DONE. best:', results.save_dir if hasattr(results, 'save_dir') else '')


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
    import shutil
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
