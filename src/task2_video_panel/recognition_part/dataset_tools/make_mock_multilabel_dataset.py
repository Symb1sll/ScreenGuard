from __future__ import annotations

import argparse
import csv
from pathlib import Path

from PIL import Image, ImageDraw

LABELS = ["sexual_vulgar", "gambling", "drugs_contraband", "violence_terror", "privacy_leak"]


def main() -> None:
    ap = argparse.ArgumentParser(description="生成仅用于代码回归测试的多标签合成数据，不代表真实准确率。")
    ap.add_argument("--out", required=True)
    ap.add_argument("--groups", type=int, default=18)
    ap.add_argument("--images-per-group", type=int, default=4)
    args = ap.parse_args()
    out = Path(args.out)
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    rows = []
    split_cycle = ["train"] * 6 + ["val"] * 2 + ["test"] * 2
    for g in range(args.groups):
        split = split_cycle[g % len(split_cycle)]
        group_id = f"mock_group_{g:03d}"
        for k in range(args.images_per_group):
            idx = g * args.images_per_group + k
            im = Image.new("RGB", (160, 160), (235, 235, 235))
            draw = ImageDraw.Draw(im)
            # Unique visual marker prevents accidental byte-identical samples across splits.
            draw.text((3, 3), f"sample-{idx}", fill=((idx * 17) % 220, (idx * 29) % 220, (idx * 43) % 220))
            labels = []
            # Deterministic patterns. Multiple labels can co-exist.
            if idx % 3 == 0:
                labels.append("gambling")
                draw.rectangle((10, 10, 70, 70), fill=(15, 160, 35))
            if idx % 5 == 0:
                labels.append("violence_terror")
                draw.rectangle((85, 15, 150, 75), fill=(205, 30, 30))
            if idx % 7 == 0:
                labels.append("privacy_leak")
                draw.rectangle((20, 95, 145, 145), fill=(255, 255, 255), outline=(0, 0, 0), width=2)
                draw.text((25, 110), "ID 123456 PHONE 1380000", fill=(0, 0, 0))
            if idx % 11 == 0:
                labels.append("sexual_vulgar")
                draw.ellipse((70, 70, 135, 135), fill=(205, 85, 180))
            if idx % 13 == 0:
                labels.append("drugs_contraband")
                draw.ellipse((30, 55, 70, 95), fill=(230, 220, 40), outline=(0, 0, 0))
            name = f"mock_{idx:04d}.jpg"
            im.save(images / name, quality=94)
            rows.append({
                "image_path": f"images/{name}",
                "split": split,
                "labels": "|".join(labels),
                "group_id": group_id,
                "source_id": "mock",
                "captured_at": f"mock+{idx}s",
                "status": "approved",
                "reviewer": "generator",
                "notes": "synthetic regression only",
            })
    with (out / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["image_path", "split", "labels", "group_id", "source_id", "captured_at", "status", "reviewer", "notes"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
    print({"rows": len(rows), "manifest": str(out / 'manifest.csv')})


if __name__ == "__main__":
    main()
