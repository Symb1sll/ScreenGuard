"""Local engineering acceptance with synthetic HTTP MJPEG cameras, no external traffic."""
import argparse,contextlib,json,os,socket,subprocess,sys,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
import cv2,httpx,numpy as np,psutil
ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'src/task2_video_panel'
def unused_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=60);args=parser.parse_args()
    if args.seconds<10:parser.error('steady-run seconds must be >=10')
    run=ROOT/'artifacts/tests'/'simulation'/uuid4().hex[:12];run.mkdir(parents=True)
    data=run/'data';data.mkdir()
    camera_on=threading.Event();camera_on.set()
    rng=np.random.default_rng(12)
    frame=rng.integers(50,180,(360,640,3),dtype=np.uint8)
    cv2.rectangle(frame,(100,60),(540,300),(30,70,210),-1)
    cv2.putText(frame,'SYNTHETIC TEST',(150,185),cv2.FONT_HERSHEY_SIMPLEX,1,(240,240,240),2)
    _,jpeg=cv2.imencode('.jpg',frame);jpeg=jpeg.tobytes()
    class Camera(BaseHTTPRequestHandler):
        def do_GET(self):
            if not camera_on.is_set():self.send_error(503);return
            self.send_response(200);self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame');self.end_headers()
            try:
                while camera_on.is_set():
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: '+str(len(jpeg)).encode()+b'\r\n\r\n'+jpeg+b'\r\n')
                    self.wfile.flush();time.sleep(.1)
            except (BrokenPipeError,ConnectionResetError,OSError):pass
            self.close_connection=True
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Camera);server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    port=unused_port();url=f'http://127.0.0.1:{port}'
    env=dict(os.environ,SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(data),PYTHONDONTWRITEBYTECODE='1',TEMP=str(run),TMP=str(run),TMPDIR=str(run),SCREEN_GUARD_AUTH_MODE='local')
    env.pop('SCREEN_GUARD_TESTING',None)
    log=(run/'server.log').open('a',encoding='utf-8')
    process=None;checks={};observations=[];began=time.time()
    client=httpx.Client(base_url=url,timeout=10,trust_env=False);csrf=''
    def launch():
        nonlocal process,csrf
        process=subprocess.Popen([sys.executable,'-X','utf8','-B',str(PROJECT/'manage.py'),'--data-dir',str(data),'serve','--port',str(port)],cwd=PROJECT,env=env,stdout=log,stderr=log)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('test application exited; inspect '+str(run/'server.log'))
            try:
                r=client.get('/api/bootstrap')
                if r.status_code==200:csrf=r.json()['csrf'];return
            except httpx.HTTPError:pass
            time.sleep(.2)
        raise TimeoutError('application startup')
    def request(method,path,**kwargs):
        headers={'X-SG-CSRF':csrf}
        r=client.request(method,'/api'+path,headers=headers,**kwargs);r.raise_for_status();return r.json()
    def sources():return request('GET','/sources')
    def until(predicate,timeout,description):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            rows=sources()
            if predicate(rows):return rows
            time.sleep(.3)
        raise AssertionError(description+': '+json.dumps(rows,ensure_ascii=False))
    def kill_tree():
        nonlocal process
        if process and process.poll() is None:
            parent=psutil.Process(process.pid);children=parent.children(recursive=True)
            parent.terminate()
            for child in children:
                with contextlib.suppress(psutil.NoSuchProcess):child.terminate()
            psutil.wait_procs([parent,*children],timeout=5)
            for child in [parent,*children]:
                with contextlib.suppress(psutil.NoSuchProcess):
                    if child.is_running():child.kill()
            process.wait(timeout=5)
    try:
        launch();cfg=request('GET','/settings')
        cfg['settings'].update(sample_seconds=.5,min_free_gib=0,audit_daily_per_source=2)
        request('POST','/settings',json={'expected_version':cfg['version'],'settings':cfg['settings']})
        ids=[]
        for i in range(4):
            sid=request('POST','/video/rtsp',json={'name':f'模拟摄像头 {i+1}','url':f'http://127.0.0.1:{server.server_port}/camera/{i}','auto_start':True})['source_id'];ids.append(sid)
            if i==0:
                until(lambda rows:any(r['id']==sid and r['processed']>=3 for r in rows),20,'single-camera capture')
                checks['single_camera']=True;print('单路模拟采集通过',flush=True)
        rows=until(lambda rows:all(any(r['id']==sid and r['status']=='running' and r['processed']>=3 for r in rows) for sid in ids),25,'four-camera capture')
        checks['four_cameras']=True;print('四路模拟并发通过',flush=True)
        fifth=request('POST','/video/rtsp',json={'name':'超限模拟源','url':f'http://127.0.0.1:{server.server_port}/camera/5','auto_start':False})['source_id']
        assert client.post(f'/api/video/{fifth}/start',headers={'X-SG-CSRF':csrf}).status_code==400
        checks['four_camera_limit']=True
        camera_on.clear()
        until(lambda rows:any(r['id'] in ids and r['status']=='reconnecting' for r in rows),20,'disconnect surfaced')
        checks['disconnect_visible']=True;recover_start=time.monotonic();camera_on.set()
        until(lambda rows:all(any(r['id']==sid and r['status']=='running' and time.time()-r['last_frame_at']<3 for r in rows) for sid in ids),35,'network recovery')
        checks['network_recovery_seconds']=round(time.monotonic()-recover_start,2)
        print('断流及恢复通过',flush=True)
        request('POST',f'/video/{ids[0]}/stop')
        until(lambda rows:any(r['id']==ids[0] and r['status']=='stopped' and not r['desired_running'] for r in rows),10,'manual stop')
        before={r['id']:r['job_id'] for r in sources()}
        kill_tree();launch()
        rows=until(lambda rows:all(any(r['id']==sid and r['status']=='running' and r['processed']>=2 and r['job_id']!=before[sid] for r in rows) for sid in ids[1:]),25,'restart restores desired sources')
        assert next(r for r in rows if r['id']==ids[0])['status']=='stopped'
        checks['restart_restores_only_desired']=True;print('服务重启恢复与手动停机保留通过',flush=True)
        request('POST',f'/video/{ids[0]}/start')
        until(lambda rows:all(any(r['id']==sid and r['status']=='running' and r['processed']>=2 for r in rows) for sid in ids),20,'four camera restart')
        stable=time.monotonic();base={r['id']:r['processed'] for r in sources()}
        while time.monotonic()-stable<args.seconds:
            rows=sources()
            if any(r['status']!='running' or time.time()-(r['last_frame_at'] or 0)>5 for r in rows if r['id'] in ids):raise AssertionError('steady capture stale')
            proc=psutil.Process(process.pid)
            rss=proc.memory_info().rss+sum(p.memory_info().rss for p in proc.children(recursive=True) if p.is_running())
            observations.append({'seconds':round(time.monotonic()-stable,1),'rss_bytes':rss,'processed':{r['id']:r['processed'] for r in rows if r['id'] in ids}})
            time.sleep(1)
        final=sources()
        assert all(next(r for r in final if r['id']==sid)['processed']>base[sid]+args.seconds for sid in ids)
        checks['steady_seconds']=args.seconds;checks['evidence_events']=request('GET','/events?limit=1')['total']
        assert checks['evidence_events']>0
        cfg=request('GET','/settings');cfg['settings']['auto_restore_sources']=False
        request('POST','/settings',json={'expected_version':cfg['version'],'settings':cfg['settings']})
        for sid in ids:request('POST',f'/video/{sid}/stop')
        until(lambda rows:all(r['status']=='stopped' for r in rows if r['id'] in ids),10,'all workers stop')
        checks['all_stop']=True
        print('稳定采集与全部停止通过',flush=True)
        status='passed'
    except BaseException as exc:
        status='failed';checks['error']=str(exc);raise
    finally:
        kill_tree();camera_on.clear();server.shutdown();server.server_close();client.close();log.close()
        report={'status':locals().get('status','failed'),'started_at':began,'elapsed_seconds':round(time.time()-began,1),'scope':'synthetic local HTTP MJPEG only; engineering verification, not RTSP hardware or detection accuracy','checks':checks,'observations':observations,'run_directory':str(run)}
        (run/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (ROOT/'artifacts/tests'/'simulation_latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':report['status'],'report':str(run/'report.json')},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
