from pathlib import Path
import argparse
import torch
from train_binary_classifier import build_model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--out', default=str(Path(__file__).resolve().parents[4] / 'artifacts/training/screen_binary.onnx'))
    args = ap.parse_args()
    model = build_model()
    state = torch.load(args.ckpt, map_location='cpu')
    model.load_state_dict(state['model'])
    model.eval()
    dummy = torch.randn(1, 3, 224, 224)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, dummy, args.out, input_names=['image'], output_names=['logits'], opset_version=13, dynamic_axes={'image': {0: 'batch'}, 'logits': {0: 'batch'}})
    print({'onnx': args.out})

if __name__ == '__main__':
    main()
