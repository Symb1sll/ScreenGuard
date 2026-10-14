"""Independent integration tests; never opens production DB or external endpoints."""
import os,sys,json,time,hashlib,contextlib
from pathlib import Path
from uuid import uuid4
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
PROJECT=ROOT/'src/task2_video_panel'
DATA=ROOT/'artifacts/tests'/'test_enterprise'/uuid4().hex[:12];DATA.mkdir(parents=True)
for key in ('TEMP','TMP','TMPDIR','XDG_CACHE_HOME'):os.environ[key]=str(DATA)
os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(DATA),SCREEN_GUARD_TESTING='1')
sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part'),str(PROJECT/'recognition_part/benchmarking')]
import pytest,numpy as np
from fastapi.testclient import TestClient
from app.main import app
from app.core import database as db
from app.services import events,evidence,settings
from screen_guard_core import LABELS,decide
FRAME=np.random.default_rng(4).integers(0,255,(64,96,3),dtype=np.uint8)
@pytest.fixture
def cfg():return settings.Settings(audit_daily_per_source=0,auto_alerts=True).model_dump()
def accumulator(cfg,sid=None):
    sid=sid or db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',('synthetic','local','not-a-file'))
    return events.EventAccumulator(sid,uuid4().hex,{'id':'roi-1'},0,settings.current()['version'],cfg,'test')
def feed(a,t,frame=FRAME,clock=None,**kw):
    scores={k:.1 for k in LABELS};scores.update(kw)
    a.consume(frame,frame,t,int(t+1),scores,decide(scores,a.cfg['policies'],model_validated=True,auto_alerts=True),captured_at=clock)
def test_review_requires_immutable_segment_and_new_frames_stay_pending(cfg):
    a=accumulator(cfg);feed(a,0,gambling=.7);eid=a.active['id']
    with pytest.raises(RuntimeError):events.review(eid,'normal',[],'',0,0)
    old=events.event_detail(eid);sealed=events.freeze(eid,old['content_version'])
    events.review(eid,'normal',[],'first segment',0,100,sealed['content_version'])
    feed(a,1,gambling=.99);feed(a,2,gambling=.99);new=events.event_detail(a.active['id'])
    assert new['id']!=eid and new['kind']=='alert' and new['review_state']=='pending'
    assert new['episode_id']==sealed['episode_id']
    assert events.event_detail(eid)['content_version']==sealed['content_version']
    verdict=events.event_detail(eid)['reviews'][0]['result']
    assert verdict['media_end']==0 and verdict['evidence']
    a.close()
def test_freeze_conflict(cfg):
    a=accumulator(cfg);feed(a,0,gambling=.7);eid=a.active['id'];version=events.event_detail(eid)['content_version']
    feed(a,1,gambling=.8)
    with pytest.raises(RuntimeError):events.freeze(eid,version)
    a.close()
def test_normal_gap_and_minor_label_do_not_split_or_bypass_cooldown(cfg):
    a=accumulator(cfg);feed(a,0,gambling=.99);feed(a,1,gambling=.99);eid=a.active['id']
    feed(a,2);feed(a,3,gambling=.99,privacy_leak=.6);feed(a,4,gambling=.99,privacy_leak=.6)
    assert a.active['id']==eid
    a.close(continue_episode=True)
    feed(a,5,gambling=.99,privacy_leak=.6);feed(a,6,gambling=.99,privacy_leak=.6)
    assert events.event_detail(a.active['id'])['kind']=='review';a.close()
def test_each_high_label_confirms_independently(cfg):
    a=accumulator(cfg);feed(a,0,gambling=.99);feed(a,1,gambling=.99,privacy_leak=.99)
    assert events.event_detail(a.active['id'])['kind']=='alert'
    rows=db.query('SELECT label FROM alert_cooldowns WHERE source_id=?',(a.source_id,))
    assert [r['label'] for r in rows]==['gambling'];a.close()
def test_checkpoint_is_durable_and_old_ai_evidence_immutable(cfg):
    a=accumulator(cfg);feed(a,0,gambling=.6);eid=a.active['id']
    first=next(e for e in events.event_detail(eid)['evidence'] if e['role']=='peak_roi')
    feed(a,2,frame=255-FRAME,gambling=.95)
    peak=next(e for e in events.event_detail(eid)['evidence'] if e['role']=='peak_roi')
    assert peak['id']!=first['id'] and peak['media_time']==2
    old=db.query_one('SELECT * FROM evidence WHERE id=?',(first['id'],))
    assert hashlib.sha256(evidence.resolve(old['path']).read_bytes()).hexdigest()==first['sha256']
    a.close()
