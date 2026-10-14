from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from multilabel_common import load_checkpoint, LEGACY_PREPROCESSING


def main() -> None:
    ap = argparse.ArgumentParser(description="导出多标签模型为 ONNX，并生成标签/阈值元数据。")
    ap.add_argument("--weights", required=True)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[4] / "artifacts/training/multilabel/best.onnx"))
    ap.add_argument("--opset", type=int, default=18)
    args = ap.parse_args()

    try:
        import onnx  # noqa: F401
    except Exception as exc:
        raise SystemExit("ONNX export requires `pip install onnx`. Install recognition_part/requirements.txt first.") from exc

    model, labels, arch, image_size, thresholds, ckpt = load_checkpoint(args.weights, device="cpu")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(1, 3, image_size, image_size)
    torch.onnx.export(
        model,
        dummy,
        str(out),
        input_names=["image"],
        output_names=["logits"],
        dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=args.opset,
        do_constant_folding=True,
        dynamo=False,
        external_data=False,
    )
    meta = {
        "preprocessing": ckpt.get("preprocessing", LEGACY_PREPROCESSING),
        "arch": arch,
        "image_size": image_size,
        "labels": labels,
        "thresholds": {labels[i]: float(thresholds[i]) for i in range(len(labels))},
        "output": "logits; apply sigmoid before thresholding",
    }
    out.with_suffix(out.suffix + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"onnx": str(out), "metadata": str(out.with_suffix(out.suffix + '.json'))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
