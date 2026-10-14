from pathlib import Path
from typing import BinaryIO
from uuid import uuid4
import cv2
from app.core.config import UPLOAD_DIR, SNAPSHOT_DIR

ALLOWED_EXT = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.webm'}

def save_upload(filename: str, file_obj: BinaryIO) -> Path:
    ext = Path(filename).suffix.lower() or '.mp4'
    if ext not in ALLOWED_EXT:
        ext = '.mp4'
    target = UPLOAD_DIR / f'{uuid4().hex}{ext}'
    with target.open('wb') as f:
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return target

def save_snapshot(source_id: int, frame_no: int, frame) -> str:
    target = SNAPSHOT_DIR / f'source{source_id}_frame{frame_no}_{uuid4().hex}.jpg'
    ok, encoded = cv2.imencode('.jpg', frame)
    if not ok:
        raise RuntimeError('快照编码失败')
    temporary = target.with_suffix('.tmp')
    try:
        temporary.write_bytes(encoded.tobytes())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target.relative_to(SNAPSHOT_DIR.parent).as_posix()
