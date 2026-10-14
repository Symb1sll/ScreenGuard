"""Isolated API smoke test; runnable on Windows and Linux, no live data modified."""
from pathlib import Path
import json,sys,os,time
from uuid import uuid4
BASE=Path(__file__).resolve().parents[1] / 'src/task2_video_panel'
def main():
    run=BASE.parents[1]/'artifacts/tests'/'api_smoke'/uuid4().hex[:12]
    run.mkdir(parents=True)
    for key in ('TEMP','TMP','TMPDIR','MPLCONFIGDIR','XDG_CACHE_HOME'):os.environ[key]=str(run)
    os.environ.update(SCREEN_GUARD_BASE_DIR=str(BASE),SCREEN_GUARD_DATA_DIR=str(run/'data'),SCREEN_GUARD_TESTING='1',PYTHONDONTWRITEBYTECODE='1')
    sys.dont_write_bytecode=True;sys.path.insert(0,str(BASE/'framework_part'))
    from fastapi.testclient import TestClient
    from app.main import app
    video=BASE/'sample_media'/'demo_city_screen.mp4'
    if not video.exists():raise SystemExit('请先生成或提供 sample_media/demo_city_screen.mp4')
    with TestClient(app) as client:
        client.headers['X-SG-CSRF']=client.get('/api/bootstrap').json()['csrf']
        with video.open('rb') as f:
            response=client.post('/api/video/local',data={'name':'isolated-api-smoke','auto_start':'true'},files={'file':(video.name,f,'video/mp4')})
        response.raise_for_status();sid=response.json()['source_id']
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            source=next(s for s in client.get('/api/sources').json() if s['id']==sid)
            if source['status'] in ('finished','error','stopped'):break
            time.sleep(.1)
        assert source['status']=='finished' and source['processed']>0,source
        result={'health':client.get('/api/health').json(),'source':source,'overview':client.get('/api/overview').json(),'events':client.get('/api/events?limit=5').json()}
    out=run/'api_smoke_test.json';out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'passed':True,'processed':source['processed'],'report':str(out)},ensure_ascii=False))
if __name__=='__main__':main()

