"""Interface regression suite: isolated DB; no paid API calls or model downloads."""
import os,sys,time,json,uuid,hashlib,io,concurrent.futures
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
PROJECT=ROOT/'src/task2_video_panel'
DATA=ROOT/'artifacts/tests'/'test_model_interfaces'/uuid.uuid4().hex[:12]
DATA.mkdir(parents=True)
for key in ('TEMP','TMP','TMPDIR','XDG_CACHE_HOME'):os.environ[key]=str(DATA)
os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(DATA),SCREEN_GUARD_TESTING='1',PYTHONDONTWRITEBYTECODE='1')
sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part')]
import pytest,httpx
from PIL import Image
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.core import database as db
from app.services import ai_connections as ai,ai_protocols as wire
from screen_guard_core import LABELS
KEY='test-secret-never-render-324578'
SCORES={k:0.1 for k in LABELS}
VALID={'scores':SCORES,'needs_review':True,'evidence':['需要结合上下文人工复核']}
@pytest.fixture
def client():
    c=TestClient(app)
    token=c.get('/api/bootstrap').json()['csrf'];c.headers['X-SG-CSRF']=token
    yield c
    c.close()

def config(**kw):
    values=dict(name='Test',provider='local',protocol='chat',base_url='http://127.0.0.1:8088/v1',model='qwen-local',daily_limit=200)
    values.update(kw);return ai.Connection(**values)

def connection(**kw):
    id='test-'+uuid.uuid4().hex
    return ai.save(id,ai.SaveConnection(expected_version=0,config=config(**kw)))

def request(c,**kw):
    values=dict(connection_id=c['id'],expected_version=c['version'],request_id=uuid.uuid4().hex,kind='text',text='测试样本')
    values.update(kw);return ai.Submit(**values)

def wait(job):
    end=time.time()+5
    while job['status'] in ('queued','running') and time.time()<end:
        time.sleep(.01);job=ai.get_request(job['id'])
    assert job['status'] in ('completed','error')
    return job

def png():
    out=io.BytesIO();Image.new('RGB',(48,24),'blue').save(out,format='PNG');return out.getvalue()

@pytest.mark.parametrize('protocol',['responses','anthropic','chat'])
def test_protocol_text_and_image(protocol,monkeypatch):
    cfg=config(protocol=protocol,provider='compatible',base_url='https://example.test/v1').model_dump()
    raw=json.dumps(VALID)
    response={'responses':{'status':'completed','output':[{'content':[{'type':'output_text','text':raw}]}]},
              'anthropic':{'stop_reason':'end_turn','content':[{'type':'text','text':raw}]},
              'chat':{'choices':[{'finish_reason':'stop','message':{'content':raw}}]}}[protocol]
    seen=[]
    def remote(p,k,method,path,body):
        seen.append((method,path,body,wire.headers(p,k)));return {**response,'usage':{'input_tokens':7,'echo':KEY}}
    monkeypatch.setattr(wire,'request_json',remote)
    for image in (None,b'jpeg-bytes'):
        result=wire.invoke(cfg,KEY,'quoted untrusted input',image)
        assert result['scores']==SCORES and result['advisory_only'] is True and result['usage']=={'input_tokens':7}
        method,path,body,headers=seen[-1]
        assert body['model']=='qwen-local' and KEY not in json.dumps(body)
        if protocol=='responses':
            assert path=='/responses' and body['store'] is False
            assert ('input_image' in json.dumps(body))==bool(image)
        elif protocol=='anthropic':
            assert path=='/messages' and headers['x-api-key']==KEY and headers['anthropic-version']=='2023-06-01'
            assert ('base64' in json.dumps(body))==bool(image)
        else:
            assert path=='/chat/completions' and headers['Authorization']=='Bearer '+KEY
            assert ('image_url' in json.dumps(body))==bool(image)

@pytest.mark.parametrize('scores',[
    {'sexual_vulgar':.1},
    {**SCORES,'gambling':float('nan')},
    {**SCORES,'gambling':float('inf')},
    {**SCORES,'gambling':True},
    {**SCORES,'gambling':1.5},
    {**SCORES,'gambling':-.01},
    {**SCORES,'gambling':'0.1'},
    {**SCORES,'unknown':0},
])
def test_bad_scores_are_unknown(scores):
    with pytest.raises(wire.ProviderError):wire.parse_result(json.dumps({**VALID,'scores':scores}))

@pytest.mark.parametrize('raw',['{}','{"ready":false}', 'ordinary non JSON','{"scores":{},"scores":{}}'])
def test_incomplete_schema(raw):
    with pytest.raises(wire.ProviderError):wire.parse_result(raw)

