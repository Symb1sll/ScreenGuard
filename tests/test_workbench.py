"""Regression tests run against an isolated database under this delivery directory."""
import os,sys,time,json,hashlib,copy
from pathlib import Path
from uuid import uuid4
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
PROJECT=ROOT/'src/task2_video_panel'
DATA=ROOT/'artifacts/tests'/'test_workbench'/uuid4().hex[:12]
DATA.mkdir(parents=True)
for key in ('TEMP','TMP','TMPDIR','MPLCONFIGDIR','XDG_CACHE_HOME'):os.environ[key]=str(DATA)
os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(DATA),SCREEN_GUARD_TESTING='1',PYTHONDONTWRITEBYTECODE='1')
sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part'),str(PROJECT/'recognition_part'/'training')]
import pytest,numpy as np,cv2
from PIL import Image
from fastapi.testclient import TestClient
from screen_guard_core import *
from app.main import app
from app.core import database as db
from app.services import settings,regions,events,evidence,comparison,models
from app.services.video_worker import JobManager
from multilabel_common import build_transforms
@pytest.fixture
def cfg():
    return settings.Settings(audit_sample_rate=0,audit_daily_per_source=0).model_dump()
@pytest.fixture
def source():
    return db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',('test','local','synthetic'))

@pytest.fixture
def client():
    c=TestClient(app,raise_server_exceptions=True)
    token=c.get('/api/bootstrap').json()['csrf'];c.headers['X-SG-CSRF']=token
    yield c
    c.close()
def scores(**kw):
    result={k:.1 for k in LABELS};result.update(kw);return result
def acc(source,cfg):
    return events.EventAccumulator(source,'job',{'id':'screen-1'},0,settings.current()['version'],cfg,'test-model')
FRAME=np.random.default_rng(42).integers(0,255,(72,128,3),dtype=np.uint8)
def feed(a,t,sc,cfg,validated=True):
    a.consume(FRAME,FRAME,t,int(t*10)+1,sc,decide(sc,cfg['policies'],model_validated=validated,auto_alerts=True))
def make_event(source,cfg):
    a=acc(source,cfg);feed(a,0,scores(gambling=.7),cfg);eid=a.active['id'];a.close();return eid

def test_letterbox_preserves_edges_and_training_parity():
    img=Image.new('RGB',(200,100),'white')
    pixels=np.asarray(prepare_image(img,100))
    assert (pixels[:25]==114).all() and (pixels[25:75]==255).all()
    expected=tensor_array(img,100)[0]
    actual=build_transforms(False,100)(img).numpy()
    np.testing.assert_allclose(actual,expected,atol=1e-6)
    assert 'RandomResizedCrop' not in str(build_transforms(True,100))
    assert 'HorizontalFlip' not in str(build_transforms(True,100))
def test_legacy_does_not_apply_letterbox():
    assert (np.asarray(prepare_image(Image.new('RGB',(200,100),'white'),100,LEGACY_PREPROCESSING))==255).all()
@pytest.mark.parametrize('points',[
    [[0,0],[1,1],[1,0],[0,1]],[[0,0],[0,1],[1,1],[1,0]],
    [[0,0],[2,0],[1,1],[0,1]],[[0,0],[0,0],[0,0],[0,0]],[[float('nan'),0]]*4])
def test_invalid_roi(points):
    with pytest.raises(ValueError):validate_points(points)
def test_crop_orientation():
    frame=np.zeros((101,201,3),np.uint8);frame[:,:100]=(255,0,0);frame[:,100:]=(0,0,255)
    crop=crop_screen(frame,[[0,0],[1,0],[1,1],[0,1]])
    assert crop.shape==(100,200,3)
    assert crop[50,10,0]==255 and crop[50,-10,2]==255
def test_unknown_and_candidate_cannot_alert(cfg):
    assert decide({},cfg['policies'])['action']=='unknown'
    assert decide(scores(gambling=float('nan')),cfg['policies'])['action']=='unknown'
    assert decide(scores(gambling=.99),cfg['policies'],auto_alerts=True)['action']=='review'
    assert decide(scores(gambling=.99),cfg['policies'],model_validated=True,auto_alerts=True)['action']=='alert'
    assert decide(scores(),cfg['policies'],quality_issue='blur')['action']=='unknown'
