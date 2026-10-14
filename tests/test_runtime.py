"""Runtime regressions with isolated data and mocked providers; no cloud traffic."""
import os, sys, json, time, threading, sqlite3
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROJECT=ROOT/'src/task2_video_panel'
DATA = ROOT/'artifacts/tests'/'test_runtime'/uuid4().hex[:12]
DATA.mkdir(parents=True)
os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT), SCREEN_GUARD_DATA_DIR=str(DATA), SCREEN_GUARD_TESTING='1', PYTHONDONTWRITEBYTECODE='1')
for key in ('TEMP','TMP','TMPDIR','XDG_CACHE_HOME'):os.environ[key]=str(DATA)
sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part')]
import pytest, numpy as np
from fastapi.testclient import TestClient
from app.main import app
from app.core import database as db
from app.services import auto_review, source_profiles, settings, events, ai_connections as ai, diagnostics, maintenance, cases
from screen_guard_core import LABELS,decide

FRAME=np.random.default_rng(5).integers(20,230,(96,160,3),dtype=np.uint8)

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    # Every test owns its tasks; historical events cannot be newly submitted.
    db.execute("UPDATE source_profiles SET body=json_set(body,'$.ai.enabled',json('false'))")
    db.execute('DELETE FROM auto_jobs')
    ai.stopping.clear()
    monkeypatch.setattr(ai.wire,'invoke',lambda *args,**kw:{'scores':{k:.1 for k in LABELS},'needs_review':False,'evidence':['合成画面'],'usage':{}})
    yield
    deadline=time.monotonic()+3
    while db.query_one("SELECT COUNT(*) n FROM ai_requests WHERE status IN ('queued','running')")['n'] and time.monotonic()<deadline:time.sleep(.02)

def client():
    c=TestClient(app);c.headers['X-SG-CSRF']=c.get('/api/bootstrap').json()['csrf'];return c

def source(enabled=True, **ai_options):
    fixture=DATA/'uploads'/'fixture.dat';fixture.write_bytes(b'Synthetic backup fixture')
    sid=db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',('test-'+uuid4().hex[:8],'local',str(fixture)))
    row=ai.get_row('local-qwen');cfg=ai.Connection.model_validate_json(row['body']);cfg.vision=True;cfg.enabled=True;cfg.daily_limit=10000
    ai.save('local-qwen',ai.SaveConnection(expected_version=row['version'],config=cfg))
    p=source_profiles.SourceProfile().model_dump();p['ai'].update(enabled=enabled,min_interval_seconds=1,**ai_options)
    source_profiles.save(sid,p,0,'test')
    return sid

def event(sid, frame=FRAME, context=0, close=True):
    cfg=settings.Settings(audit_daily_per_source=0,context_frames=context,min_free_gib=0,segment_seconds=30).model_dump()
    cfg['_source_profile_version']=source_profiles.get(sid)['version']
    a=events.EventAccumulator(sid,uuid4().hex,{'id':'screen'},0,settings.current()['version'],cfg,'demo-rules')
    scores={k:.1 for k in LABELS};scores['gambling']=.7
    a.consume(frame,frame,0,1,scores,decide(scores,cfg['policies']))
    eid=a.active['id']
    if close:a.close()
    return eid,a

def complete(eid):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        auto_review.tick()
        row=auto_review.for_event(eid)
        if row and row['status'] not in auto_review.ACTIVE:return row
        time.sleep(.025)
    raise AssertionError(auto_review.for_event(eid))

def cloud(sid):
    name='cloud-'+uuid4().hex[:8]
    cfg=ai.Connection(name='Mock cloud',provider='compatible',protocol='chat',base_url='https://example.invalid/v1',model='mock',vision=True,key_env='SG_TEST_KEY')
    os.environ['SG_TEST_KEY']='test-key'
    ai.save(name,ai.SaveConnection(expected_version=0,config=cfg))
    p=source_profiles.get(sid)
    p['profile']['ai'].update(allow_cloud=True,cloud_connection=name)
    source_profiles.save(sid,p['profile'],p['version'],'test')
    return name

