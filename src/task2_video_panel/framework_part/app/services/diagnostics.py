"""Cheap runtime diagnostics; expensive directory scans happen only in maintenance."""
import json
import shutil
import threading
import time
from app.core import database as db
from app.core.config import DATA_DIR

_lock = threading.Lock()
_storage = {'categories': {}, 'total_bytes': 0, 'checked_at': None, 'error': ''}


def refresh_storage():
    from app.services.operations import storage_breakdown
    try:
        result = storage_breakdown()
        with _lock:
            _storage.update(result, checked_at=time.time(), error='')
    except OSError:
        with _lock:
            _storage['error'] = '存储统计失败，请检查目录和磁盘'
        raise


def storage():
    with _lock:
        result = dict(_storage)
    result['stale'] = result['checked_at'] is None or time.time()-result['checked_at'] > 180
    return result


def source_status(source, now=None):
    now = now if now is not None else time.time()
    running = json.loads(source.get('running_config') or '{}')
    interval = running.get('sample_seconds', 1)
    capture_age = max(0, now-source['last_frame_at']) if source.get('last_frame_at') else None
    inference_age = max(0, now-source['last_inference_at']) if source.get('last_inference_at') else None
    expected = (bool(source['desired_running']) or source['status'] in ('starting', 'running', 'reconnecting')) and source['status'] != 'scheduled'
    capture_ok = source['status'] == 'running' and capture_age is not None and capture_age < max(15, interval*3)
    inference_ok = source['inference_state'] in ('ready', 'unvalidated') and inference_age is not None and inference_age < max(30, interval*3)
    return {'source_id': source['id'], 'name': source['name'], 'status': source['status'],
            'expected': expected, 'capture_ok': capture_ok, 'inference_ok': inference_ok,
            'capture_age_seconds': capture_age, 'inference_age_seconds': inference_age,
            'last_inference_at': source.get('last_inference_at'), 'inference_ms': source.get('inference_ms'),
            'queue_lag_seconds': source.get('queue_lag_seconds'), 'dropped_frames': source.get('dropped_frames', 0),
            'model_id': running.get('model_id'), 'inference_state': source['inference_state'],
            'healthy': not expected or (capture_ok and inference_ok), 'message': source['last_message']}


def snapshot():
    from app.services import maintenance, evidence, settings, auto_review
    checks = maintenance.status()
    sources = [source_status(row) for row in db.query('SELECT * FROM video_sources WHERE archived=0')]
    cfg = settings.current()['settings']
    disk = evidence.storage_status()
    disk_ok = disk['free_bytes'] >= cfg['min_free_gib']*1024**3 and disk['used_bytes'] < disk['quota_bytes'] and not disk['error']
    ready = bool(checks) and all(c['healthy'] for c in checks) and disk_ok
    return {'server_time': time.time(), 'service': 'screen-guard', 'version': '4.1.0', 'database_ok': True,
            'ready': ready, 'monitoring_ok': ready and any(s['expected'] for s in sources) and all(s['healthy'] for s in sources),
            'sources': sources, 'maintenance': checks, 'storage': storage(), 'evidence': disk,
            'automatic_review': auto_review.status()}
