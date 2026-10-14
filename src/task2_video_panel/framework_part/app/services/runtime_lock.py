"""One server per data directory; offline backups acquire the same OS lock."""
from contextlib import contextmanager
from app.core.config import DATA_DIR
def acquire():
    handle=(DATA_DIR/'runtime.lock').open('a+b')
    try:
        handle.seek(0,2)
        if not handle.tell():handle.write(b'0');handle.flush()
        handle.seek(0)
        if __import__('os').name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close();raise RuntimeError('该数据目录已由另一个服务使用；离线操作前请停止服务') from None
def release(handle):
    if handle and not handle.closed:handle.close()
@contextmanager
def locked():
    handle=acquire()
    try:yield
    finally:release(handle)