def test_quality_is_retained_as_unknown(cfg):
    assert assess_quality(np.zeros((100,100,3),np.uint8))
    assert not assess_quality(FRAME)
    assert decide(scores(),cfg['policies'],quality_issue=assess_quality(np.zeros((100,100,3),np.uint8)))['action']=='unknown'
def test_event_merge_context_and_confirmation(source,cfg):
    a=acc(source,cfg)
    feed(a,0,scores(),cfg)
    feed(a,1,scores(gambling=.96),cfg);eid=a.active['id']
    assert events.event_detail(eid)['kind']=='review'
    feed(a,2,scores(gambling=.99),cfg)
    feed(a,3,scores(gambling=.97),cfg)
    feed(a,4,scores(),cfg)
    a.close()
    e=events.event_detail(eid)
    assert e['kind']=='alert' and e['samples']==3 and e['lifecycle']=='closed'
    assert {v['role'] for v in e['evidence']}=={r+'_'+s for r in ('before','first','peak','last','after') for s in ('original','roi')}
    assert next(v for v in e['evidence'] if v['role']=='peak_roi')['media_time']==2
    for v in db.query('SELECT * FROM evidence WHERE event_id=?',(eid,)):
        assert hashlib.sha256(evidence.resolve(v['path']).read_bytes()).hexdigest()==v['sha256']
def test_different_high_labels_do_not_confirm(source,cfg):
    a=acc(source,cfg)
    feed(a,0,scores(gambling=.99,privacy_leak=.7),cfg)
    feed(a,1,scores(gambling=.7,privacy_leak=.99),cfg)
    assert events.event_detail(a.active['id'])['kind']=='review'
    feed(a,2,scores(gambling=.7,privacy_leak=.99),cfg)
    assert events.event_detail(a.active['id'])['kind']=='alert'
    a.close()
def test_short_event_and_cooldown(source,cfg):
    a=acc(source,cfg);feed(a,0,scores(gambling=.99),cfg);first=a.active['id']
    feed(a,1,scores(),cfg)
    a.close()
    assert events.event_detail(first)['kind']=='review'
    feed(a,2,scores(gambling=.99),cfg);feed(a,3,scores(gambling=.99),cfg);second=a.active['id']
    feed(a,4,scores(),cfg)
    a.close()
    feed(a,5,scores(gambling=.99),cfg);feed(a,6,scores(gambling=.99),cfg);third=a.active['id'];a.close()
    assert events.event_detail(second)['kind']=='alert' and events.event_detail(third)['kind']=='review'
def test_label_change_and_gap_split_events(source,cfg):
    a=acc(source,cfg);feed(a,0,scores(gambling=.7),cfg);e1=a.active['id']
    feed(a,1,scores(gambling=.7,privacy_leak=.7),cfg);e2=a.active['id']
    feed(a,20,scores(gambling=.7,privacy_leak=.7),cfg);e3=a.active['id'];a.close()
    assert e1==e2 and e3!=e2
