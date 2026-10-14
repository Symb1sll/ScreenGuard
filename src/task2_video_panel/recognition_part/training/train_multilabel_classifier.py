from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from multilabel_common import (
    PREPROCESSING,
    ManifestDataset,
    build_model,
    build_transforms,
    compute_metrics,
    load_label_keys,
    save_json,
    select_thresholds,
    set_seed,
    sigmoid_np,
)


def collect(model, loader, device: str):
    model.eval()
    targets, probs = [], []
    with torch.no_grad():
        for x, y in loader:
            x = x.to(device)
            logits = model(x)
            targets.append(y.numpy())
            probs.append(sigmoid_np(logits))
    if not targets:
        return np.empty((0, 0), dtype=np.float32), np.empty((0, 0), dtype=np.float32)
    return np.concatenate(targets, axis=0), np.concatenate(probs, axis=0)


def main() -> None:
    ap = argparse.ArgumentParser(description="公共大屏多标签图像分类训练")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--arch", default="efficientnet_b0", choices=["mobilenet_v3_small", "efficientnet_b0", "convnext_tiny"])
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--patience", type=int, default=6)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--image-size", type=int, default=224)
    ap.add_argument("--pretrained", action="store_true", help="使用 torchvision ImageNet 预训练权重；首次运行可能需要联网下载")
    ap.add_argument("--min-recall", type=float, default=0.80, help="验证集逐标签阈值搜索时优先满足的最低 recall")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[4] / "artifacts/training/multilabel"))
    args = ap.parse_args()

    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    labels = load_label_keys(args.labels)
    train_ds = ManifestDataset(args.manifest, args.data_root, labels, "train", build_transforms(True, args.image_size))
    val_ds = ManifestDataset(args.manifest, args.data_root, labels, "val", build_transforms(False, args.image_size))
    if not len(train_ds) or not len(val_ds):
        raise SystemExit(f"train/val dataset must be non-empty, got train={len(train_ds)}, val={len(val_ds)}")

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=args.workers, pin_memory=device == "cuda")
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False, num_workers=args.workers, pin_memory=device == "cuda")

    pos = torch.tensor(train_ds.targets.sum(axis=0), dtype=torch.float32)
    neg = float(len(train_ds)) - pos
    pos_weight = torch.where(pos > 0, neg / torch.clamp(pos, min=1.0), torch.ones_like(pos)).clamp(1.0, 25.0).to(device)

    try:
        model = build_model(args.arch, len(labels), pretrained=args.pretrained).to(device)
    except Exception as exc:
        if args.pretrained:
            raise SystemExit(f"pretrained weights unavailable: {exc}. Retry without --pretrained or configure a reachable torchvision cache.")
        raise

    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(args.epochs, 1))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    best_score = -1.0
    best_epoch = 0
    wait = 0
    history = []
    started = time.time()

    for epoch in range(1, args.epochs + 1):
        model.train()
        running_loss = 0.0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
            optimizer.zero_grad(set_to_none=True)
            logits = model(x)
            loss = criterion(logits, y)
            loss.backward()
            optimizer.step()
            running_loss += float(loss.item()) * x.shape[0]
        scheduler.step()

        y_true, y_prob = collect(model, val_loader, device)
        thresholds = select_thresholds(y_true, y_prob, min_recall=args.min_recall)
        metrics = compute_metrics(y_true, y_prob, thresholds, labels)
        score = float(metrics["macro_f1"])
        row = {
            "epoch": epoch,
            "train_loss": round(running_loss / max(len(train_ds), 1), 8),
            "lr": optimizer.param_groups[0]["lr"],
            "val": metrics,
        }
        history.append(row)
        print(json.dumps(row, ensure_ascii=False))

        if score > best_score:
            best_score = score
            best_epoch = epoch
            wait = 0
            ckpt = {
                "preprocessing": PREPROCESSING,
                "validation_status": "candidate",
                "model": model.state_dict(),
                "labels": labels,
                "arch": args.arch,
                "image_size": args.image_size,
                "thresholds": [float(x) for x in thresholds],
                "best_val_macro_f1": best_score,
                "epoch": epoch,
            }
            torch.save(ckpt, out / "best.pt")
            save_json(out / "thresholds.json", {labels[i]: float(thresholds[i]) for i in range(len(labels))})
            save_json(out / "best_val_metrics.json", metrics)
        else:
            wait += 1
            if wait >= args.patience:
                print(json.dumps({"event": "early_stopping", "epoch": epoch, "best_epoch": best_epoch}, ensure_ascii=False))
                break

    duration = time.time() - started
    summary = {
        "preprocessing": PREPROCESSING,
        "validation_status": "candidate",
        "device": device,
        "arch": args.arch,
        "pretrained": bool(args.pretrained),
        "labels": labels,
        "train_samples": len(train_ds),
        "val_samples": len(val_ds),
        "train_positive_counts": {labels[i]: int(pos[i].item()) for i in range(len(labels))},
        "pos_weight": {labels[i]: round(float(pos_weight[i].detach().cpu()), 6) for i in range(len(labels))},
        "best_epoch": best_epoch,
        "best_val_macro_f1": best_score,
        "elapsed_seconds": round(duration, 3),
        "output": str(out / "best.pt"),
    }
    save_json(out / "history.json", history)
    save_json(out / "training_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