def test_fenced_json_and_probe():
    assert wire.parse_result('```json\n'+json.dumps(VALID)+'\n```')['scores']==SCORES
    assert wire.parse_result('{"ready":true}',probe=True)['ready']

@pytest.mark.parametrize('probe',[False,True])
def test_local_schema_matches_request_kind(probe):
    _,body=wire.payload(config(json_mode=True).model_dump(),'test',probe=probe)
    schema=body['response_format']['schema']
    assert body['temperature']==0 and schema['additionalProperties'] is False
    if probe:
        assert schema['required']==['ready'] and schema['properties']['ready']['const'] is True
    else:
        assert set(schema['required'])=={'scores','needs_review','evidence'}
        scores=schema['properties']['scores']
        assert set(scores['required'])==set(LABELS) and scores['additionalProperties'] is False
        assert all(v=={'type':'number','minimum':0,'maximum':1} for v in scores['properties'].values())
        assert schema['properties']['evidence']['minItems']==1
        assert schema['properties']['evidence']['maxItems']==3

@pytest.mark.parametrize('provider,json_mode',[('compatible',True),('local',False)])
def test_local_schema_does_not_change_other_modes(provider,json_mode):
    base_url='http://127.0.0.1:8088/v1' if provider=='local' else 'https://example.test/v1'
    _,body=wire.payload(config(provider=provider,json_mode=json_mode,base_url=base_url).model_dump(),'test')
    assert 'schema' not in body.get('response_format',{}) and 'temperature' not in body
    assert ('response_format' in body)==json_mode

@pytest.mark.parametrize('protocol,response',[
 ('chat',{'choices':[{'finish_reason':'length','message':{'content':json.dumps(VALID)}}]}),
 ('chat',{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(VALID),'refusal':'refused'}}]}),
 ('anthropic',{'stop_reason':'max_tokens','content':[{'type':'text','text':json.dumps(VALID)}]}),
 ('responses',{'status':'incomplete','output':[]}),
 ('responses',{'status':'completed','output':[{'content':[{'type':'refusal','refusal':'no'}]}]}),
])
def test_incomplete_or_refusal(protocol,response):
    with pytest.raises(wire.ProviderError):wire.extract({'protocol':protocol},response)

@pytest.mark.parametrize('status',[302,401,403,429,500])
def test_transport_no_redirect_no_retry_no_error_echo(status,monkeypatch):
    original=httpx.Client;calls=[]
    def handler(req):
        calls.append(req);return httpx.Response(status,headers={'Location':'https://evil.invalid'},json={'error':KEY})
    def client_factory(**kw):
        assert kw['follow_redirects'] is False and kw['trust_env'] is False
        return original(**kw,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(wire.httpx,'Client',client_factory)
    with pytest.raises(wire.ProviderError) as caught:
        wire.request_json(config().model_dump(),KEY,'POST','/chat/completions',{})
    assert len(calls)==1 and KEY not in str(caught.value)

def test_transport_timeout_and_response_cap(monkeypatch):
    original=httpx.Client
    def timeout(req):raise httpx.ReadTimeout(KEY,request=req)
    monkeypatch.setattr(wire.httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(timeout)))
    with pytest.raises(wire.ProviderError) as caught:wire.request_json(config().model_dump(),KEY,'POST','/chat/completions',{})
    assert KEY not in str(caught.value) and '未自动重试' in str(caught.value)
    monkeypatch.setattr(wire.httpx,'Client',lambda **kw:original(**kw,transport=httpx.MockTransport(lambda req:httpx.Response(200,content=b'x'*(2*1024*1024+1)))))
    with pytest.raises(wire.ProviderError,match='2 MiB'):wire.request_json(config().model_dump(),'','GET','/models')

@pytest.mark.parametrize('values',[
 {'base_url':'http://example.com/v1'},
 {'base_url':'http://localhost:8088/v1?api_key=abc'},
 {'base_url':'http://user:pass@localhost:8088/v1'},
 {'base_url':'http://127.0.0.1:8088/v1/chat/completions'},
 {'base_url':'http://127.0.0.1:bad'},
 {'provider':'openai','base_url':'http://api.openai.com/v1'},
 {'provider':'openai','base_url':'https://api.openai.com/v1#secret'},
])
def test_url_validation(values):
    with pytest.raises(ValidationError):config(**values)

