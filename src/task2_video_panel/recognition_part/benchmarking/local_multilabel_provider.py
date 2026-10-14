from __future__ import annotations

import sys
import time
import hashlib,json
from pathlib import Path
from typing import Dict, Sequence

import torch
from PIL import Image, ImageOps

HERE = Path(__file__).resolve()
sys.path.insert(0,str(HERE.parents[2]))
import model_api as wire
TRAINING_DIR = HERE.parents[1] / "training"
if str(TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(TRAINING_DIR))

from multilabel_common import build_transforms, load_checkpoint  # noqa: E402
from providers import ProviderResult  # noqa: E402


class LocalMultilabelProvider:
    provider_name = "local"

    def __init__(self, weights: str | Path, device: str | None = None) -> None:
        self.weights = str(weights)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model, self.label_keys, self.arch, self.image_size, self.thresholds, self.ckpt = load_checkpoint(weights, device=self.device)
        self.model_name = f"{self.arch}:{Path(weights).name}"
        self.tfm = build_transforms(False, self.image_size, self.ckpt.get('preprocessing', 'legacy_square_rgb_v0'))
        self.weights_sha256=hashlib.sha256(Path(self.weights).read_bytes()).hexdigest()

    @property
    def configured(self) -> bool:
        return Path(self.weights).exists()

    def cache_identity(self):
        return {'weights_sha256':self.weights_sha256,'preprocessing':self.ckpt.get('preprocessing','legacy_square_rgb_v0'),'thresholds':self.thresholds.tolist(),'image_size':self.image_size}

    def predict(self, image_path: str | Path) -> ProviderResult:
        started = time.perf_counter()
        try:
            with Image.open(image_path) as img:
                image = ImageOps.exif_transpose(img).convert("RGB")
            x = self.tfm(image).unsqueeze(0).to(self.device)
            with torch.no_grad():
                prob = torch.sigmoid(self.model(x))[0].cpu().numpy()
            scores = {self.label_keys[i]: round(float(prob[i]), 6) for i in range(len(self.label_keys))}
            checked=wire.parse_result(json.dumps({'scores':scores,'needs_review':False,'evidence':['本地分类器输出']}))
            overall = checked['overall_risk']
            return ProviderResult(
                provider="local",
                model=self.model_name,
                scores=scores,
                overall_risk=round(float(overall), 6),
                needs_review=False,
                evidence=[],
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        except Exception as exc:
            return ProviderResult(
                provider="local",
                model=self.model_name,
                scores={},
                overall_risk=None,
                needs_review=True,
                evidence=[],
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
                error=f"{type(exc).__name__}: {exc}",
            )
