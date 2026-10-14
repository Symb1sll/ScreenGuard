from pathlib import Path
import os

BASE_DIR = Path(os.environ.get('SCREEN_GUARD_BASE_DIR', Path(__file__).resolve().parents[3]))
DATA_DIR = Path(os.environ.get('SCREEN_GUARD_DATA_DIR', BASE_DIR.parents[1] / 'windows_preview' / 'runtime' / 'data'))
UPLOAD_DIR = DATA_DIR / 'uploads'
SNAPSHOT_DIR = DATA_DIR / 'snapshots'
DB_PATH = DATA_DIR / 'screen_guard.db'
STATIC_DIR = BASE_DIR / 'framework_part' / 'app' / 'static'
FRAME_INTERVAL = int(os.environ.get('SCREEN_GUARD_FRAME_INTERVAL', '10'))
MAX_FRAMES_PER_JOB = int(os.environ.get('SCREEN_GUARD_MAX_FRAMES', '160'))
ALERT_THRESHOLD = float(os.environ.get('SCREEN_GUARD_ALERT_THRESHOLD', '0.72'))

for p in (DATA_DIR, UPLOAD_DIR, SNAPSHOT_DIR):
    p.mkdir(parents=True, exist_ok=True)