def test_secret_roundtrip_and_redaction(client):
    c=connection();body={'expected_version':1,'config':c['config'],'api_key':KEY}
    response=client.put('/api/ai/connections/'+c['id'],json=body)
    assert response.status_code==200 and response.json()['has_saved_key']
    assert KEY not in response.text
    row=ai.get_row(c['id']);assert KEY.encode() not in row['secret'] and ai.key_for(row)==KEY
    assert KEY not in client.get('/api/ai/connections').text
    assert KEY not in client.get('/api/ai/connections/'+c['id']+'/history').text
    body['expected_version']=2;body['config']['timeout_seconds']=0
    bad=client.put('/api/ai/connections/'+c['id'],json=body)
    assert bad.status_code==422 and KEY not in bad.text
    body['config']['timeout_seconds']=90;body['config']['base_url']='http://127.0.0.1:8089/v1';body.pop('api_key')
    assert client.put('/api/ai/connections/'+c['id'],json=body).status_code==409
    body['clear_key']=True
    assert client.put('/api/ai/connections/'+c['id'],json=body).status_code==200
    assert not ai.get_row(c['id'])['secret']

def test_config_version_and_env_key(monkeypatch):
    c=connection()
    with pytest.raises(HTTPException) as caught:ai.save(c['id'],ai.SaveConnection(expected_version=0,config=config()))
    assert caught.value.status_code==409
    c=connection(key_env='SCREEN_GUARD_TEST_KEY');monkeypatch.setenv('SCREEN_GUARD_TEST_KEY',KEY)
    assert ai.key_for(ai.get_row(c['id']))==KEY
    assert ai.public(ai.get_row(c['id']))['has_env_key']

def test_capability_and_cloud_consent(monkeypatch):
    c=connection()
    with pytest.raises(HTTPException,match='400'):ai.submit(request(c,kind='evidence'),png())
    c=connection(provider='openai',protocol='responses',base_url='https://example.test/v1')
    with pytest.raises(HTTPException) as caught:ai.submit(request(c))
    assert caught.value.status_code==400 and '本次输入' in caught.value.detail
    with pytest.raises(HTTPException) as caught:ai.submit(request(c,allow_cloud=True))
    assert '密钥' in caught.value.detail

def test_unconfigured_local_assets_are_not_reported_as_present(monkeypatch):
    for key in ('LOCAL_MODEL', 'LOCAL_EXE', 'LOCAL_MMPROJ'):
        monkeypatch.setattr(ai, key, None)
    info=ai.local_info()
    assert info['model_path']==info['server_path']==info['mmproj_path']==''
    assert not any(info[key] for key in ('model_exists','server_exists','mmproj_exists'))
    assert info['model_bytes'] is None


def test_idempotency_concurrent_and_daily_limit(monkeypatch):
    c=connection(daily_limit=1);calls=[]
    def invoke(*args,**kw):calls.append(1);return VALID
    monkeypatch.setattr(wire,'invoke',invoke);data=request(c)
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        jobs=list(pool.map(lambda _:ai.submit(data),range(4)))
    assert len({j['id'] for j in jobs})==1
    assert wait(jobs[0])['status']=='completed' and len(calls)==1
    with pytest.raises(HTTPException) as caught:ai.submit(request(c))
    assert caught.value.status_code==429
    with pytest.raises(HTTPException) as caught:ai.submit(data.model_copy(update={'text':'other'}))
    assert caught.value.status_code==409
    c=ai.save(c['id'],ai.SaveConnection(expected_version=1,config=config(daily_limit=1)))
    with pytest.raises(HTTPException) as caught:ai.submit(request(c))
    assert caught.value.status_code==429

def test_failed_job_consumes_allowance_and_never_zero_scores(monkeypatch):
    c=connection(daily_limit=1)
    def fail(*args,**kw):raise wire.ProviderError('模型结果不完整')
    monkeypatch.setattr(wire,'invoke',fail)
    job=wait(ai.submit(request(c)))
    assert job['status']=='error' and job['result'] is None
    with pytest.raises(HTTPException) as caught:ai.submit(request(c))
    assert caught.value.status_code==429

def test_picture_normalization_and_invalid_upload(client,monkeypatch):
    c=connection(vision=True);seen=[]
    def invoke(cfg,key,text,image,probe):
        seen.append(image);return VALID
    monkeypatch.setattr(wire,'invoke',invoke)
    metadata=request(c,kind='evidence').model_dump()
    response=client.post('/api/ai/requests/image',data={'metadata':json.dumps(metadata)},files={'file':('test.png',png(),'image/png')})
    assert response.status_code==202 and wait(response.json())['status']=='completed'
    assert seen[0].startswith(b'\xff\xd8')
    response=client.post('/api/ai/requests/image',data={'metadata':json.dumps(metadata)},files={'file':('test.png',b'not an image','image/png')})
    assert response.status_code==400