def test_automatic_result_preserves_review_and_notification_state():
    sid=source();eid,_=event(sid)
    job=complete(eid)
    assert job['status']=='completed' and job['outcome']=='low'
    e=events.event_detail(eid)
    assert e['review_state']=='pending' and e['kind']=='review'
    assert e['automatic_review']['requests'][0]['evidence_id']==job['evidence_id']
    assert not db.query_one('SELECT * FROM notifications WHERE event_id=?',(eid,))

def test_duplicate_frames_keep_events_but_do_not_call_twice():
    sid=source();one,_=event(sid);two,_=event(sid)
    assert auto_review.for_event(two)['status']=='skipped'
    assert auto_review.for_event(two)['related_job']==auto_review.for_event(one)['id']
    complete(one)
    assert db.query_one('SELECT COUNT(*) n FROM ai_requests WHERE event_id IN (?,?)',(one,two))['n']==1
    assert events.event_detail(two)['review_state']=='pending'

def test_local_unknown_falls_back_only_to_authorized_cloud(monkeypatch):
    sid=source();cid=cloud(sid);called=[]
    def invoke(cfg,*args,**kw):
        called.append(cfg['provider'])
        return {'scores':{k:.8 for k in LABELS},'needs_review':cfg['provider']=='local','evidence':['模拟依据'],'usage':{}}
    monkeypatch.setattr(ai.wire,'invoke',invoke)
    eid,_=event(sid);job=complete(eid)
    assert called==['local','compatible'] and job['outcome']=='risk' and job['cloud_request']
    assert events.event_detail(eid)['review_state']=='pending'

def test_cloud_not_authorized_is_never_called(monkeypatch):
    sid=source()
    monkeypatch.setattr(ai.wire,'invoke',lambda *a,**k:{'scores':{k:.1 for k in LABELS},'needs_review':True,'evidence':['未知']})
    eid,_=event(sid);job=complete(eid)
    assert job['outcome']=='unknown' and not job['cloud_request']

def test_revoked_profile_cancels_queued_work():
    sid=source();eid,_=event(sid);p=source_profiles.get(sid);p['profile']['ai']['enabled']=False
    source_profiles.save(sid,p['profile'],p['version'],'operator')
    assert complete(eid)['status']=='cancelled'
    assert not db.query_one('SELECT * FROM ai_requests WHERE event_id=?',(eid,))

def test_connection_edit_cancels_without_forwarding():
    sid=source();eid,_=event(sid)
    row=ai.get_row('local-qwen');cfg=ai.Connection.model_validate_json(row['body']);cfg.model='different'
    ai.save('local-qwen',ai.SaveConnection(expected_version=row['version'],config=cfg))
    assert complete(eid)['status']=='cancelled'

def test_manual_verdict_wins_over_queued_ai():
    sid=source();eid,_=event(sid);events.review(eid,'normal',[],'manual',0,0)
    assert complete(eid)['status']=='cancelled'
    assert events.event_detail(eid)['review_state']=='normal'

def test_timeout_is_unknown_not_normal(monkeypatch):
    sid=source()
    def fail(*args,**kw):raise ai.wire.ProviderError('调用超时；结果未知')
    monkeypatch.setattr(ai.wire,'invoke',fail)
    eid,_=event(sid);job=complete(eid)
    assert job['status']=='error' and job['outcome']=='unknown'
    assert events.event_detail(eid)['review_state']=='pending'

def test_queue_ttl_prevents_late_send():
    sid=source();eid,_=event(sid)
    db.execute('UPDATE auto_jobs SET expires_at=? WHERE event_id=?',(time.time()-1,eid))
    assert complete(eid)['status']=='expired'
    assert not db.query_one('SELECT * FROM ai_requests WHERE event_id=?',(eid,))

def test_queue_full_keeps_evidence(monkeypatch):
    sid=source();monkeypatch.setattr(auto_review,'QUEUE_LIMIT',1)
    one,_=event(sid);two,_=event(sid,255-FRAME)
    assert auto_review.for_event(two)['status']=='skipped'
    assert events.event_detail(two)['evidence']

