"""Loopback-only Windows preview using the existing project interpreter."""
import argparse
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PROJECT = ROOT / 'src' / 'task2_video_panel'
RUNTIME = Path(os.environ.get('SCREEN_GUARD_STATE_DIR',ROOT/'windows_preview')) / 'runtime'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True,exist_ok=True)
    for key in ('TEMP', 'TMP', 'TMPDIR', 'XDG_CACHE_HOME', 'MPLCONFIGDIR'):
        directory = RUNTIME / key.lower()
        directory.mkdir(exist_ok=True)
        os.environ[key] = str(directory)
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    os.environ['SCREEN_GUARD_BASE_DIR'] = str(PROJECT)
    os.environ.setdefault('SCREEN_GUARD_DATA_DIR', str(RUNTIME / 'data'))
    os.environ['SCREEN_GUARD_MAX_FRAMES'] = '0'
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(PROJECT / 'framework_part'))

    import uvicorn
    from app.main import app
    from app.core import database as db
    from app.services.video_worker import manager

    db.execute("UPDATE video_sources SET status='stopped', last_message='预览服务已重新启动，请手动重新开始检测' WHERE status IN ('running','stopping')")

    @app.get('/api/preview')
    def preview_info():
        return {'mode': 'local_workbench', 'pid': os.getpid(), 'host': '127.0.0.1',
                'port': args.port, 'continuous_monitoring_ready': False}

    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, access_log=True))

    @app.post('/api/shutdown')
    def shutdown_preview():
        server.should_exit = True
        return {'stopping': True}

    server.run()


if __name__ == '__main__':
    main()