def test_auth_csrf_and_catalog(client):
    anonymous=TestClient(app)
    assert anonymous.get('/api/ai/connections').status_code==401
    assert client.post('/api/ai/requests',headers={'X-SG-CSRF':''},json={}).status_code==403
    data=client.get('/api/ai/providers').json()
    assert {p['id'] for p in data['providers']}=={'local','openai','claude','deepseek','kimi','glm','compatible'}
    assert data['local']['vision_ready'] is None  # File discovery does not prove live model capability.
    anonymous.close()

def test_event_advice_does_not_change_human_result(monkeypatch):
    c=connection(vision=True)
    event=db.execute("INSERT INTO events(source_id,job_id,roi_id,roi_version,settings_version,model_id,kind,labels,scores,reason,started_at,updated_at,media_start,media_end,review_state,review_note) VALUES(1,'j','s',0,1,'demo','review','[]','{}','test',1,1,0,1,'normal','human note')")
    from app.services.evidence import DIRECTORY
    raw=png();path=DIRECTORY/(uuid.uuid4().hex+'.jpg');path.write_bytes(raw)
    asset=db.execute('INSERT INTO evidence(event_id,role,path,sha256,captured_at,media_time,frame_no,bytes) VALUES(?,?,?,?,1,1,1,?)',(event,'first_roi',path.relative_to(DATA).as_posix(),hashlib.sha256(raw).hexdigest(),len(raw)))
    monkeypatch.setattr(wire,'invoke',lambda *a,**kw:VALID)
    before=db.query_one('SELECT * FROM events WHERE id=?',(event,))
    job=wait(ai.submit(request(c,kind='evidence',evidence_id=asset)))
    assert job['event_id']==event and job['evidence_id']==asset
    assert db.query_one('SELECT * FROM events WHERE id=?',(event,))==before
    path.write_bytes(b'tampered')
    with pytest.raises(HTTPException) as caught:ai.submit(request(c,kind='evidence',evidence_id=asset))
    assert caught.value.status_code==409

def test_restart_marks_unknown():
    c=connection()
    db.execute("INSERT INTO ai_requests(id,request_id,fingerprint,connection_id,connection_version,model,protocol,prompt_version,kind,input_sha256,status,created_at,day) VALUES('restart','restart-request-id','hash',?,1,'m','chat','v','text','h','running',1,?)",(c['id'],ai.today()))
    ai.restart_cleanup()
    job=ai.get_request('restart')
    assert job['status']=='error' and job['result'] is None and '未自动重试' in job['error']

@pytest.mark.parametrize('protocol',['responses','anthropic','chat'])
def test_full_probe_adapter(protocol,monkeypatch):
    cfg=config(protocol=protocol,provider='compatible',base_url='https://example.test/v1').model_dump()
    def remote(profile,key,method,path,body):
        assert 'ready' in json.dumps(body) and 'scores' not in json.dumps(body)
        if protocol=='responses':return {'status':'completed','output':[{'content':[{'type':'output_text','text':'{"ready":true}'}]}]}
        if protocol=='anthropic':return {'stop_reason':'end_turn','content':[{'type':'text','text':'{"ready":true}'}]}
        return {'choices':[{'finish_reason':'stop','message':{'content':'{"ready":true}'}}]}
    monkeypatch.setattr(wire,'request_json',remote)
    assert wire.invoke(cfg,'','test',None,True)['ready'] is True

def test_bounded_queue_and_shutdown_do_not_send(monkeypatch):
    import threading
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=1)
    gate=threading.Event()
    occupied=pool.submit(gate.wait)
    monkeypatch.setattr(ai,'executor',pool)
    monkeypatch.setattr(ai,'stopping',threading.Event())
    monkeypatch.setattr(ai,'slots',threading.BoundedSemaphore(6))
    calls=[]
    monkeypatch.setattr(wire,'invoke',lambda *a,**kw:calls.append(1))
    c=connection()
    try:
        jobs=[ai.submit(request(c)) for _ in range(6)]
        with pytest.raises(HTTPException) as caught:ai.submit(request(c))
        assert caught.value.status_code==429
        ai.shutdown()
        assert not calls
        assert all(ai.get_request(j['id'])['status']=='error' for j in jobs)
        assert all('未发送' in ai.get_request(j['id'])['error'] for j in jobs)
        with pytest.raises(HTTPException) as caught:ai.submit(request(c))
        assert caught.value.status_code==503
    finally:
        gate.set();occupied.result(timeout=2);pool.shutdown(wait=True,cancel_futures=True)

def test_key_validation_error_never_echoes_secret(client):
    c=connection()
    for key in (KEY+' invalid',KEY*200):
        response=client.put('/api/ai/connections/'+c['id'],json={'expected_version':1,'config':c['config'],'api_key':key})
        assert response.status_code==422 and KEY not in response.text


