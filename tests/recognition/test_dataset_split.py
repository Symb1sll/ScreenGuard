import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "src/task2_video_panel/recognition_part" / "dataset_tools"
sys.path.insert(0, str(TOOLS))
from split_manifest import choose_split


def test_group_split_is_stable():
    assert choose_split("same_group", 0.7, 0.15) == choose_split("same_group", 0.7, 0.15)
