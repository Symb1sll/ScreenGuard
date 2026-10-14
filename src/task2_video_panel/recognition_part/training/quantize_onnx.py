"""ONNX 动态量化脚本，用于提升 CPU 推理速度和降低模型体积。"""
import argparse
from pathlib import Path
from onnxruntime.quantization import quantize_dynamic, QuantType

ap = argparse.ArgumentParser()
ap.add_argument('--model', required=True)
ap.add_argument('--out', default=str(Path(__file__).resolve().parents[4] / 'artifacts/training/screen_binary_int8.onnx'))
args = ap.parse_args()
Path(args.out).parent.mkdir(parents=True, exist_ok=True)
quantize_dynamic(args.model, args.out, weight_type=QuantType.QInt8)
print({'quantized_model': args.out})
