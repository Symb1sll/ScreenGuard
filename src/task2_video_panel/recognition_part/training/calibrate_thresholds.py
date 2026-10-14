from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from multilabel_common import ManifestDataset, build_transforms, compute_metrics, load_checkpoint, save_json, select_thresholds, sigmoid_np


def main() -> None:
    ap = argparse.ArgumentParser(description="仅在 validation split 上标定多标签阈值，禁止使用 test split 调参。")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--min-recall", type=float, default=0.85)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[4] / "artifacts/training/multilabel/calibrated_thresholds.json"))
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, labels, _, image_size, _, ckpt = load_checkpoint(args.weights, device=device)
    ds = ManifestDataset(args.manifest, args.data_root, labels, "val", build_transforms(False, image_size, ckpt.get('preprocessing', 'legacy_square_rgb_v0')))
    loader = DataLoader(ds, batch_size=args.batch_size, shuffle=False, num_workers=2)
    true, prob = [], []
    with torch.no_grad():
        for x, y in loader:
            true.append(y.numpy())
            prob.append(sigmoid_np(model(x.to(device))))
    y_true = np.concatenate(true, axis=0)
    y_prob = np.concatenate(prob, axis=0)
    thresholds = select_thresholds(y_true, y_prob, min_recall=args.min_recall)
    metrics = compute_metrics(y_true, y_prob, thresholds, labels)
    payload = {
        "min_recall_target": args.min_recall,
        "thresholds": {labels[i]: float(thresholds[i]) for i in range(len(labels))},
        "validation_metrics": metrics,
    }
    save_json(args.out, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
