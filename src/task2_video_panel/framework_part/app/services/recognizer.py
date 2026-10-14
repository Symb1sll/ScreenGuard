from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Dict, Optional
import cv2
import numpy as np

@dataclass
class RecognitionResult:
    label: str
    subtype: str
    risk_score: float
    is_violation: bool
    detail: str

class FastPolicyRecognizer:
    """轻量级识别占位器。

    生产环境中应替换为 recognition_part 导出的 ONNX/INT8 模型。这里保留一个
    不依赖 PaddleOCR/PyTorch 的快速策略，便于先运行主体框架、验证视频接入、
    告警闭环和前端交互。
    """
    keyword_map = [
        (r'gambling|casino|bet|lottery|涉赌|赌博', '涉赌内容', 0.91),
        (r'drug|毒|违禁品|powder|pill', '涉毒/违禁品', 0.88),
        (r'porn|sex|nude|vulgar|涉黄|低俗|色情', '涉黄/低俗内容', 0.89),
        (r'violence|weapon|blood|gun|knife|暴力|武器|血腥', '暴力/恐怖内容', 0.87),
        (r'idcard|privacy|private|身份证|隐私|phone', '隐私泄露', 0.82),
    ]

    def classify(self, frame: np.ndarray, context: Optional[str] = None) -> RecognitionResult:
        ctx = (context or '').lower()
        for pattern, subtype, score in self.keyword_map:
            if re.search(pattern, ctx):
                return RecognitionResult('违法违规内容', subtype, score, True, f'命中视频源/文件名关键词：{pattern}')

        if frame is None or frame.size == 0:
            return RecognitionResult('违法违规内容', '异常/无法识别', 0.76, True, '帧为空或解码失败')

        small = cv2.resize(frame, (64, 64), interpolation=cv2.INTER_AREA)
        b, g, r = [float(x) for x in small.mean(axis=(0, 1))]
        brightness = (b + g + r) / 3.0
        red_ratio = float((small[:, :, 2] > 150).mean())
        green_ratio = float((small[:, :, 1] > 145).mean())
        purple_score = (r + b) / 2 - g

        # 以下规则用于演示视频和极简边缘环境。实际系统可替换为 ONNX 分类器。
        if red_ratio > 0.45 and r > g + 35 and r > b + 20:
            return RecognitionResult('违法违规内容', '暴力/恐怖内容', min(0.96, red_ratio + 0.35), True, '画面红色占比偏高，触发暴力/血腥风险演示规则')
        if purple_score > 40 and r > 105 and b > 105:
            return RecognitionResult('违法违规内容', '涉黄/低俗内容', 0.86, True, '画面色彩分布触发低俗风险演示规则')
        if green_ratio > 0.50 and g > r + 25 and g > b + 25:
            return RecognitionResult('违法违规内容', '涉赌/广告风险', 0.84, True, '画面绿色/广告类模板触发风险演示规则')
        if brightness > 220:
            return RecognitionResult('违法违规内容', '隐私泄露', 0.74, True, '高亮证件/文档类画面触发隐私风险人工复核规则')
        return RecognitionResult('正常合规内容', '正常内容', 0.12, False, '未命中风险规则')