def test_auth_csrf_and_private_files(client):
    remote=TestClient(app,client=('198.51.100.2',12345))
    assert remote.get('/api/bootstrap').status_code==403
    remote.close()
    unauth=TestClient(app)
    assert unauth.get('/api/overview').status_code==401
    assert unauth.get('/api/evidence/1').status_code==401
    assert unauth.get('/api/bootstrap',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert client.post('/api/video/1/stop',headers={'X-SG-CSRF':'wrong'}).status_code==403
    assert client.get('/api/overview',headers={'Host':'evil.example'}).status_code==403
    assert client.get('/api/overview',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/data/snapshots/anything.jpg').status_code==404
    unauth.close()
def test_review_undo_conflict_and_export(source,cfg,client):
    eid=make_event(source,cfg)
    body={'expected_version':0,'state':'confirmed','labels':['gambling','privacy_leak'],'note':'test','duration_ms':2500}
    result=client.post(f'/api/events/{eid}/review',json=body);assert result.status_code==200
    assert client.post(f'/api/events/{eid}/review',json=body).status_code==409
    rid=result.json()['review_id']
    result=client.post(f'/api/events/{eid}/undo',json={'expected_version':1,'review_id':rid});assert result.status_code==200
    e=events.event_detail(eid);assert e['review_state']=='pending' and e['review_version']==2
    assert e['reviews'][0]['operation']=='undo'
    body.update(expected_version=2,state='normal')
    assert client.post(f'/api/events/{eid}/review',json=body).status_code==200
    row=next(x for x in client.get('/api/review-export').json()['items'] if x['id']==eid)
    assert row['labels']==[] and row['status']=='candidate'
def test_roi_versions(source,client):
    data={'expected_version':0,'regions':[{'id':'screen-1','name':'one','points':[[0,0],[1,0],[1,1],[0,1]]}]}
    r=client.post(f'/api/sources/{source}/roi',json=data);assert r.status_code==200
    assert client.post(f'/api/sources/{source}/roi',json=data).status_code==409
    result=client.get(f'/api/sources/{source}/roi').json()
    assert len(result['history'])==1 and result['version']>0
def test_retention_protects_confirmed_and_open(source,cfg):
    pending=make_event(source,cfg);confirmed=make_event(source,cfg);open_event=make_event(source,cfg)
    events.review(confirmed,'confirmed',['gambling'],'keep',0,0)
    db.execute("UPDATE events SET updated_at=? WHERE id IN (?,?,?)",(time.time()-9*86400,pending,confirmed,open_event))
    db.execute("UPDATE events SET lifecycle='open' WHERE id=?",(open_event,))
    evidence.cleanup()
    assert events.event_detail(pending)['review_state']=='expired_unreviewed'
    assert not events.event_detail(confirmed)['expired'] and events.event_detail(confirmed)['evidence']
    assert not events.event_detail(open_event)['expired']
def test_quota_fails_without_removing_existing(source,cfg,monkeypatch):
    eid=make_event(source,cfg);count=len(events.event_detail(eid)['evidence'])
    monkeypatch.setattr(evidence,'storage_status',lambda:{'used_bytes':99,'quota_bytes':100,'free_bytes':10**12})
    with pytest.raises(OSError):evidence.attach(eid,'extra_roi',255-FRAME,0,1)
    assert len(events.event_detail(eid)['evidence'])==count
def test_settings_validation_conflict_and_comparison_gate(client):
    old=client.get('/api/settings').json();body=copy.deepcopy(old['settings'])
    body['review_time']='18:30'
    r=client.post('/api/settings',json={'expected_version':old['version'],'settings':body});assert r.status_code==200
    assert client.post('/api/settings',json={'expected_version':old['version'],'settings':body}).status_code==409
    v=r.json()['version'];body['policies']['gambling']['review']=.6
    assert client.post('/api/settings',json={'expected_version':v,'settings':body}).status_code==400
    body['policies']['gambling']['review']=.99
    assert client.post('/api/settings',json={'expected_version':v,'settings':body}).status_code==422
    settings.save(old['settings'],settings.current()['version'])
def test_comparison_freezes_truth_and_uses_paired_samples(source,cfg,client):
    eid=make_event(source,cfg);events.review(eid,'normal',[],'synthetic',0,2000)
    old=settings.current();candidate=copy.deepcopy(old['settings']);candidate['policies']['gambling']['review']=.61
    r=client.post('/api/comparisons',json={'candidate_model':'demo-rules','candidate_settings':candidate});assert r.status_code==200,r.text
    jid=r.json()['id']
    for _ in range(100):
        job=client.get('/api/comparisons/'+jid).json()
        if job['status'] in ('completed','error'):break
        time.sleep(.05)
    assert job['status']=='completed',job
    assert job['report']['paired_success']>0
    assert all('path' not in x for x in job['spec']['samples'])
    assert settings.current()['version']==old['version']
    assert client.post('/api/settings',json={'expected_version':old['version'],'settings':candidate}).status_code==200
    settings.save(old['settings'],settings.current()['version'])
def test_capacity_limit_and_stop_are_bounded(source):
    from threading import Event
    jm=JobManager();jm.stop_flags={i:Event() for i in range(100,104)}
    try:
        with pytest.raises(ValueError):jm.start(source)
        assert jm.start(100) is False
        assert jm.stop(100) and jm.stop_flags[100].is_set()
    finally:jm.shutdown()
def test_migration_idempotent(source):
    db.init_db();db.init_db()
    assert db.query_one('SELECT name FROM video_sources WHERE id=?',(source,))['name']=='test'

def test_calibration_refuses_insufficient_class_support():
    from calibrate_review_policy import select_policy
    y=np.array([[0]*5,[1]*5]);p=np.array([[.05]*5,[.99]*5])
    result=select_policy(y,p)
    assert not result['validation_report']['approved_for_alerts']
    assert all(not x['eligible'] for x in result['validation_report']['classes'].values())
    assert all(x['review']<x['alert'] for x in result['policies'].values())
def test_full_replay_metrics_match_once():
    sys.path.insert(0,str(PROJECT/'recognition_part/evaluation'))
    from replay_acceptance import match_events
    truth=[{'start':1,'end':4,'labels':['gambling']}]
    pred=[{'start':1,'end':4,'labels':['gambling']},{'start':1,'end':4,'labels':['gambling']},{'start':8,'end':9,'labels':['privacy_leak']}]
    assert match_events(pred,truth,'gambling')=={'tp':1,'fp':1,'fn':0,'precision':.5,'recall':1.}
    assert match_events(pred,truth,'privacy_leak')['fp']==1
def test_package_onnx_matches_training_and_rejects_tamper(tmp_path,monkeypatch):
    import torch,shutil
    from multilabel_common import load_checkpoint,build_model
    from export_model_package import main as export_package
    # Generate a deterministic checkpoint locally; historical delivery artifacts are optional.
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(7)
        reference=build_model('mobilenet_v3_small',len(LABELS),pretrained=False)
    weights=tmp_path/'reference.pt'
    torch.save({'model':reference.state_dict(),'labels':list(LABELS),'arch':'mobilenet_v3_small',
                'image_size':96,'preprocessing':PREPROCESSING},weights)
    package=tmp_path/'package'
    monkeypatch.setattr(sys,'argv',['export_model_package','--weights',str(weights),'--out',str(package),'--model-id','regression-model'])
    export_package()
    deployed=ModelPackage(package)
    model,*_=load_checkpoint(weights,'cpu')
    x=tensor_array(Image.fromarray(cv2.cvtColor(FRAME,cv2.COLOR_BGR2RGB)),96)
    with torch.no_grad():expected=torch.sigmoid(model(torch.from_numpy(x))).numpy()[0]
    actual=list(deployed.predict(FRAME).values())
    np.testing.assert_allclose(actual,expected,atol=1e-5)
    assert not deployed.validated
    shutil.copy2(package/'manifest.json',tmp_path/'manifest.json')
    (tmp_path/'model.onnx').write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='哈希'):ModelPackage(tmp_path)
def test_disconnected_stream_reconnect_and_stop(source):
    db.execute("UPDATE video_sources SET source_type='rtsp',source_value='rtsp://127.0.0.1:9/no-stream' WHERE id=?",(source,))
    jm=JobManager()
    try:
        jm.start(source);deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            state=db.query_one('SELECT status FROM video_sources WHERE id=?',(source,))['status']
            if state=='reconnecting':break
            time.sleep(.05)
        assert state=='reconnecting'
        started=time.monotonic();assert jm.stop(source)
        while source in jm.stop_flags and time.monotonic()-started<5:time.sleep(.05)
        assert source not in jm.stop_flags and db.query_one('SELECT status FROM video_sources WHERE id=?',(source,))['status']=='stopped'
    finally:jm.shutdown()