def test_daily_budget_includes_failed_calls(monkeypatch):
    sid=source(daily_limit=1)
    def fail(*a,**kw):raise ai.wire.ProviderError('模拟失败')
    monkeypatch.setattr(ai.wire,'invoke',fail)
    first,_=event(sid);assert complete(first)['status']=='error'
    second,_=event(sid,255-FRAME);job=complete(second)
    assert job['status']=='skipped' and '每日' in job['reason']

def test_restart_links_reserved_request_without_resend():
    sid=source();eid,_=event(sid);job=auto_review.for_event(eid)
    req=ai.submit(ai.Submit(connection_id='local-qwen',expected_version=ai.get_row('local-qwen')['version'],request_id='auto_'+job['id']+'_local',kind='evidence',evidence_id=job['evidence_id']))
    db.execute("UPDATE auto_jobs SET status='submitting' WHERE event_id=?",(eid,))
    auto_review.recover()
    assert auto_review.for_event(eid)['local_request']==req['id']
    complete(eid)
    assert db.query_one('SELECT COUNT(*) n FROM ai_requests WHERE event_id=?',(eid,))['n']==1

def test_restart_unreserved_submission_is_not_repeated():
    sid=source();eid,_=event(sid)
    db.execute("UPDATE auto_jobs SET status='submitting' WHERE event_id=?",(eid,))
    auto_review.recover()
    assert auto_review.for_event(eid)['status']=='error'
    assert not db.query_one('SELECT * FROM ai_requests WHERE event_id=?',(eid,))

def test_last_moment_send_guard_checks_revocation():
    sid=source();eid,_=event(sid);job=db.query_one('SELECT * FROM auto_jobs WHERE event_id=?',(eid,))
    ver=ai.get_row('local-qwen')['version']
    assert auto_review.before_send(job,'local-qwen',ver)==''
    p=source_profiles.get(sid);p['profile']['ai']['enabled']=False
    source_profiles.save(sid,p['profile'],p['version'],'test')
    assert auto_review.before_send(job,'local-qwen',ver)

def test_cleanup_and_webhook_have_independent_clocks():
    blocked=threading.Event();began=threading.Event();ran=threading.Event()
    def slow():began.set();blocked.wait(2)
    one=maintenance.PeriodicTask('slow',.01,slow);two=maintenance.PeriodicTask('fast',.01,ran.set)
    try:
        one.start();assert began.wait(1);two.start()
        assert ran.wait(.5) and not blocked.is_set()
    finally:
        one.stop.set();two.stop.set();blocked.set();one.thread.join(1);two.thread.join(1)

def test_storage_status_does_not_scan_files(monkeypatch):
    from app.services import operations
    def forbidden():raise AssertionError('Request performed a scan')
    monkeypatch.setattr(operations,'storage_breakdown',forbidden)
    with client() as c:
        assert c.get('/api/operations/status').status_code==200
        assert c.get('/api/operations/badge').status_code==200

def test_stale_inference_is_unhealthy_even_with_new_frames():
    sid=source(False)
    db.execute("UPDATE video_sources SET desired_running=1,status='running',last_frame_at=?,last_inference_at=?,inference_state='ready' WHERE id=?",(time.time(),time.time()-100,sid))
    result=diagnostics.source_status(db.query_one('SELECT * FROM video_sources WHERE id=?',(sid,)))
    assert result['capture_ok'] and not result['inference_ok'] and not result['healthy']
    with client() as c:assert c.get('/api/health').json()['ok']

def test_per_source_config_does_not_change_global():
    sid=source(False);base=settings.current()
    p=source_profiles.get(sid);p['profile']['sample_seconds']=2.5
    source_profiles.save(sid,p['profile'],p['version'],'test')
    assert source_profiles.effective(sid)['settings']['sample_seconds']==2.5
    assert settings.current()==base
    with client() as c:assert next(s for s in c.get('/api/sources').json() if s['id']==sid)['configuration_pending']