def test_segments_are_bounded(cfg):
    cfg['segment_seconds']=5;a=accumulator(cfg);feed(a,0,gambling=.7);eid=a.active['id']
    feed(a,5,gambling=.7)
    assert a.active['id']!=eid and events.event_detail(eid)['lifecycle']=='closed';a.close()
def test_audit_budget_shared_by_rois_persistent_and_duplicates_skipped(cfg):
    cfg['audit_daily_per_source']=2;a=accumulator(cfg)
    # 2026-01-01 00:00 UTC, budget day uses configured Shanghai time.
    feed(a,0,clock=1767225600);feed(a,1,clock=1767225601)
    b=accumulator(cfg,a.source_id);b.roi={'id':'roi-2'}
    feed(b,2,frame=255-FRAME,clock=1767225602)
    assert db.query_one('SELECT COUNT(*) n FROM events WHERE source_id=?',(a.source_id,))['n']==1
    feed(a,20000,clock=1767245600)  # Same visual must not be sampled again in the next bucket.
    feed(a,20001,frame=255-FRAME,clock=1767245601)
    assert db.query_one('SELECT COUNT(*) n FROM events WHERE source_id=?',(a.source_id,))['n']==2
    feed(a,20002,gambling=.7,clock=1767245602)
    assert a.active and events.event_detail(a.active['id'])['kind']=='review';a.close()
def test_shared_evidence_survives_other_event_expiry(cfg):
    a=accumulator(cfg);feed(a,0,gambling=.7);one=a.active['id'];a.close()
    b=accumulator(cfg);feed(b,0,gambling=.7);two=b.active['id'];b.close()
    events.review(two,'confirmed',['gambling'],'keep',0,0)
    db.execute('UPDATE events SET updated_at=? WHERE id=?',(time.time()-9*86400,one));evidence.cleanup()
    assert events.event_detail(one)['expired']
    for row in db.query('SELECT path FROM evidence WHERE event_id=?',(two,)):assert evidence.resolve(row['path']).exists()
def test_online_offline_contract_same():
    from providers import normalize_payload
    import model_api
    from app.services import ai_protocols
    assert model_api is ai_protocols
    with pytest.raises(model_api.ProviderError):normalize_payload({},list(LABELS))
    payload={'scores':{k:.2 for k in LABELS},'needs_review':True,'evidence':['uncertain']}
    assert normalize_payload(payload,list(LABELS))[0]==model_api.parse_result(json.dumps(payload))['scores']

def test_missing_evidence_cannot_be_called_normal(cfg):
    a=accumulator(cfg);feed(a,0,gambling=.7);eid=a.active['id'];a.close()
    db.execute('DELETE FROM evidence WHERE event_id=?',(eid,))
    with pytest.raises(ValueError):events.review(eid,'normal',[],'',0,0)
    events.review(eid,'uncertain',[],'storage failure',0,0)

