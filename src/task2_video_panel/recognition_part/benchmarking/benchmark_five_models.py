from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Sequence

import numpy as np

HERE = Path(__file__).resolve()
TRAINING_DIR = HERE.parents[1] / "training"
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))

from multilabel_common import compute_metrics, load_label_keys, parse_label_set, save_json  # noqa: E402
from local_multilabel_provider import LocalMultilabelProvider  # noqa: E402
from providers import (  # noqa: E402
    AnthropicProvider,
    JsonlCache,
    KimiProvider,
    OpenAIResponsesProvider,
    ProviderResult,
    QwenProvider,
    cache_key, DeepSeekProvider, GLMProvider,
)


def load_test_rows(manifest: str | Path, data_root: str | Path, label_keys: Sequence[str], max_samples: int | None) -> List[Dict[str, object]]:
    rows = []
    with Path(manifest).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("split") or "").strip() != "test":
                continue
            if (row.get("status") or "").strip() != "approved":
                continue
            rel = Path(row["image_path"])
            path = rel if rel.is_absolute() else Path(data_root) / rel
            labels = parse_label_set(row.get("labels", ""))
            if labels-set(label_keys):raise ValueError('Unknown labels in approved test manifest')
            y = [1 if key in labels else 0 for key in label_keys]
            rows.append({"path": path, "y": y, "row": row})
            if max_samples and len(rows) >= max_samples:
                break
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="本地多标签模型 + OpenAI + Claude + Kimi + Qwen 五模型离线横向评测")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--local-weights", default="")
    ap.add_argument("--providers", default="local,openai,claude,kimi,qwen")
    ap.add_argument("--agent-threshold", type=float, default=0.5)
    ap.add_argument("--max-samples", type=int, default=0, help="0 表示全部 test；联调时建议先 10-50")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--retries", type=int, default=0)
    ap.add_argument("--cache", default=str(HERE.parents[4] / "artifacts/evaluation/five_model_benchmark/api_cache.jsonl"))
    ap.add_argument("--out", default=str(HERE.parents[4] / "artifacts/evaluation/five_model_benchmark"))
    ap.add_argument("--allow-partial", action="store_true", help="API key 缺失时跳过该 provider，而不是直接失败")
    args = ap.parse_args()
    if args.retries != 0:raise SystemExit("Automatic paid-call retries disabled; use --retries 0")
    if not 0<args.agent_threshold<=1:raise SystemExit("agent threshold must be in (0,1]")

    labels = load_label_keys(args.labels)
    test_rows = load_test_rows(args.manifest, args.data_root, labels, args.max_samples or None)
    if not test_rows:
        raise SystemExit("approved test split is empty")

    requested = [x.strip().lower() for x in args.providers.split(",") if x.strip()]
    providers = {}
    for name in requested:
        if name == "local":
            if args.local_weights and Path(args.local_weights).exists():
                provider = LocalMultilabelProvider(args.local_weights)
                if provider.label_keys != labels:
                    raise SystemExit(f"local checkpoint labels {provider.label_keys} do not match benchmark labels {labels}")
                providers[name] = provider
            elif not args.allow_partial:
                raise SystemExit("local provider requested but --local-weights is missing or does not exist")
        elif name == "openai":
            providers[name] = OpenAIResponsesProvider(labels, args.timeout, args.retries)
        elif name == "claude":
            providers[name] = AnthropicProvider(labels, args.timeout, args.retries)
        elif name == "kimi":
            providers[name] = KimiProvider(labels, args.timeout, args.retries)
        elif name == "qwen":
            providers[name] = QwenProvider(labels, args.timeout, args.retries)
        elif name == "deepseek":providers[name] = DeepSeekProvider(labels,args.timeout,args.retries)
        elif name == "glm":providers[name] = GLMProvider(labels,args.timeout,args.retries)
        else:
            raise SystemExit(f"unknown provider: {name}")

    unconfigured = [name for name in requested if name not in providers or not providers[name].configured]
    if unconfigured and not args.allow_partial:
        raise SystemExit(f"missing API configuration for: {unconfigured}. Set keys/base URLs or use --allow-partial.")
    for name in unconfigured:
        providers.pop(name, None)
    if not providers:
        raise SystemExit("no configured providers are available")

    cache = JsonlCache(args.cache)
    detail_rows = []
    by_provider_ids = defaultdict(list)
    by_provider_cached = defaultdict(int)
    by_provider_true = defaultdict(list)
    by_provider_prob = defaultdict(list)
    by_provider_latency = defaultdict(list)
    by_provider_errors = defaultdict(int)
    by_provider_tokens = defaultdict(lambda: {"input": 0, "output": 0})

    local_thresholds = None
    if "local" in providers:
        local_thresholds = np.asarray(providers["local"].thresholds, dtype=np.float32)

    for idx, sample in enumerate(test_rows, start=1):
        image_path = Path(sample["path"])
        if not image_path.exists():
            raise SystemExit(f"missing image: {image_path}")
        y = np.asarray(sample["y"], dtype=np.int32)
        for name, provider in providers.items():
            key = cache_key(name, provider.model_name if name == "local" else provider.model, image_path, configuration=provider.cache_identity())
            result = cache.get(key)
            if result is None:
                result = provider.predict(image_path)
                if not result.error:
                    cache.put(key, result)
            if result.error:
                by_provider_errors[name] += 1
                detail_rows.append({
                    "provider": name, "model": result.model, "image_path": str(image_path),
                    "error": result.error, "latency_ms": result.latency_ms,
                })
                continue
            if set(result.scores)!=set(labels) or any(not np.isfinite(v) or not 0<=v<=1 for v in result.scores.values()):
                by_provider_errors[name]+=1
                detail_rows.append({'provider':name,'model':result.model,'image_path':str(image_path),'error':'Invalid complete score contract','latency_ms':result.latency_ms})
                continue
            prob = np.asarray([result.scores[label] for label in labels], dtype=np.float32)
            by_provider_ids[name].append(idx)
            by_provider_cached[name] += int(result.cached)
            by_provider_true[name].append(y)
            by_provider_prob[name].append(prob)
            if not result.cached:by_provider_latency[name].append(float(result.latency_ms))
            if result.input_tokens and not result.cached:
                by_provider_tokens[name]["input"] += int(result.input_tokens)
            if result.output_tokens and not result.cached:
                by_provider_tokens[name]["output"] += int(result.output_tokens)
            row = {
                "provider": name,
                "model": result.model,
                "image_path": str(image_path),
                "latency_ms": result.latency_ms,
                "cached": result.cached,
                "overall_risk": result.overall_risk,
                "needs_review": result.needs_review,
                "evidence": " | ".join(result.evidence),
                "input_tokens": result.input_tokens or "",
                "output_tokens": result.output_tokens or "",
                "error": "",
            }
            threshold_vec = local_thresholds if name == "local" and local_thresholds is not None else np.full(len(labels), args.agent_threshold)
            for j, label in enumerate(labels):
                row[f"true_{label}"] = int(y[j])
                row[f"score_{label}"] = round(float(prob[j]), 6)
                row[f"pred_{label}"] = int(prob[j] >= threshold_vec[j])
            detail_rows.append(row)
        print(json.dumps({"progress": f"{idx}/{len(test_rows)}", "image": str(image_path)}, ensure_ascii=False))

    common_ids=set.intersection(*(set(by_provider_ids[name]) for name in providers))
    summaries = {}
    ranking_rows = []
    for name, provider in providers.items():
        if not by_provider_true[name]:
            summaries[name] = {
                "provider": name,
                "model": provider.model_name if name == "local" else provider.model,
                "successful_samples": 0,
                "coverage": 0,
                "common_samples": len(common_ids),
                "cached_samples": 0,
                "fresh_latency_samples": 0,
                "errors": by_provider_errors[name],
            }
            continue
        y_true = np.asarray(by_provider_true[name], dtype=np.int32)
        y_prob = np.asarray(by_provider_prob[name], dtype=np.float32)
        thresholds = local_thresholds if name == "local" and local_thresholds is not None else np.full(len(labels), args.agent_threshold)
        selected=[i for i,sample_id in enumerate(by_provider_ids[name]) if sample_id in common_ids]
        metrics = compute_metrics(y_true[selected], y_prob[selected], thresholds, labels) if selected else {}
        lats = by_provider_latency[name]
        summary = {
            "provider": name,
            "model": provider.model_name if name == "local" else provider.model,
            "successful_samples": len(y_true),
            "coverage": len(y_true)/len(test_rows),
            "common_samples": len(common_ids),
            "cached_samples": by_provider_cached[name],
            "fresh_latency_samples": len(lats),
            "errors": by_provider_errors[name],
            "latency_ms_avg": round(float(statistics.mean(lats)), 3) if lats else None,
            "latency_ms_p50": round(float(np.percentile(lats, 50)), 3) if lats else None,
            "latency_ms_p95": round(float(np.percentile(lats, 95)), 3) if lats else None,
            "input_tokens": by_provider_tokens[name]["input"],
            "output_tokens": by_provider_tokens[name]["output"],
            **metrics,
        }
        summaries[name] = summary
        if not common_ids:continue
        ranking_rows.append({
            "provider": name,
            "model": summary["model"],
            "samples": len(common_ids),
            "errors": summary["errors"],
            "macro_f1": summary["macro_f1"],
            "micro_f1": summary["micro_f1"],
            "violation_recall": summary["violation_recall"],
            "violation_precision": summary["violation_precision"],
            "latency_ms_avg": summary["latency_ms_avg"],
            "latency_ms_p95": summary["latency_ms_p95"],
            "input_tokens": summary["input_tokens"],
            "output_tokens": summary["output_tokens"],
        })

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "summary.json", {
        "test_samples_requested": len(test_rows),
        "providers_requested": requested,
        "providers_run": list(providers),
        "providers_unconfigured": unconfigured,
        "common_sample_ids": sorted(common_ids),
        "metric_scope": "Only the intersection of successful samples across all configured providers; latency excludes cached results",
        "agent_threshold": args.agent_threshold,
        "results": summaries,
    })

    all_fields = []
    for row in detail_rows:
        for key in row:
            if key not in all_fields:
                all_fields.append(key)
    with (out / "predictions.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(detail_rows)
    with (out / "ranking.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["provider", "model", "samples", "errors", "macro_f1", "micro_f1", "violation_recall", "violation_precision", "latency_ms_avg", "latency_ms_p95", "input_tokens", "output_tokens"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(ranking_rows, key=lambda x: (x["macro_f1"], x["violation_recall"]), reverse=True))

    print(json.dumps({"summary": str(out / 'summary.json'), "ranking": str(out / 'ranking.csv'), "predictions": str(out / 'predictions.csv')}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