def test_profile_optimistic_conflict():
    sid=source(False);p=source_profiles.get(sid)
    source_profiles.save(sid,p['profile'],p['version'],'one')
    with pytest.raises(RuntimeError):source_profiles.save(sid,p['profile'],p['version'],'two')

def test_midnight_schedule_belongs_to_start_day():
    p=source_profiles.SourceProfile(schedule_enabled=True,schedule_start='22:00',schedule_end='02:00',schedule_days=[0]).model_dump()
    from datetime import datetime,timezone
    assert source_profiles.within_schedule(p,'UTC',datetime(2026,10,13,1,tzinfo=timezone.utc).timestamp())
    assert not source_profiles.within_schedule(p,'UTC',datetime(2026,10,13,3,tzinfo=timezone.utc).timestamp())
    assert not source_profiles.within_schedule(p,'UTC',datetime(2026,10,14,1,tzinfo=timezone.utc).timestamp())

def test_webcam_creation_and_probe_endpoint(monkeypatch):
    from app.services import device_probe
    monkeypatch.setattr(device_probe,'probe',lambda s:(FRAME,time.time()))
    with client() as c:
        result=c.post('/api/video/webcam',json={'name':'USB','device_index':0}).json();sid=result['source_id']
        assert result['started'] is False
        assert db.query_one('SELECT source_type FROM video_sources WHERE id=?',(sid,))['source_type']=='webcam'
        response=c.post(f'/api/sources/{sid}/probe')
        assert response.status_code==200 and response.headers['content-type']=='image/jpeg'
        assert c.get(f'/api/sources/{sid}/frame').status_code==200

def test_bounded_context_frames_and_episode_links():
    sid=source(False);eid,a=event(sid,context=3,close=False)
    sc={k:.7 for k in LABELS}
    for t in range(1,29):
        a.consume(FRAME,FRAME,t,t,sc,decide(sc,a.cfg['policies']))
    a.close(continue_episode=True)
    detail=events.event_detail(eid)
    assert len([x for x in detail['evidence'] if x['role'].startswith('context')])==6
    a.consume(FRAME,FRAME,30,30,sc,decide(sc,a.cfg['policies']));new=a.active['id'];a.close()
    assert events.event_detail(new)['related_segments'][0]['id']==eid

def test_response_requires_confirmed_risk_and_keeps_audit():
    sid=source(False);eid,_=event(sid)
    with pytest.raises(ValueError):cases.save(eid,'resolved',0,'Alice','done','reviewer',0)
    events.review(eid,'confirmed',['gambling'],'risk',0,0)
    result=cases.save(eid,'in_progress',0,'Alice','联系现场','reviewer',1)
    result=cases.save(eid,'resolved',1,'Alice','现场已关闭播放','reviewer',1)
    assert len(result['history'])==2 and result['current']['state']=='resolved'
    with pytest.raises(RuntimeError):cases.save(eid,'pending',1,'Alice','stale','reviewer',1)
    events.review(eid,'normal',[],'corrected',1,0)
    assert not cases.get(eid)['applicable'] and len(cases.get(eid)['history'])==2
    events.review(eid,'confirmed',['privacy_leak'],'new finding',2,0)
    assert cases.get(eid)['current']['state']=='pending'
    assert len(cases.get(eid)['history'])==3

def test_reviewer_can_dispose_but_not_change_source(monkeypatch):
    from app.services import accounts
    sid=source(False);eid,_=event(sid);events.review(eid,'confirmed',['gambling'],'risk',0,0)
    monkeypatch.setenv('SCREEN_GUARD_AUTH_MODE','accounts')
    user='review-'+uuid4().hex[:8];accounts.create_user(user,'Reviewer-password-123','reviewer')
    c=TestClient(app);assert c.post('/api/auth/login',json={'username':user,'password':'Reviewer-password-123'}).status_code==200
    c.headers['X-SG-CSRF']=c.get('/api/bootstrap').json()['csrf']
    assert c.post(f'/api/events/{eid}/disposition',json={'state':'in_progress','expected_version':0,'review_version':1,'owner':'审核员','note':'已联系'}).status_code==200
    assert c.put(f'/api/sources/{sid}/profile',json={'expected_version':1,'profile':source_profiles.get(sid)['profile']}).status_code==403
    assert c.post(f'/api/sources/{sid}/probe').status_code==403
    c.close()

