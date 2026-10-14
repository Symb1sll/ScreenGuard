"""Disposable local UI/provider fixture; all files stay in this test directory."""
import os,sys,json,socket,time,threading
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'src/task2_video_panel'
HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part')]
def main():
    DATA=ROOT/'artifacts/tests/browser_runs'/uuid4().hex[:12];DATA.mkdir(parents=True)
    os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(DATA),SCREEN_GUARD_AUTH_MODE='local',PYTHONDONTWRITEBYTECODE='1')
    for k in ('TEMP','TMP','TMPDIR','XDG_CACHE_HOME'):os.environ[k]=str(DATA)
    import cv2,numpy as np,uvicorn
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from app.main import app
    from app.core import database as db
    from app.services import ai_connections as ai,events,settings,source_profiles
    from screen_guard_core import LABELS,decide
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            result={'scores':{k:.1 for k in LABELS},'needs_review':False,'evidence':['本机模拟接口：用于工程验收，不代表识别准确率']}
            content=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(result,ensure_ascii=False)}}],'usage':{'total_tokens':10}},ensure_ascii=False).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
        def log_message(self,*a):pass
    mock=ThreadingHTTPServer(('127.0.0.1',0),Provider);threading.Thread(target=mock.serve_forever,daemon=True).start()
    row=ai.get_row('local-qwen');cfg=ai.Connection.model_validate_json(row['body']);cfg.vision=True;cfg.base_url=f'http://127.0.0.1:{mock.server_port}/v1'
    ai.save('local-qwen',ai.SaveConnection(expected_version=row['version'],config=cfg,clear_key=True))
    cfg=settings.current();cfg['settings'].update(min_free_gib=0,context_frames=4,segment_seconds=10,audit_daily_per_source=0)
    settings.save(cfg['settings'],cfg['version'],'UI test fixture')
    frame=np.random.default_rng(9).integers(20,100,(240,400,3),dtype=np.uint8);frame[:,:,2]=200
    cv2.putText(frame,'SYNTHETIC SCREEN',(35,120),cv2.FONT_HERSHEY_SIMPLEX,.9,(240,240,240),2)
    video=DATA/'uploads'/'synthetic.avi'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),5,(400,240))
    for n in range(150):
        image=frame.copy();cv2.putText(image,str(n),(30,35),cv2.FONT_HERSHEY_SIMPLEX,.7,(200,200,200),2);writer.write(image)
    writer.release()
    sid=db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',('本机模拟验收大屏','local',str(video)))
    scores={k:.1 for k in LABELS};scores['gambling']=.7
    a=events.EventAccumulator(sid,'fixture',{'id':'screen-1'},0,settings.current()['version'],cfg['settings'],'demo-rules')
    for n in range(8):a.consume(frame,frame,n,n,scores,decide(scores,cfg['settings']['policies']))
    eid=a.active['id'];a.close(continue_episode=True)
    a.consume(frame,frame,10,10,scores,decide(scores,cfg['settings']['policies']));a.close()
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    state={'port':port,'url':f'http://127.0.0.1:{port}','pid':os.getpid(),'data':str(DATA),'source_id':sid,'event_id':eid,'mock_provider_port':mock.server_port}
    (ROOT/'artifacts/tests/browser_state.json').write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False),flush=True)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False))
    @app.post('/api/test-shutdown')
    def shutdown():server.should_exit=True;return {'ok':True}
    try:server.run()
    finally:mock.shutdown()
if __name__=='__main__':main()

