"""Read-only startup checks; no dependency installation or model loading."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys


def inspect(data_dir):
    required = ['fastapi', 'uvicorn', 'pydantic', 'cv2', 'numpy', 'PIL', 'httpx', 'multipart']
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    path = Path(data_dir).resolve()
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    return {'python': sys.executable, 'python_version': sys.version.split()[0],
            'ok': sys.version_info >= (3, 11) and not missing,
            'missing_modules': missing, 'data_dir': str(path),
            'free_gib': round(shutil.disk_usage(ancestor).free/1024**3, 2),
            'onnx_runtime_available': importlib.util.find_spec('onnxruntime') is not None,
            'local_server_path': os.environ.get('SCREEN_GUARD_LLAMA_SERVER', ''),
            'local_model_path': os.environ.get('SCREEN_GUARD_LOCAL_MODEL', ''),
            'mmproj_path': os.environ.get('SCREEN_GUARD_MMPROJ', ''),
            'scope': '依赖可发现性与存储检查；不代表摄像头连通或模型准确率验证'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    result = inspect(args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