def test_migration_is_idempotent_preserves_data_and_rejects_future():
    from app.core.migrations import apply
    c=sqlite3.connect(':memory:');c.executescript(db.SCHEMA)
    c.execute("INSERT INTO video_sources(name,source_type,source_value) VALUES('keep','local','keep')")
    c.execute('PRAGMA user_version=4');apply(c);apply(c)
    assert c.execute('SELECT name FROM video_sources').fetchone()[0]=='keep'
    assert c.execute('PRAGMA user_version').fetchone()[0]==5
    c.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError):apply(c)
    c.close()

def test_restore_does_not_send_automatic_requests():
    from app.services import backups
    sid=source();event(sid)
    archive=backups.create()
    destination=DATA/'restored'
    backups.restore(backups.resolve(archive['name']),destination)
    with sqlite3.connect(destination/'screen_guard.db') as c:
        assert not json.loads(c.execute('SELECT body FROM source_profiles WHERE source_id=?',(sid,)).fetchone()[0])['ai']['enabled']
        assert c.execute("SELECT COUNT(*) FROM auto_jobs WHERE status IN ('pending','waiting','submitting','cloud_pending')").fetchone()[0]==0


def test_database_upgrade_snapshots_wal_and_refuses_active_runtime(monkeypatch):
    from app.services import runtime_lock
    original_path=db.DB_PATH
    folder=DATA/'upgrade';folder.mkdir()
    path=folder/'screen_guard.db'
    monkeypatch.setattr(db,'DB_PATH',path)
    monkeypatch.setattr(runtime_lock,'DATA_DIR',folder)
    old=sqlite3.connect(path)
    old.execute('PRAGMA journal_mode=WAL')
    old.executescript(db.SCHEMA)
    old.execute("INSERT INTO video_sources(name,source_type,source_value) VALUES('preserved WAL row','local','sample')")
    old.execute('PRAGMA user_version=4');old.commit()
    lock=runtime_lock.acquire()
    try:
        with pytest.raises(RuntimeError,match='另一个服务'):db.init_db()
        assert old.execute('PRAGMA user_version').fetchone()[0]==4
    finally:runtime_lock.release(lock)
    db.init_db();old.close()
    snapshots=list((folder/'backups').glob('schema-v4-*.db'))
    assert len(snapshots)==1
    with sqlite3.connect(snapshots[0]) as c:
        assert c.execute('PRAGMA user_version').fetchone()[0]==4
        assert c.execute('SELECT name FROM video_sources').fetchone()[0]=='preserved WAL row'
    assert db.query_one('SELECT name FROM video_sources')['name']=='preserved WAL row'
    db.init_db()
    assert len(list((folder/'backups').glob('schema-v4-*.db')))==1
    monkeypatch.setattr(db,'DB_PATH',original_path)


def test_storage_scan_tolerates_deleted_wal(monkeypatch):
    from app.services import operations
    folder=DATA/'scan';folder.mkdir()
    wal=folder/'screen_guard.db-wal';wal.write_bytes(b'temporary')
    monkeypatch.setattr(operations,'DATA_DIR',folder)
    original=Path.is_file
    def disappearing(path):
        if path==wal:
            path.unlink()
            return True
        return original(path)
    monkeypatch.setattr(Path,'is_file',disappearing)
    result=operations.storage_breakdown()
    assert result['categories']['database']==0


def test_last_moment_expiry_prevents_provider_send():
    sid=source();eid,_=event(sid)
    job=db.query_one('SELECT * FROM auto_jobs WHERE event_id=?',(eid,))
    job['expires_at']=time.time()-1
    assert '超时' in auto_review.before_send(job,'local-qwen',ai.get_row('local-qwen')['version'])

