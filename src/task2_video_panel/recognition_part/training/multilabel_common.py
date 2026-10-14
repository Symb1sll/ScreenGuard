from __future__ import annotations

import csv
import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np
import torch
from PIL import Image, ImageOps
from sklearn.metrics import (
    average_precision_score,
    f1_score,
    precision_recall_fscore_support,
)
from torch import nn
from torch.utils.data import Dataset
from torchvision import models, transforms
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from screen_guard_core import prepare_image, PREPROCESSING, LEGACY_PREPROCESSING


IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def load_label_keys(path: str | Path) -> List[str]:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    keys = [str(item["key"]) for item in data.get("labels", [])]
    if not keys:
        raise ValueError(f"no labels found in {path}")
    return keys


def parse_label_set(raw: str) -> set[str]:
    return {x.strip() for x in (raw or "").split("|") if x.strip()}


class ManifestDataset(Dataset):
    def __init__(
        self,
        manifest: str | Path,
        data_root: str | Path,
        label_keys: Sequence[str],
        split: str,
        transform=None,
        approved_only: bool = True,
        return_meta: bool = False,
    ) -> None:
        self.manifest = Path(manifest)
        self.data_root = Path(data_root)
        self.label_keys = list(label_keys)
        self.split = split
        self.transform = transform
        self.return_meta = return_meta
        with self.manifest.open("r", encoding="utf-8-sig", newline="") as f:
            rows = [dict(row) for row in csv.DictReader(f)]
        self.rows = []
        valid = set(self.label_keys)
        for row in rows:
            if (row.get("split") or "").strip() != split:
                continue
            if approved_only and (row.get("status") or "").strip() != "approved":
                continue
            labels = parse_label_set(row.get("labels", ""))
            unknown = labels - valid
            if unknown:
                raise ValueError(f"unknown labels for {row.get('image_path')}: {sorted(unknown)}")
            self.rows.append(row)
        self.targets = np.zeros((len(self.rows), len(self.label_keys)), dtype=np.float32)
        for i, row in enumerate(self.rows):
            labels = parse_label_set(row.get("labels", ""))
            for j, key in enumerate(self.label_keys):
                self.targets[i, j] = 1.0 if key in labels else 0.0

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int):
        row = self.rows[idx]
        rel = Path(row["image_path"])
        path = rel if rel.is_absolute() else self.data_root / rel
        with Image.open(path) as img:
            image = ImageOps.exif_transpose(img).convert("RGB")
        if self.transform is not None:
            image = self.transform(image)
        target = torch.from_numpy(self.targets[idx].copy())
        if self.return_meta:
            return image, target, {"image_path": str(path), "row": row}
        return image, target


class PrepareImage:
    """Picklable on Windows; preserve all content and text direction."""
    def __init__(self, size, version):
        self.size, self.version = size, version
    def __call__(self, image):
        return prepare_image(image, self.size, self.version)

def build_transforms(train: bool, image_size: int = 224, preprocessing: str = PREPROCESSING):
    steps = [PrepareImage(image_size, preprocessing)]
    if train:
        steps.append(transforms.RandomApply([transforms.ColorJitter(brightness=.12, contrast=.12, saturation=.08)], p=.5))
    return transforms.Compose(steps + [transforms.ToTensor(), transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD)])


def build_model(arch: str, num_labels: int, pretrained: bool = False) -> nn.Module:
    if arch == "mobilenet_v3_small":
        weights = models.MobileNet_V3_Small_Weights.DEFAULT if pretrained else None
        model = models.mobilenet_v3_small(weights=weights)
        model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, num_labels)
    elif arch == "convnext_tiny":
        weights = models.ConvNeXt_Tiny_Weights.DEFAULT if pretrained else None
        model = models.convnext_tiny(weights=weights)
        model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, num_labels)
    elif arch == "efficientnet_b0":
        weights = models.EfficientNet_B0_Weights.DEFAULT if pretrained else None
        model = models.efficientnet_b0(weights=weights)
        model.classifier[-1] = nn.Linear(model.classifier[-1].in_features, num_labels)
    else:
        raise ValueError(f"unsupported arch: {arch}")
    return model


