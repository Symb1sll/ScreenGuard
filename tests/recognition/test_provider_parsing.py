import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[2] / "src/task2_video_panel/recognition_part" / "benchmarking"
sys.path.insert(0, str(BENCH))
from providers import extract_json_object, normalize_payload


def test_extract_fenced_json():
    data = extract_json_object('```json\n{"scores":{"a":0.8},"overall_risk":0.8,"needs_review":false,"evidence":[]}\n```')
    assert data["scores"]["a"] == 0.8


def test_normalize_rejects_incomplete_or_invalid_results():
    import pytest
    from providers import DEFAULT_LABELS
    from model_api import ProviderError
    for result in ({},{'scores':{k:0 for k in DEFAULT_LABELS}}, {'scores':{k:2 for k in DEFAULT_LABELS},'needs_review':False,'evidence':['x']}):
        with pytest.raises(ProviderError):normalize_payload(result,DEFAULT_LABELS)