def test_enterprise_roles_csrf_sessions_and_actor(monkeypatch,cfg):
    from app.services import accounts,operations
    monkeypatch.setenv('SCREEN_GUARD_AUTH_MODE','accounts')
    accounts.create_user('admin01','Admin-password-123','admin')
    accounts.create_user('reviewer01','Review-password-123','reviewer')
    reviewer=TestClient(app)
    assert reviewer.get('/api/overview').status_code==401
    bad=reviewer.post('/api/auth/login',json={'username':'reviewer01','password':'bad'})
    assert bad.status_code==401
    r=reviewer.post('/api/auth/login',json={'username':'reviewer01','password':'Review-password-123'})
    assert r.status_code==200 and 'httponly' in r.headers['set-cookie'].lower()
    boot=reviewer.get('/api/bootstrap').json();csrf=boot['csrf']
    assert boot['role']=='reviewer' and boot['operator']=='reviewer01'
    assert reviewer.get('/api/bootstrap').json()['csrf']==csrf
    h={'X-SG-CSRF':csrf}
    assert reviewer.post('/video/1/start').status_code==404
    assert reviewer.get('/api/operations/backups').status_code==403
    assert reviewer.post('/api/auth/users',headers=h,json={'username':'other','password':'New-password-123'}).status_code==403
    a=accumulator(cfg);feed(a,0,gambling=.7);eid=a.active['id'];a.close()
    data={'expected_version':0,'content_version':events.event_detail(eid)['content_version'],'state':'normal'}
    assert reviewer.post(f'/api/events/{eid}/review',json=data).status_code==403
    assert reviewer.post(f'/api/events/{eid}/review',headers=h,json=data).status_code==200
    assert events.event_detail(eid)['reviews'][0]['actor']=='reviewer01'
    operations.emit('role-test','summary','test')
    nid=db.query_one("SELECT id FROM notifications WHERE dedupe='role-test'")['id']
    assert reviewer.post(f'/api/operations/notifications/{nid}/ack',headers=h).status_code==200
    assert db.query_one('SELECT acked_by FROM notifications WHERE id=?',(nid,))['acked_by']=='reviewer01'
    admin=TestClient(app)
    assert admin.post('/api/auth/login',json={'username':'admin01','password':'Admin-password-123'}).status_code==200
    admin_csrf=admin.get('/api/bootstrap').json()['csrf']
    assert admin.get('/api/auth/users').status_code==200
    assert admin.put('/api/auth/users/reviewer01',headers={'X-SG-CSRF':admin_csrf},json={'enabled':False}).status_code==200
    assert reviewer.get('/api/overview').status_code==401
    with pytest.raises(ValueError):accounts.set_enabled('admin01',False)
    assert admin.post('/api/auth/logout',headers={'X-SG-CSRF':admin_csrf}).status_code==200
    assert admin.get('/api/overview').status_code==401
    logs=json.dumps(db.query('SELECT * FROM audit_log'))
    assert 'Admin-password-123' not in logs and 'Review-password-123' not in logs

def test_remote_enterprise_requires_https_and_explicit_host(monkeypatch):
    monkeypatch.setenv('SCREEN_GUARD_AUTH_MODE','accounts')
    monkeypatch.setenv('SCREEN_GUARD_ALLOWED_HOSTS','review.example.internal')
    remote=TestClient(app,base_url='http://review.example.internal',client=('192.0.2.2',123))
    assert remote.get('/api/health').status_code==403
    secure=TestClient(app,base_url='https://review.example.internal',client=('192.0.2.2',123))
    assert secure.get('/api/health').status_code==200
    assert secure.post('/api/auth/login',headers={'Origin':'https://evil.example'},json={'username':'admin01','password':'bad'}).status_code==403
    unknown=TestClient(app,base_url='https://unlisted.example',client=('192.0.2.2',123))
    assert unknown.get('/api/health').status_code==403

def test_idle_poll_does_not_extend_session(monkeypatch):
    from app.services import accounts
    accounts.create_user('idle01','Idle-password-123')
    token,csrf=accounts.authenticate('idle01','Idle-password-123','127.0.0.1')
    db.execute('UPDATE sessions SET last_at=? WHERE token_hash=?',(time.time()-1000,accounts.digest(token)))
    old=accounts.session(token)['last_at']
    assert accounts.session(token)['last_at']==old
    accounts.session(token,touch=True)
    assert accounts.session(token)['last_at']>old
    accounts.change_password('idle01','Changed-password-123')
    assert accounts.session(token) is None

def test_login_rate_limit_commits_failures():
    from app.services import accounts
    for _ in range(5):
        with pytest.raises(ValueError,match='账号或密码错误'):accounts.authenticate('limit01','wrong','192.0.2.10')
    with pytest.raises(ValueError,match='登录尝试过多'):accounts.authenticate('limit01','wrong','192.0.2.10')
    assert db.query_one('SELECT failures FROM login_limits WHERE identity=?',(accounts.digest('192.0.2.10\nlimit01'),))['failures']==5

