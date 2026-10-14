from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

from multilabel_common import ManifestDataset, build_transforms, compute_metrics, load_checkpoint, save_json, sigmoid_np


def main() -> None:
    ap = argparse.ArgumentParser(description="在冻结 test split 上评估多标签本地模型")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[4] / "artifacts/evaluation/multilabel_test"))
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, labels, arch, image_size, thresholds, ckpt = load_checkpoint(args.weights, device=device)
    ds = ManifestDataset(args.manifest, args.data_root, labels, args.split, build_transforms(False, image_size, ckpt.get('preprocessing', 'legacy_square_rgb_v0')), return_meta=True)
    if not len(ds):
        raise SystemExit(f"split {args.split} is empty")
    loader = DataLoader(ds, batch_size=args.batch_size, shuffle=False, num_workers=args.workers)

    all_targets, all_probs, all_meta = [], [], []
    t0 = time.perf_counter()
    with torch.no_grad():
        for x, y, meta in loader:
            x = x.to(device)
            prob = sigmoid_np(model(x))
            all_targets.append(y.numpy())
            all_probs.append(prob)
            # default collate turns metadata dict values into lists/dicts. Keep only path here.
            paths = meta["image_path"]
            all_meta.extend([{"image_path": p} for p in paths])
    elapsed = time.perf_counter() - t0
    y_true = np.concatenate(all_targets, axis=0)
    y_prob = np.concatenate(all_probs, axis=0)
    metrics = compute_metrics(y_true, y_prob, thresholds, labels)
    metrics.update({
        "split": args.split,
        "arch": arch,
        "device": device,
        "elapsed_seconds": round(elapsed, 6),
        "avg_inference_ms_per_image": round(elapsed * 1000 / len(ds), 4),
    })

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "metrics.json", metrics)
    with (out / "predictions.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["image_path"]
        for label in labels:
            fields += [f"true_{label}", f"prob_{label}", f"pred_{label}"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, meta in enumerate(all_meta):
            row = {"image_path": meta["image_path"]}
            for j, label in enumerate(labels):
                row[f"true_{label}"] = int(y_true[i, j])
                row[f"prob_{label}"] = round(float(y_prob[i, j]), 6)
                row[f"pred_{label}"] = int(y_prob[i, j] >= thresholds[j])
            w.writerow(row)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