def sigmoid_np(logits: torch.Tensor) -> np.ndarray:
    return torch.sigmoid(logits).detach().cpu().numpy()


def select_thresholds(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    min_recall: float = 0.80,
    low: float = 0.10,
    high: float = 0.90,
    step: float = 0.02,
) -> np.ndarray:
    thresholds = np.full(y_true.shape[1], 0.5, dtype=np.float32)
    grid = np.arange(low, high + 1e-9, step)
    for j in range(y_true.shape[1]):
        target = y_true[:, j].astype(int)
        if target.sum() == 0:
            thresholds[j] = 0.5
            continue
        candidates = []
        for t in grid:
            pred = (y_prob[:, j] >= t).astype(int)
            p, r, f, _ = precision_recall_fscore_support(target, pred, average="binary", zero_division=0)
            candidates.append((float(t), float(p), float(r), float(f)))
        feasible = [x for x in candidates if x[2] >= min_recall]
        pool = feasible if feasible else candidates
        best = max(pool, key=lambda x: (x[3], x[1], x[2], -abs(x[0] - 0.5)))
        thresholds[j] = best[0]
    return thresholds


def compute_metrics(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    thresholds: Sequence[float],
    label_keys: Sequence[str],
) -> Dict[str, object]:
    thresholds_arr = np.asarray(thresholds, dtype=np.float32)[None, :]
    y_pred = (y_prob >= thresholds_arr).astype(np.int32)
    true = y_true.astype(np.int32)

    micro_p, micro_r, micro_f1, _ = precision_recall_fscore_support(true, y_pred, average="micro", zero_division=0)
    macro_p, macro_r, macro_f1, _ = precision_recall_fscore_support(true, y_pred, average="macro", zero_division=0)
    subset_accuracy = float((true == y_pred).all(axis=1).mean()) if len(true) else 0.0

    true_risk = true.any(axis=1).astype(int)
    pred_risk = y_pred.any(axis=1).astype(int)
    vp, vr, vf1, _ = precision_recall_fscore_support(true_risk, pred_risk, average="binary", zero_division=0)

    per_label: Dict[str, object] = {}
    ap_values = []
    for j, label in enumerate(label_keys):
        p, r, f1, _ = precision_recall_fscore_support(true[:, j], y_pred[:, j], average="binary", zero_division=0)
        if len(np.unique(true[:, j])) > 1:
            ap = float(average_precision_score(true[:, j], y_prob[:, j]))
            ap_values.append(ap)
        else:
            ap = None
        per_label[label] = {
            "threshold": round(float(thresholds_arr[0, j]), 4),
            "positives": int(true[:, j].sum()),
            "precision": round(float(p), 6),
            "recall": round(float(r), 6),
            "f1": round(float(f1), 6),
            "average_precision": None if ap is None else round(ap, 6),
        }

    return {
        "samples": int(len(true)),
        "micro_precision": round(float(micro_p), 6),
        "micro_recall": round(float(micro_r), 6),
        "micro_f1": round(float(micro_f1), 6),
        "macro_precision": round(float(macro_p), 6),
        "macro_recall": round(float(macro_r), 6),
        "macro_f1": round(float(macro_f1), 6),
        "macro_average_precision": round(float(np.mean(ap_values)), 6) if ap_values else None,
        "subset_accuracy": round(subset_accuracy, 6),
        "violation_precision": round(float(vp), 6),
        "violation_recall": round(float(vr), 6),
        "violation_f1": round(float(vf1), 6),
        "per_label": per_label,
    }


def save_json(path: str | Path, data: object) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_checkpoint(weights: str | Path, device: str = "cpu"):
    ckpt = torch.load(weights, map_location=device, weights_only=False)
    labels = list(ckpt["labels"])
    arch = str(ckpt.get("arch", "efficientnet_b0"))
    image_size = int(ckpt.get("image_size", 224))
    model = build_model(arch, len(labels), pretrained=False)
    model.load_state_dict(ckpt["model"])
    model.to(device).eval()
    thresholds = np.asarray(ckpt.get("thresholds", [0.5] * len(labels)), dtype=np.float32)
    return model, labels, arch, image_size, thresholds, ckpt
