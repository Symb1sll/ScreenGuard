import json
from pathlib import Path
from sklearn.metrics import accuracy_score, precision_recall_fscore_support, confusion_matrix

y_true = [0, 1, 1, 1, 1, 0, 1, 0]
y_pred = [0, 1, 1, 1, 1, 0, 1, 0]
precision, recall, f1, _ = precision_recall_fscore_support(y_true, y_pred, average='binary', zero_division=0)
result = {
    'note': '这是占位演示标签的评估格式，不代表公开真实数据集准确率。真实准确率需要下载 RWF-2000、Open Images V7、UnsafeBench 或自建大屏截图集后评估。',
    'accuracy_demo_only': accuracy_score(y_true, y_pred),
    'precision_demo_only': precision,
    'recall_demo_only': recall,
    'f1_demo_only': f1,
    'confusion_matrix': confusion_matrix(y_true, y_pred).tolist(),
}
out = Path(__file__).resolve().parents[4] / 'artifacts/evaluation/demo_eval_metrics.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
