"""Four synthetic HTTP sources plus preloaded real Qwen; project-local data only."""
import os, sys, json, socket, subprocess, secrets, time, threading
from pathlib import Path
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from model_assets import load_assets

ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'src/task2_video_panel'

def main():
    import cv2, numpy as np, httpx, psutil
    run=ROOT/'artifacts/tests'/'warm_camera_runs'/uuid4().hex[:12]
    run.mkdir(parents=True);data=run/'data'
    modelenv=dict(os.environ,LLAMA_API_KEY=secrets.token_urlsafe(36))
    for key in ('TEMP','TMP','TMPDIR','HF_HOME','LLAMA_CACHE','XDG_CACHE_HOME','CUDA_CACHE_PATH'):modelenv[key]=str(run)
    os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(data),SCREEN_GUARD_TESTING='1',SG_ACCEPTANCE_MODEL_KEY=modelenv['LLAMA_API_KEY'],PYTHONDONTWRITEBYTECODE='1')
    for key in ('TEMP','TMP','TMPDIR','XDG_CACHE_HOME'):os.environ[key]=str(run)
    sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part')]
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    assets=load_assets()
    command=[assets['server_path'],'-m',assets['model_path'],'--mmproj',assets['mmproj'],'--host','127.0.0.1','--port',str(port),'--alias','qwen-local','--threads','4','--threads-batch','4','--ctx-size','4096','--parallel','1','--cache-ram','0','--offline','--fit','on','--fit-target','1024','--reasoning','off','--reasoning-budget','0','--image-min-tokens','1024','--image-max-tokens','1536','--no-webui','--no-slots']
    log=(run/'model.log').open('w',encoding='utf-8')
    modelproc=None;camera=None;manager=None;ai=None;stop=threading.Event()
    report={'scope':'4 local synthetic HTTP MJPEG feeds and preloaded local Qwen; not accuracy or hardware RTSP acceptance','cloud_calls':0,'model_threads':4,'passed':False,'observations':[]}
    try:
        modelproc=subprocess.Popen(command,cwd=run,env=modelenv,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
        print('Preloading local Qwen: '+str(run),flush=True);began=time.monotonic()
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer '+modelenv['LLAMA_API_KEY']},trust_env=False,timeout=2) as client:
            while time.monotonic()-began<240:
                if modelproc.poll() is not None:raise RuntimeError('model process exited')
                try:
                    if client.get('/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(1)
            else:raise TimeoutError('model loading')
        report['model_load_seconds']=round(time.monotonic()-began,2)
        from app.main import app
        from app.core import database as db
        from app.services import ai_connections as ai,settings,source_profiles,auto_review
        from app.services.video_worker import manager
        cfg=settings.current();cfg['settings'].update(min_free_gib=0,audit_daily_per_source=0,sample_seconds=1,segment_seconds=10)
        settings.save(cfg['settings'],cfg['version'],'warm concurrency test')
        conrow=ai.get_row('local-qwen');con=ai.Connection.model_validate_json(conrow['body'])
        con.base_url=f'http://127.0.0.1:{port}/v1';con.vision=True;con.key_env='SG_ACCEPTANCE_MODEL_KEY';con.daily_limit=4;con.timeout_seconds=120
        ai.save('local-qwen',ai.SaveConnection(expected_version=conrow['version'],config=con,clear_key=True))
        frame=np.full((240,400,3),(35,65,190),dtype=np.uint8)
        cv2.putText(frame,'TEST SCREEN',(35,120),cv2.FONT_HERSHEY_SIMPLEX,1.1,(255,255,255),2)
        _,jpeg=cv2.imencode('.jpg',frame);jpeg=jpeg.tobytes()
        class Camera(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame');self.end_headers()
                try:
                    while not stop.is_set():
                        self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n');self.wfile.flush();stop.wait(.1)
                except OSError:pass
                self.close_connection=True
            def log_message(self,*args):pass
        camera=ThreadingHTTPServer(('127.0.0.1',0),Camera);camera.daemon_threads=True
        threading.Thread(target=camera.serve_forever,daemon=True).start()
        ids=[]
        for i in range(4):
            sid=db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',(f'warm camera {i+1}','rtsp',f'http://127.0.0.1:{camera.server_port}/{i}'))
            p=source_profiles.SourceProfile();p.ai.enabled=True;p.ai.daily_limit=1;p.ai.min_interval_seconds=1;p.ai.ttl_seconds=600
            source_profiles.save(sid,p.model_dump(),0,'warm concurrency test');ids.append(sid);manager.start(sid)
        began=time.monotonic();last_sample=0
        print('Preloaded model; running four cameras for 240 seconds',flush=True)
        while time.monotonic()-began<240:
            auto_review.tick();elapsed=time.monotonic()-began
            rows=db.query('SELECT id,status,processed,last_frame_at,last_inference_at,inference_ms,dropped_frames FROM video_sources')
            if elapsed>20 and any(r['status']!='running' or time.time()-(r['last_frame_at'] or 0)>5 or time.time()-(r['last_inference_at'] or 0)>5 for r in rows):
                report['failed_sources']=rows;raise AssertionError('capture or inference stale during warm model use')
            if elapsed-last_sample>=5:
                report['observations'].append({'seconds':round(elapsed,1),'sources':rows,'free_ram_bytes':psutil.virtual_memory().available})
                last_sample=elapsed
            time.sleep(.5)
        report['steady_seconds']=round(time.monotonic()-began,1)
        report['requests']=db.query('SELECT event_id,status,created_at,finished_at,error FROM ai_requests')
        report['sources']=rows
        assert len(report['requests'])==4 and all(r['status']=='completed' for r in report['requests']), 'not all four automatic model calls completed'
        assert all(r['processed']>=200 for r in rows), 'insufficient capture progress'
        report['passed']=True
    except BaseException as exc:
        report['error']=str(exc);raise
    finally:
        if manager:manager.shutdown()
        if ai:ai.shutdown()
        stop.set()
        if camera:camera.shutdown();camera.server_close()
        if modelproc and modelproc.poll() is None:
            modelproc.terminate()
            try:modelproc.wait(timeout=20)
            except subprocess.TimeoutExpired:modelproc.kill();modelproc.wait(timeout=5)
        log.close();(run/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':report['passed'],'report':str(run/'result.json')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
