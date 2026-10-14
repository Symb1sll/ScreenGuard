from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import yaml


def load_label_keys(path: str | Path) -> List[str]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    labels = data.get("labels", [])
    keys = [str(x["key"]) for x in labels]
    if not keys:
        raise ValueError(f"no labels found in {path}")
    return keys


def read_manifest(path: str | Path) -> List[Dict[str, str]]:
    p = Path(path)
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def write_manifest(path: str | Path, rows: Sequence[Dict[str, object]], fieldnames: Sequence[str] | None = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        ordered: List[str] = []
        for row in rows:
            for key in row.keys():
                if key not in ordered:
                    ordered.append(key)
        fieldnames = ordered
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_labels(raw: str) -> List[str]:
    return sorted({x.strip() for x in (raw or "").split("|") if x.strip()})


def stable_bucket(value: str, modulo: int = 10_000) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulo


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def dump_json(path: str | Path, obj: object) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
