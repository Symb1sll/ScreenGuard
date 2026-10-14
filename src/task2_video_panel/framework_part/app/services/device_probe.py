"""Bounded camera preflight. A separate process makes blocked drivers reclaimable."""
import multiprocessing as mp
import queue
import threading
import time
from app.services.capture import capture_worker

_slots = threading.BoundedSemaphore(2)


def probe(source, timeout=8):
    if not _slots.acquire(blocking=False):
        raise RuntimeError('正在测试其他设备，请稍后重试')
    process = None
    output = None
    try:
        context = mp.get_context('spawn')
        output = context.Queue(maxsize=1)
        stop = context.Event()
        value = int(source['source_value']) if source['source_type'] == 'webcam' else source['source_value']
        process = context.Process(target=capture_worker, args=(value, source['source_type'] != 'local', 1, output, stop), daemon=True)
        process.start()
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            try:
                packet = output.get(timeout=.2)
            except queue.Empty:
                if not process.is_alive():
                    raise ValueError('设备采集进程退出；请检查驱动或视频地址')
                continue
            if packet[0] == 'frame':
                return packet[4], packet[3]
            raise ValueError('无法取得画面；请检查网络、账号密码、视频编码或摄像头占用情况')
        raise ValueError('连接测试超时；请检查网络、地址或摄像头占用情况')
    finally:
        if process and process.pid:
            stop.set()
            process.join(timeout=.3)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
        if output:
            output.close()
            output.cancel_join_thread()
        _slots.release()
