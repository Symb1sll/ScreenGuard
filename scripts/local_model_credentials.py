"""Internal launcher helper: stdout is captured into the child process environment, never logged."""
import os,sys,secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1]
project=root/'src'/'task2_video_panel'
os.environ['SCREEN_GUARD_BASE_DIR']=str(project)
os.environ.setdefault('SCREEN_GUARD_DATA_DIR',str(root/'windows_preview/runtime/data'))
sys.path[:0]=[str(project),str(project/'framework_part')]
from app.core.database import init_db
from app.services import ai_connections as ai
from pydantic import SecretStr
init_db();ai.init()
row=ai.get_row('local-qwen')
cfg=ai.Connection.model_validate_json(row['body'])
port=int(sys.argv[1])
if cfg.provider!='local' or cfg.base_url!=f'http://127.0.0.1:{port}/v1' or cfg.model!='qwen-local':
    raise SystemExit('The saved local-qwen connection does not match the launcher port/model. Check its settings first.')
key=ai.key_for(row)
if not key:
    key=secrets.token_urlsafe(40)
    cfg.json_mode=True
    ai.save('local-qwen',ai.SaveConnection(expected_version=row['version'],config=cfg,api_key=SecretStr(key)))
print(key)

