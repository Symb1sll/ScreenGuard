"""Independent maintenance clocks: network delivery cannot delay source recovery."""
import logging
import threading
import time

logger = logging.getLogger(__name__)


class PeriodicTask:
    def __init__(self, name, interval, action, max_seconds=30):
        self.name, self.interval, self.action, self.max_seconds = name, interval, action, max_seconds
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.thread = None
        self.state = {'last_started': None, 'last_success': None, 'last_error': '', 'running': False, 'failures': 0}

    def once(self):
        with self.lock:
            self.state.update(last_started=time.time(), running=True)
        try:
            self.action()
            with self.lock:
                self.state.update(last_success=time.time(), last_error='')
        except Exception as exc:
            logger.exception('Maintenance task failed: %s', self.name)
            with self.lock:
                self.state['last_error'] = type(exc).__name__
                self.state['failures'] += 1
        finally:
            with self.lock:
                self.state['running'] = False

    def loop(self):
        while not self.stop.is_set():
            self.once()
            self.stop.wait(self.interval)

    def start(self):
        self.stop.clear()
        self.thread = threading.Thread(target=self.loop, name='maintenance-'+self.name, daemon=True)
        self.thread.start()

    def snapshot(self):
        with self.lock:
            state = dict(self.state)
        now = time.time()
        stale = state['last_started'] is not None and now-state['last_started'] > self.interval+self.max_seconds
        stuck = state['running'] and now-state['last_started'] > self.max_seconds
        state.update(name=self.name, healthy=bool(self.thread and self.thread.is_alive()) and not(state['last_error'] or stale or stuck), interval_seconds=self.interval)
        return state


tasks = []


def start():
    from app.services import operations, evidence, auto_review, diagnostics
    from app.services.video_worker import manager
    global tasks
    if any(t.thread and t.thread.is_alive() for t in tasks):
        raise RuntimeError('后台维护任务尚未停止')
    tasks = [PeriodicTask('notifications', 2, operations.deliver_one, 15),
             PeriodicTask('evidence_cleanup', 30, evidence.cleanup, 30),
             PeriodicTask('source_recovery', 5, manager.restore, 20),
             PeriodicTask('faults', 10, operations.sweep, 20),
             PeriodicTask('storage_metrics', 60, diagnostics.refresh_storage, 120),
             PeriodicTask('automatic_review', 1, auto_review.tick, 30)]
    for task in tasks:
        task.start()


def shutdown():
    for task in tasks:
        task.stop.set()
    deadline = time.monotonic()+10
    for task in tasks:
        if task.thread:
            task.thread.join(timeout=max(0, deadline-time.monotonic()))


def status():
    return [task.snapshot() for task in tasks]
