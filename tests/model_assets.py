"""Resolve explicitly configured assets for optional real-model acceptance runs."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_assets(root=ROOT, environment=None):
    environment = os.environ if environment is None else environment
    config = root / 'config/runtime_config.json'
    if not config.exists():
        config = root / 'runtime_config.json'
    values = json.loads(config.read_text(encoding='utf-8-sig')) if config.exists() else {}
    result = {}
    for key, variable in {'model_path': 'SCREEN_GUARD_LOCAL_MODEL',
                          'server_path': 'SCREEN_GUARD_LLAMA_SERVER',
                          'mmproj': 'SCREEN_GUARD_MMPROJ'}.items():
        value = environment.get(variable) or values.get(key)
        if not value:
            raise ValueError('Configure '+key+' before running real-model acceptance')
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if not path.is_file():
            raise ValueError('Configured '+key+' file does not exist')
        result[key] = str(path.resolve())
    return result
