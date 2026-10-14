"""One harmless synthetic-video -> auto queue -> real local Qwen acceptance."""
import os,sys,json,socket,subprocess,time,secrets
from pathlib import Path
from uuid import uuid4
from model_assets import load_assets
ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'src/task2_video_panel'

def main():
    import cv2,numpy as np,httpx
    run=ROOT/'artifacts/tests'/'local_acceptance'/uuid4().hex[:12];run.mkdir(parents=True)
    data=run/'data'
    env=dict(os.environ)
    for k in ('TEMP','TMP','TMPDIR','HF_HOME','LLAMA_CACHE','XDG_CACHE_HOME','CUDA_CACHE_PATH'):env[k]=str(run)
    env['LLAMA_API_KEY']=secrets.token_urlsafe(36)
    os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(data),SCREEN_GUARD_TESTING='1',SG_ACCEPTANCE_MODEL_KEY=env['LLAMA_API_KEY'],PYTHONDONTWRITEBYTECODE='1')
    for k in ('TEMP','TMP','TMPDIR','XDG_CACHE_HOME'):os.environ[k]=str(run)
    sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part')]
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    assets=load_assets()
    command=[assets['server_path'],'-m',assets['model_path'],'--mmproj',assets['mmproj'],'--host','127.0.0.1','--port',str(port),'--alias','qwen-local','--ctx-size','4096','--parallel','1','--cache-ram','0','--offline','--fit','on','--fit-target','1024','--reasoning','off','--reasoning-budget','0','--image-min-tokens','1024','--image-max-tokens','1536','--no-webui','--no-slots']
    log=(run/'model.log').open('w',encoding='utf-8')
    process=None
    report={'scope':'合成无害视频的自动调用工程验收；不是业务准确率测试','cloud_calls':0}
    try:
        process=subprocess.Popen(command,cwd=run,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        print('Loading real local Qwen; run='+str(run),flush=True)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer '+env['LLAMA_API_KEY']},timeout=2,trust_env=False) as client:
            until=time.monotonic()+240
            while time.monotonic()<until:
                if process.poll() is not None:raise RuntimeError('Model exited; inspect local model.log')
                try:
                    if client.get('/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(1)
            else:raise TimeoutError('Model load exceeded 240 seconds')
        from app.main import app
        from app.services import settings,source_profiles,ai_connections as ai,auto_review,events
        from app.services.video_worker import manager
        from app.core import database as db
        cfg=settings.current();cfg['settings'].update(min_free_gib=0,audit_daily_per_source=0,sample_seconds=2)
        settings.save(cfg['settings'],cfg['version'],'local acceptance')
        row=ai.get_row('local-qwen')
        con=ai.Connection.model_validate_json(row['body']);con.base_url=f'http://127.0.0.1:{port}/v1';con.vision=True;con.key_env='SG_ACCEPTANCE_MODEL_KEY';con.daily_limit=2;con.timeout_seconds=120
        ai.save('local-qwen',ai.SaveConnection(expected_version=row['version'],config=con,clear_key=True))
        frame=np.full((240,400,3),(35,65,190),dtype=np.uint8)
        cv2.putText(frame,'TEST SCREEN',(35,120),cv2.FONT_HERSHEY_SIMPLEX,1.1,(255,255,255),2)
        video=data/'uploads'/'harmless.avi'
        writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10,(400,240))
        for n in range(80):writer.write(frame)
        writer.release()
        sid=db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',('本地自动链路验收','local',str(video)))
        p=source_profiles.SourceProfile();p.ai.enabled=True;p.ai.daily_limit=2;p.ai.min_interval_seconds=1
        source_profiles.save(sid,p.model_dump(),0,'acceptance')
        began=time.time();manager.start(sid)
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            auto_review.tick()
            jobs=db.query('SELECT * FROM auto_jobs WHERE source_id=?',(sid,))
            if jobs and all(j['status'] not in auto_review.ACTIVE for j in jobs):
                report['jobs']=[auto_review.for_event(j['event_id']) for j in jobs]
                report['source']=db.query_one('SELECT status,processed,last_inference_at FROM video_sources WHERE id=?',(sid,))
                report['elapsed_seconds']=round(time.time()-began,2)
                report['passed']=all(j['status']=='completed' for j in jobs) and report['source']['processed']>0
                if not report['passed']:raise AssertionError('Automatic model request failed; inspect report')
                print(json.dumps({'passed':True,'jobs':len(jobs),'processed':report['source']['processed'],'seconds':report['elapsed_seconds']},ensure_ascii=False),flush=True)
                break
            time.sleep(.5)
        else:raise TimeoutError('Auto review exceeded 180 seconds')
        manager.shutdown();ai.shutdown()
    finally:
        (run/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        log.close()
if __name__=='__main__':main()

