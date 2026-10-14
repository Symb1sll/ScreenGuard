from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


def dhash(frame: np.ndarray, size: int = 8) -> int:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def quality(frame: np.ndarray) -> Dict[str, float | bool]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return {
        "brightness": round(brightness, 3),
        "contrast": round(contrast, 3),
        "blur_laplacian_var": round(blur, 3),
        "is_black": brightness < 12,
        "is_overexposed": brightness > 248,
        "is_low_contrast": contrast < 6,
        "is_blurry": blur < 20,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="从真实视频/RTSP 采集训练候选帧，带质量和近重复过滤。")
    ap.add_argument("--source", required=True, help="本地视频路径或 RTSP/HTTP 视频 URL")
    ap.add_argument("--out", required=True)
    ap.add_argument("--source-id", required=True)
    ap.add_argument("--interval-sec", type=float, default=2.0)
    ap.add_argument("--max-frames", type=int, default=5000)
    ap.add_argument("--near-dup-hamming", type=int, default=5, help="与上一已保存帧的 dHash 汉明距离小于等于该值时跳过")
    ap.add_argument("--keep-low-quality", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "unlabeled_manifest.csv"
    metadata_path = out / "extraction_metadata.jsonl"

    cap = cv2.VideoCapture(args.source)
    if not cap.isOpened():
        raise SystemExit(f"cannot open source: {args.source}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if not math.isfinite(fps) or fps <= 0:
        fps = 25.0
    step = max(1, int(round(fps * max(args.interval_sec, 0.1))))

    saved_rows: List[Dict[str, str]] = []
    last_hash: Optional[int] = None
    frame_no = 0
    saved = 0
    skipped_quality = 0
    skipped_duplicate = 0
    with metadata_path.open("w", encoding="utf-8") as meta:
        while saved < args.max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            frame_no += 1
            if frame_no % step != 0:
                continue
            q = quality(frame)
            if not args.keep_low_quality and (q["is_black"] or q["is_overexposed"] or q["is_low_contrast"] or q["is_blurry"]):
                skipped_quality += 1
                continue
            ph = dhash(frame)
            if last_hash is not None and hamming(ph, last_hash) <= args.near_dup_hamming:
                skipped_duplicate += 1
                continue

            ts_sec = frame_no / fps
            file_name = f"{args.source_id}_{frame_no:09d}_{int(ts_sec * 1000):012d}ms.jpg"
            path = images / file_name
            if not cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 93]):
                continue
            group_slot = int(ts_sec // (30 * 60))
            group_id = f"{args.source_id}_slot{group_slot:04d}"
            saved_rows.append({
                "image_path": str(Path("images") / file_name),
                "split": "",
                "labels": "",
                "group_id": group_id,
                "source_id": args.source_id,
                "captured_at": f"video+{ts_sec:.3f}s",
                "status": "uncertain",
                "reviewer": "",
                "notes": "待人工标注",
            })
            meta.write(json.dumps({"image": file_name, "frame_no": frame_no, "ts_sec": ts_sec, "dhash": f"{ph:016x}", "quality": q}, ensure_ascii=False) + "\n")
            last_hash = ph
            saved += 1

    cap.release()
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["image_path", "split", "labels", "group_id", "source_id", "captured_at", "status", "reviewer", "notes"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(saved_rows)

    print({
        "saved": saved,
        "skipped_quality": skipped_quality,
        "skipped_near_duplicate": skipped_duplicate,
        "fps": fps,
        "sampling_step_frames": step,
        "manifest": str(manifest_path),
        "metadata": str(metadata_path),
    })


if __name__ == "__main__":
    main()
