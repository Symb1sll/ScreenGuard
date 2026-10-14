from __future__ import annotations

import argparse
from pathlib import Path

from common import read_manifest, stable_bucket, write_manifest


def choose_split(group_id: str, train: float, val: float) -> str:
    x = stable_bucket(group_id) / 10_000.0
    if x < train:
        return "train"
    if x < train + val:
        return "val"
    return "test"


def main() -> None:
    ap = argparse.ArgumentParser(description="按 group_id 稳定切分多标签数据，防止相邻帧泄漏。")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--train", type=float, default=0.70)
    ap.add_argument("--val", type=float, default=0.15)
    args = ap.parse_args()
    if not (0 < args.train < 1 and 0 <= args.val < 1 and args.train + args.val < 1):
        raise SystemExit("split ratios must satisfy 0 < train < 1, 0 <= val < 1, train + val < 1")

    rows = read_manifest(args.manifest)
    if not rows:
        raise SystemExit("manifest is empty")
    missing = [i + 2 for i, row in enumerate(rows) if not (row.get("group_id") or "").strip()]
    if missing:
        raise SystemExit(f"missing group_id at CSV lines: {missing[:10]}")

    group_to_split = {}
    for row in rows:
        gid = row["group_id"].strip()
        group_to_split.setdefault(gid, choose_split(gid, args.train, args.val))
        row["split"] = group_to_split[gid]

    write_manifest(args.out, rows)
    counts = {k: sum(1 for r in rows if r["split"] == k) for k in ("train", "val", "test")}
    groups = {k: sum(1 for v in group_to_split.values() if v == k) for k in ("train", "val", "test")}
    print({"images": counts, "groups": groups, "out": str(Path(args.out))})


if __name__ == "__main__":
    main()
