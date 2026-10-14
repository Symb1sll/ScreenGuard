from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List

from PIL import Image

from common import dump_json, load_label_keys, parse_labels, read_manifest, sha256_file


def main() -> None:
    ap = argparse.ArgumentParser(description="校验真实多标签数据集 manifest。")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--data-root", default=".")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--report", default=str(Path(__file__).resolve().parents[4] / "artifacts/evaluation/dataset_validation.json"))
    ap.add_argument("--approved-only", action="store_true")
    args = ap.parse_args()

    manifest = Path(args.manifest)
    data_root = Path(args.data_root)
    valid_labels = set(load_label_keys(args.labels))
    rows = read_manifest(manifest)
    errors: List[Dict[str, object]] = []
    warnings: List[Dict[str, object]] = []
    label_counts = Counter()
    split_counts = Counter()
    status_counts = Counter()
    group_splits = defaultdict(set)
    sha_to_rows = defaultdict(list)
    readable = 0

    for idx, row in enumerate(rows, start=2):
        status = (row.get("status") or "approved").strip() or "approved"
        status_counts[status] += 1
        if args.approved_only and status != "approved":
            continue
        split = (row.get("split") or "").strip()
        if split not in {"train", "val", "test"}:
            errors.append({"line": idx, "type": "invalid_split", "value": split})
        else:
            split_counts[split] += 1
        gid = (row.get("group_id") or "").strip()
        if not gid:
            errors.append({"line": idx, "type": "missing_group_id"})
        else:
            group_splits[gid].add(split)

        labels = parse_labels(row.get("labels", ""))
        unknown = sorted(set(labels) - valid_labels)
        if unknown:
            errors.append({"line": idx, "type": "unknown_labels", "labels": unknown})
        for label in labels:
            if label in valid_labels:
                label_counts[(split, label)] += 1
                label_counts[("all", label)] += 1

        image_path = Path(row.get("image_path", ""))
        full = image_path if image_path.is_absolute() else data_root / image_path
        if not full.exists():
            errors.append({"line": idx, "type": "missing_image", "path": str(full)})
            continue
        try:
            with Image.open(full) as img:
                img.verify()
            readable += 1
        except Exception as exc:
            errors.append({"line": idx, "type": "unreadable_image", "path": str(full), "error": str(exc)})
            continue
        try:
            sha_to_rows[sha256_file(full)].append({"line": idx, "split": split, "path": str(full)})
        except Exception as exc:
            errors.append({"line": idx, "type": "hash_error", "path": str(full), "error": str(exc)})

    leakage = []
    for gid, splits in group_splits.items():
        real = sorted(s for s in splits if s)
        if len(real) > 1:
            leakage.append({"group_id": gid, "splits": real})
    if leakage:
        errors.extend({"type": "group_split_leakage", **item} for item in leakage)

    exact_duplicates = []
    cross_split_duplicates = []
    for sha, items in sha_to_rows.items():
        if len(items) <= 1:
            continue
        rec = {"sha256": sha, "items": items}
        exact_duplicates.append(rec)
        if len({x["split"] for x in items}) > 1:
            cross_split_duplicates.append(rec)
    if cross_split_duplicates:
        errors.extend({"type": "exact_duplicate_cross_split", **item} for item in cross_split_duplicates)
    elif exact_duplicates:
        warnings.append({"type": "exact_duplicates_within_split", "count": len(exact_duplicates)})

    per_label = {}
    for label in sorted(valid_labels):
        per_label[label] = {
            "all": label_counts[("all", label)],
            "train": label_counts[("train", label)],
            "val": label_counts[("val", label)],
            "test": label_counts[("test", label)],
        }
        if per_label[label]["train"] < 20:
            warnings.append({"type": "too_few_train_positives", "label": label, "count": per_label[label]["train"]})
        if per_label[label]["test"] < 10:
            warnings.append({"type": "too_few_test_positives", "label": label, "count": per_label[label]["test"]})

    report = {
        "ok": not errors,
        "manifest": str(manifest),
        "total_rows": len(rows),
        "readable_images": readable,
        "split_counts": dict(split_counts),
        "status_counts": dict(status_counts),
        "per_label_positive_counts": per_label,
        "unique_groups": len(group_splits),
        "exact_duplicate_sets": len(exact_duplicates),
        "cross_split_duplicate_sets": len(cross_split_duplicates),
        "errors": errors,
        "warnings": warnings,
    }
    dump_json(args.report, report)
    print(report)
    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
