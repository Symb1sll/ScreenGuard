import sys
from pathlib import Path

import numpy as np

TRAINING = Path(__file__).resolve().parents[2] / "src/task2_video_panel/recognition_part" / "training"
sys.path.insert(0, str(TRAINING))
from multilabel_common import compute_metrics, select_thresholds


def test_metrics_perfect():
    y_true = np.array([[1, 0], [0, 1], [1, 1], [0, 0]], dtype=np.int32)
    y_prob = np.array([[0.9, 0.1], [0.1, 0.9], [0.8, 0.8], [0.1, 0.1]], dtype=np.float32)
    m = compute_metrics(y_true, y_prob, [0.5, 0.5], ["a", "b"])
    assert m["macro_f1"] == 1.0
    assert m["violation_recall"] == 1.0


def test_threshold_search_prefers_recall_feasible():
    y_true = np.array([[1], [1], [0], [0]], dtype=np.int32)
    y_prob = np.array([[0.8], [0.45], [0.4], [0.2]], dtype=np.float32)
    t = select_thresholds(y_true, y_prob, min_recall=1.0, low=0.2, high=0.8, step=0.05)
    pred = (y_prob[:, 0] >= t[0]).astype(int)
    assert pred[:2].sum() == 2
