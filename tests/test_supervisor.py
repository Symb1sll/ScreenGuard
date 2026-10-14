"""Exercise restart using a disposable actual HTTP service, never the user workbench."""
import contextlib,os,socket,subprocess,sys,time
from pathlib import Path
import httpx,psutil
ROOT=Path(__file__).resolve().parents[1]
def test_supervisor_restarts_crashed_service(tmp_path):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    data=tmp_path/'data';data.mkdir()
    env=dict(os.environ,SCREEN_GUARD_AUTH_MODE='local',PYTHONDONTWRITEBYTECODE='1')
    log=(tmp_path/'supervisor.log').open('w',encoding='utf-8')
    supervisor=subprocess.Popen([sys.executable,'-X','utf8','-B',str(ROOT/'deploy/supervise.py'),'--port',str(port),'--data-dir',str(data)],env=env,stdout=log,stderr=log)
    parent=psutil.Process(supervisor.pid)
    def ready(old=None):
        deadline=time.monotonic()+25
        with httpx.Client(timeout=1,trust_env=False) as client:
            while time.monotonic()<deadline:
                assert supervisor.poll() is None
                children=parent.children()
                try:
                    r=client.get(f'http://127.0.0.1:{port}/api/health')
                    if r.status_code==200 and children and (old is None or children[0].pid!=old):return children[0]
                except httpx.HTTPError:pass
                time.sleep(.2)
        raise AssertionError('supervised service did not become ready')
    try:
        first=ready();first.terminate();first.wait(timeout=5)
        second=ready(first.pid)
        assert second.pid!=first.pid and supervisor.poll() is None
    finally:
        children=parent.children(recursive=True) if supervisor.poll() is None else []
        supervisor.terminate();supervisor.wait(timeout=5)
        for child in children:
            with contextlib.suppress(psutil.NoSuchProcess):child.terminate()
        psutil.wait_procs(children,timeout=5);log.close()