def test_webhook_delivery_idempotency_retries_redaction_and_version(monkeypatch):
    from app.services import operations
    monkeypatch.setenv('SCREEN_GUARD_WEBHOOK_URL','https://hooks.example.test/private-secret')
    cfg=operations.webhook_status();operations.save_webhook(cfg['version'],True,key_env='SCREEN_GUARD_WEBHOOK_URL',clear=True)
    sent=[]
    class MockClient:
        def __init__(self,**kw):assert kw['follow_redirects'] is False and kw['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextlib.contextmanager
        def stream(self,method,url,content,headers):
            assert method=='POST'
            sent.append((json.loads(content),headers))
            yield type('Response',(),{'status_code':503})()
    monkeypatch.setattr(operations.httpx,'Client',MockClient)
    db.execute("UPDATE notifications SET delivery='disabled'")
    operations.emit('delivery-test','alert','test',1,1);operations.emit('delivery-test','alert','duplicate')
    row=db.query_one("SELECT * FROM notifications WHERE dedupe='delivery-test'")
    for _ in range(3):
        db.execute('UPDATE notifications SET next_at=0 WHERE id=?',(row['id'],));operations.deliver_one()
    row=db.query_one('SELECT * FROM notifications WHERE id=?',(row['id'],))
    assert row['delivery']=='failed' and row['attempts']==3 and len(sent)==3
    assert {x[1]['X-ScreenGuard-Delivery'] for x in sent}=={row['id']}
    assert 'private-secret' not in row['error'] and 'evidence' not in sent[0][0]
    operations.retry(row['id'])
    cfg=operations.webhook_status();operations.save_webhook(cfg['version'],True,key_env='SCREEN_GUARD_WEBHOOK_URL')
    operations.deliver_one()
    assert len(sent)==3 and db.query_one('SELECT delivery FROM notifications WHERE id=?',(row['id'],))['delivery']=='failed'
    operations.retry(row['id'])
    db.execute("UPDATE notifications SET delivery='sending' WHERE id=?",(row['id'],));operations.recover()
    assert db.query_one('SELECT delivery FROM notifications WHERE id=?',(row['id'],))['delivery']=='pending'
    operations.save_webhook(operations.webhook_status()['version'],False,clear=True)

def test_webhook_quiet_hours_delay_only_summary(monkeypatch):
    from app.services import operations
    monkeypatch.setenv('SCREEN_GUARD_WEBHOOK_URL','https://hooks.example.test/test')
    operations.save_webhook(operations.webhook_status()['version'],True,clear=True)
    db.execute("UPDATE notifications SET delivery='disabled'")
    sent=[]
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        @contextlib.contextmanager
        def stream(self,method,url,content,headers):sent.append(json.loads(content));yield type('Response',(),{'status_code':204})()
    monkeypatch.setattr(operations.httpx,'Client',Client);monkeypatch.setattr(operations,'in_quiet_hours',lambda:True)
    operations.emit('quiet-summary','summary','summary');operations.emit('quiet-alert','alert','alert')
    operations.deliver_one();operations.deliver_one()
    assert len(sent)==1 and sent[0]['kind']=='alert'
    assert db.query_one("SELECT delivery FROM notifications WHERE dedupe='quiet-summary'")['delivery']=='pending'
    operations.save_webhook(operations.webhook_status()['version'],False,clear=True)

def test_source_lifecycle_and_report_formula_escape():
    client=TestClient(app);csrf=client.get('/api/bootstrap').json()['csrf'];h={'X-SG-CSRF':csrf}
    sid=db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',('=formula','rtsp','rtsp://user:pass@127.0.0.1:9999/x'))
    from app.services.video_worker import manager
    from threading import Event
    manager.stop_flags[sid]=Event()
    value={'name':'camera','location':'office','archived':True}
    assert client.put(f'/api/operations/sources/{sid}',headers=h,json=value).status_code==409
    manager.stop_flags.pop(sid)
    assert client.put(f'/api/operations/sources/{sid}',headers=h,json=value).status_code==200
    assert sid not in [s['id'] for s in client.get('/api/sources').json()]
    value['archived']=False
    assert client.put(f'/api/operations/sources/{sid}',headers=h,json=value).status_code==200
    sources=json.dumps(client.get('/api/sources').json())
    assert 'user:pass' not in sources
    report=client.get('/api/operations/report.csv');assert report.status_code==200 and 'content_version' in report.text

def test_desired_running_restores_and_manual_stop_cancels(monkeypatch):
    from app.services.video_worker import JobManager
    m=JobManager()
    sid=db.execute("INSERT INTO video_sources(name,source_type,source_value,desired_running) VALUES('restore','rtsp','rtsp://127.0.0.1/test',1)")
    calls=[];monkeypatch.setattr(m,'start',lambda id:calls.append(id))
    m.restore();assert sid in calls
    m.stop(sid);calls.clear();m.restore();assert sid not in calls
    m.shutdown()

def test_hold_preserves_then_release_expires(cfg):
    client=TestClient(app);csrf=client.get('/api/bootstrap').json()['csrf'];h={'X-SG-CSRF':csrf}
    a=accumulator(cfg);feed(a,0,gambling=.7);eid=a.active['id'];a.close()
    db.execute('UPDATE events SET updated_at=? WHERE id=?',(time.time()-9*86400,eid))
    assert client.post(f'/api/events/{eid}/hold',headers=h,json={'locked':True,'reason':'keep incident'}).status_code==200
    evidence.cleanup();assert not events.event_detail(eid)['expired']
    assert client.post(f'/api/events/{eid}/hold',headers=h,json={'locked':False,'reason':'review complete'}).status_code==200
    evidence.cleanup();assert events.event_detail(eid)['expired']

def test_backup_restore_checksums_no_overwrite_and_camera_safe(cfg):
    import sqlite3,zipfile
    from contextlib import closing
    from app.services import backups
    db.execute("UPDATE video_sources SET archived=1,desired_running=0")
    path=DATA/'uploads'/'backup-video.mp4';path.write_bytes(b'test-video')
    sid=db.execute("INSERT INTO video_sources(name,source_type,source_value,desired_running) VALUES('backup','local',?,1)",(str(path),))
    a=accumulator(cfg,sid);feed(a,0,gambling=.7);eid=a.active['id'];a.close()
    result=backups.create();archive=backups.resolve(result['name'])
    restored=DATA/'restored'
    backups.restore(archive,restored)
    with closing(sqlite3.connect(restored/'screen_guard.db')) as c:
        value,desired=c.execute('SELECT source_value,desired_running FROM video_sources WHERE id=?',(sid,)).fetchone()
        assert Path(value).is_relative_to(restored) and Path(value).read_bytes()==b'test-video' and desired==0
        for rel,sha in c.execute('SELECT path,sha256 FROM evidence WHERE event_id=?',(eid,)):
            assert hashlib.sha256((restored/rel).read_bytes()).hexdigest()==sha
        assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    with pytest.raises(ValueError,match='新目录'):backups.restore(archive,restored)
    bad=DATA/'bad.zip'
    with zipfile.ZipFile(archive) as src,zipfile.ZipFile(bad,'w') as dst:
        for name in src.namelist():dst.writestr(name,b'tampered' if name.endswith('.jpg') else src.read(name))
    with pytest.raises(ValueError):backups.restore(bad,DATA/'bad-restored')
    assert not (DATA/'bad-restored').exists()

def test_timed_inference_spawn_returns_and_closes():
    from app.services.inference import TimedModel
    from threading import Event
    stop=Event();model=TimedModel('demo-rules',stop)
    try:
        assert set(model.predict(FRAME))==set(LABELS)
        stop.set()
        with pytest.raises(InterruptedError):model.predict(FRAME)
    finally:model.close()
    assert not model.process.is_alive()

def test_runtime_directory_excludes_second_process():
    import subprocess
    from app.services import runtime_lock
    code="import sys;sys.path.insert(0,"+repr(str(PROJECT/'framework_part'))+");from app.services.runtime_lock import acquire;handle=acquire()"
    with runtime_lock.locked():
        result=subprocess.run([sys.executable,'-X','utf8','-B','-c',code],capture_output=True,text=True,encoding='utf-8')
        assert result.returncode!=0 and 'RuntimeError' in result.stderr
    result=subprocess.run([sys.executable,'-X','utf8','-B','-c',code],capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0

def test_source_fault_does_not_realert_during_starting():
    from app.services import operations
    sid=db.execute("INSERT INTO video_sources(name,source_type,source_value,status,desired_running,inference_state) VALUES('fault-test','rtsp','rtsp://127.0.0.1/test','error',1,'error')")
    operations.sweep()
    old=db.query_one('SELECT * FROM source_health WHERE source_id=?',(sid,))
    count=db.query_one('SELECT COUNT(*) n FROM notifications WHERE source_id=?',(sid,))['n']
    db.execute("UPDATE video_sources SET status='starting',inference_state='loading' WHERE id=?",(sid,))
    operations.sweep()
    assert db.query_one('SELECT incident FROM source_health WHERE source_id=?',(sid,))['incident']==old['incident']
    assert db.query_one('SELECT COUNT(*) n FROM notifications WHERE source_id=?',(sid,))['n']==count
    db.execute("UPDATE video_sources SET status='running',last_frame_at=?,last_inference_at=?,inference_state='ready' WHERE id=?",(time.time(),time.time(),sid))
    operations.sweep()
    assert db.query_one("SELECT COUNT(*) n FROM notifications WHERE source_id=? AND kind='recovery'",(sid,))['n']==1

def test_native_package_validation_and_prediction_are_isolated():
    import onnx
    from onnx import helper,TensorProto,numpy_helper
    from threading import Event
    from app.services import models
    from app.services.inference import TimedModel
    from screen_guard_core import PREPROCESSING
    package=DATA/'models'/'native-test';package.mkdir()
    graph=helper.make_graph([helper.make_node('Constant',[],['output'],value=numpy_helper.from_array(np.array([[-2,-1,0,1,2]],np.float32)))],'test',[helper.make_tensor_value_info('input',TensorProto.FLOAT,[1,3,32,32])],[helper.make_tensor_value_info('output',TensorProto.FLOAT,[1,5])])
    model=helper.make_model(graph,opset_imports=[helper.make_opsetid('',13)],ir_version=10)
    weights=package/'model.onnx';onnx.save(model,weights)
    meta={'model_id':'native-test','format_version':1,'labels':list(LABELS),'preprocessing':PREPROCESSING,'output':'logits','image_size':32,'sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),'validation_status':'candidate'}
    (package/'manifest.json').write_text(json.dumps(meta),encoding='utf-8')
    assert models.validate_package(package)['model_id']=='native-test'
    assert not models.validate_model('native-test').validated
    timed=TimedModel('native-test',Event())
    try:
        scores=timed.predict(FRAME)
        assert set(scores)==set(LABELS) and all(0<=x<=1 for x in scores.values())
        timed.predict_timeout=0
        with pytest.raises(TimeoutError):timed.predict(FRAME)
        assert not timed.process.is_alive()
    finally:timed.close()
    assert 'native-test' not in models._cache

def test_backup_rejects_corrupt_registered_evidence():
    from app.services import backups
    row=db.query_one('SELECT * FROM evidence LIMIT 1')
    path=evidence.resolve(row['path']);original=path.read_bytes();before=len(backups.list_backups())
    try:
        path.write_bytes(b'corrupt-evidence')
        with pytest.raises(ValueError,match='校验失败'):backups.create()
        assert len(backups.list_backups())==before
    finally:path.write_bytes(original)

def test_backup_compression_allows_new_capture_but_snapshot_stays_fixed(monkeypatch,cfg):
    import threading,sqlite3
    from contextlib import closing
    from app.services import backups
    sid=db.execute("INSERT INTO video_sources(name,source_type,source_value) VALUES('during-backup','rtsp','rtsp://127.0.0.1/test')")
    before=db.query_one('SELECT COALESCE(MAX(id),0) n FROM events')['n']
    done=threading.Event();errors=[]
    def capture():
        try:
            a=accumulator(cfg,sid);feed(a,0,gambling=.7);a.close()
        except Exception as exc:errors.append(exc)
        finally:done.set()
    original=backups.zipfile.ZipFile
    class ObservedZip(original):
        def __enter__(self):
            thread=threading.Thread(target=capture);thread.start()
            assert done.wait(3),'camera writes blocked by archive compression'
            thread.join();assert not errors
            return super().__enter__()
    monkeypatch.setattr(backups.zipfile,'ZipFile',ObservedZip)
    result=backups.create();monkeypatch.setattr(backups.zipfile,'ZipFile',original)
    restored=DATA/'concurrent-restored';backups.restore(backups.resolve(result['name']),restored)
    assert db.query_one('SELECT MAX(id) n FROM events')['n']>before
    with closing(sqlite3.connect(restored/'screen_guard.db')) as c:
        assert c.execute('SELECT MAX(id) FROM events').fetchone()[0]==before
