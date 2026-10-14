"""Versioned connection metadata, Windows DPAPI secrets and bounded manual requests."""
import ctypes,hashlib,io,json,os,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,SecretStr,field_validator,model_validator
from PIL import Image,ImageOps,UnidentifiedImageError
from fastapi import HTTPException
from app.core import database as db
from app.services import ai_protocols as wire
from app.services.evidence import resolve

def configured_path(name):
    value=os.environ.get(name,'').strip()
    return Path(value) if value else None

LOCAL_MODEL=configured_path('SCREEN_GUARD_LOCAL_MODEL')
LOCAL_EXE=configured_path('SCREEN_GUARD_LLAMA_SERVER')
LOCAL_MMPROJ=configured_path('SCREEN_GUARD_MMPROJ')
SCHEMA="""
CREATE TABLE IF NOT EXISTS ai_connections(id TEXT PRIMARY KEY,version INTEGER NOT NULL,body TEXT NOT NULL,secret BLOB,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS ai_connection_history(id INTEGER PRIMARY KEY,connection_id TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(connection_id,version));
CREATE TABLE IF NOT EXISTS ai_requests(id TEXT PRIMARY KEY,request_id TEXT NOT NULL UNIQUE,fingerprint TEXT NOT NULL,connection_id TEXT NOT NULL,connection_version INTEGER NOT NULL,model TEXT NOT NULL,protocol TEXT NOT NULL,prompt_version TEXT NOT NULL,kind TEXT NOT NULL,event_id INTEGER,evidence_id INTEGER,input_sha256 TEXT NOT NULL,status TEXT NOT NULL,created_at REAL NOT NULL,day TEXT NOT NULL,finished_at REAL,result TEXT,error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ai_daily ON ai_requests(connection_id,day);
CREATE INDEX IF NOT EXISTS ai_event ON ai_requests(event_id,created_at);
"""
class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)

class Connection(Strict):
    name:str=Field(min_length=1,max_length=80)
    provider:Literal['local','openai','claude','deepseek','kimi','glm','compatible']
    protocol:Literal['responses','anthropic','chat']
    base_url:str=Field(min_length=1,max_length=500)
    model:str=Field(default='',max_length=200)
    enabled:bool=True
    vision:bool=False
    json_mode:bool=False
    timeout_seconds:int=Field(default=90,ge=10,le=180)
    max_output_tokens:int=Field(default=2048,ge=128,le=8192)
    daily_limit:int=Field(default=20,ge=1,le=10000)
    key_env:str=Field(default='',max_length=100,pattern=r'^([A-Za-z_][A-Za-z0-9_]*)?$')

    @field_validator('name','model')
    @classmethod
    def clean_text(cls,v):
        if any(ord(c)<32 for c in v):raise ValueError('不得包含控制字符')
        return v.strip()

    @model_validator(mode='after')
    def valid_url(self):
        try:
            u=urlsplit(self.base_url);port=u.port
            if u.username is not None or u.password is not None or u.query or u.fragment or not u.hostname:raise ValueError()
            if any(c.isspace() for c in self.base_url) or '\\' in self.base_url or '%' in u.netloc:raise ValueError()
            if self.provider=='local':
                if u.hostname not in ('127.0.0.1','localhost','::1') or u.scheme not in ('http','https'):raise ValueError()
            elif u.scheme!='https' or u.hostname in ('127.0.0.1','localhost','::1'):raise ValueError()
            if u.path.rstrip('/').endswith(('/chat/completions','/responses','/messages','/models')):raise ValueError()
        except ValueError:raise ValueError('填写 API 基础地址；本地仅限回环地址，云端须使用 HTTPS，不能附带密钥或完整方法路径') from None
        self.base_url=self.base_url.rstrip('/')
        if self.provider=='local' and self.protocol!='chat':raise ValueError('本地服务使用 Chat Completions 协议')
        return self

class SaveConnection(Strict):
    expected_version:int=Field(ge=0)
    config:Connection
    api_key:SecretStr|None=None
    clear_key:bool=False

    @model_validator(mode='after')
    def valid_key(self):
        if self.api_key is not None:
            key=self.api_key.get_secret_value()
            if not 1<=len(key)<=4096 or any(ord(c)<=32 or ord(c)>=127 for c in key):raise ValueError('API 密钥格式无效')
            if self.clear_key:raise ValueError('更新密钥与删除密钥不能同时选择')
        return self

class Submit(Strict):
    connection_id:str=Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    expected_version:int=Field(ge=1)
    request_id:str=Field(min_length=16,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    kind:Literal['probe','text','evidence']='probe'
    text:str=Field(default='',max_length=12000)
    evidence_id:int|None=Field(default=None,ge=1)
    allow_cloud:bool=False

class Blob(ctypes.Structure):
    _fields_=[('length',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]

def protect(data:bytes,decrypt=False):
    if os.name!='nt':raise HTTPException(400,'此平台请使用环境变量保存密钥；Windows 本机密钥不能直接迁移')
    buffer=ctypes.create_string_buffer(data);src=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    fn=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint32,ctypes.POINTER(Blob)]
    fn.restype=ctypes.c_int
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not fn(ctypes.byref(src),None,None,None,None,1,ctypes.byref(out)):
        raise HTTPException(400,'无法读取或保存本机密钥；请使用当前 Windows 用户重新填写')
    try:return ctypes.string_at(out.data,out.length)
    finally:kernel.LocalFree(ctypes.cast(out.data,ctypes.c_void_p))

def init():
    # Use execute statements within our shared DB transaction instead of executescript's implicit commit.
    with db.transaction() as c:
        for statement in SCHEMA.split(';'):
            if statement.strip():c.execute(statement)
        if not c.execute('SELECT 1 FROM ai_connections LIMIT 1').fetchone():
            cfg=Connection(name='本地 Qwen',provider='local',protocol='chat',base_url='http://127.0.0.1:8088/v1',model='qwen-local',daily_limit=200,json_mode=True).model_dump()
            body=json.dumps(cfg,ensure_ascii=False);now=time.time()
            c.execute('INSERT INTO ai_connections VALUES(?,?,?,?,?,?)',('local-qwen',1,body,None,now,now))
            c.execute('INSERT INTO ai_connection_history(connection_id,version,body,created_at) VALUES(?,?,?,?)',('local-qwen',1,body,now))

def today():return datetime.now(timezone.utc).strftime('%Y-%m-%d')

def public(row):
    cfg=json.loads(row['body'])
    n=db.query_one('SELECT COUNT(*) n FROM ai_requests WHERE connection_id=? AND day=?',(row['id'],today()))['n']
    return {'id':row['id'],'version':row['version'],'config':cfg,'has_saved_key':bool(row['secret']),
            'has_env_key':bool(cfg['key_env'] and os.environ.get(cfg['key_env'])),'used_today':n,
            'updated_at':row['updated_at']}

def connections():return [public(r) for r in db.query('SELECT * FROM ai_connections ORDER BY created_at,id')]

def get_row(id):
    row=db.query_one('SELECT * FROM ai_connections WHERE id=?',(id,))
    if not row:raise HTTPException(404,'连接配置不存在')
    return row

def save(id,data:SaveConnection):
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',id):raise HTTPException(422,'连接 ID 无效')
    cfg=data.config.model_dump();body=json.dumps(cfg,ensure_ascii=False);now=time.time()
    encrypted=protect(data.api_key.get_secret_value().encode()) if data.api_key else None
    with db.transaction() as c:
        old=c.execute('SELECT * FROM ai_connections WHERE id=?',(id,)).fetchone()
        if (old['version'] if old else 0)!=data.expected_version:raise HTTPException(409,'配置已被修改，请重新加载后保存')
        if not old and c.execute('SELECT COUNT(*) FROM ai_connections').fetchone()[0]>=20:raise HTTPException(409,'最多保存 20 个连接，请编辑现有连接')
        # Changing destinations must never silently forward an old secret or environment key.
        if old:
            prev=json.loads(old['body'])
            changed=any(prev[k]!=cfg[k] for k in ('base_url','provider','protocol','key_env'))
            if changed and (old['secret'] or prev['key_env']) and not(data.api_key or data.clear_key):
                raise HTTPException(409,'地址、协议或密钥来源变化：请重新填写密钥，或勾选删除保存的密钥后确认新环境变量')
        secret=encrypted if encrypted is not None else None if data.clear_key else old['secret'] if old else None
        version=data.expected_version+1
        c.execute('INSERT INTO ai_connections VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,body=excluded.body,secret=excluded.secret,updated_at=excluded.updated_at',
                  (id,version,body,secret,old['created_at'] if old else now,now))
        c.execute('INSERT INTO ai_connection_history(connection_id,version,body,created_at) VALUES(?,?,?,?)',(id,version,body,now))
    return public(get_row(id))

def key_for(row):
    cfg=json.loads(row['body'])
    # A directly saved key takes precedence; an empty environment variable cannot erase it.
    if row['secret']:return protect(row['secret'],True).decode()
    key=os.environ.get(cfg['key_env'],'') if cfg['key_env'] else ''
    if not key and cfg['provider']!='local':raise HTTPException(400,'此连接未配置 API 密钥或对应环境变量')
    if key and (len(key)>4096 or any(ord(c)<=32 or ord(c)>=127 for c in key)):raise HTTPException(400,'环境变量中的 API 密钥格式无效')
    return key

def checked(id,version):
    row=get_row(id);cfg=json.loads(row['body'])
    if row['version']!=version:raise HTTPException(409,'连接版本已变化，请重新加载')
    if not cfg['enabled']:raise HTTPException(400,'连接已停用')
    return row,cfg

def models(id,version):
    row,cfg=checked(id,version)
    try:return {'models':wire.list_models(cfg,key_for(row)),'vision_verified':False}
    except wire.ProviderError as exc:raise HTTPException(502,str(exc)) from None

def image_bytes(data):
    try:return wire.prepare_image(data)
    except wire.ImageInputError as exc:raise HTTPException(exc.status,str(exc)) from None

executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='ai-request')
slots=threading.BoundedSemaphore(6)
stopping=threading.Event()

def get_request(id):
    row=db.query_one('SELECT * FROM ai_requests WHERE id=?',(id,))
    if not row:raise HTTPException(404,'调用记录不存在')
    row.pop('fingerprint');row['result']=json.loads(row['result']) if row['result'] else None
    return row

def run(id,cfg,key,text,image,probe,guard=None):
    try:
        if stopping.is_set():raise wire.ProviderError('服务正在停止；此任务未发送')
        if guard:
            reason=guard()
            if reason:raise wire.ProviderError(reason)
        db.execute("UPDATE ai_requests SET status='running' WHERE id=?",(id,))
        result=wire.invoke(cfg,key,text,image,probe)
        db.execute("UPDATE ai_requests SET status='completed',result=?,finished_at=? WHERE id=?",(json.dumps(result,ensure_ascii=False),time.time(),id))
    except wire.ProviderError as exc:
        db.execute("UPDATE ai_requests SET status='error',error=?,finished_at=? WHERE id=?",(str(exc),time.time(),id))
    except Exception:
        db.execute("UPDATE ai_requests SET status='error',error='内部调用失败；结果未知，请人工复核',finished_at=? WHERE id=?",(time.time(),id))
    finally:slots.release()

def submit(data:Submit,upload=None,guard=None):
    if stopping.is_set():raise HTTPException(503,'服务正在停止，暂不接受模型任务')
    row,cfg=checked(data.connection_id,data.expected_version)
    if not cfg['model']:raise HTTPException(400,'请先填写模型 ID 并保存')
    if cfg['provider']!='local' and not data.allow_cloud:raise HTTPException(400,'请确认本次输入可以发送至所选云端地址')
    image=None;event_id=None;sha=''
    if upload is not None:
        if data.kind!='evidence' or data.evidence_id is not None:raise HTTPException(422,'图片上传参数无效')
        sha=hashlib.sha256(upload).hexdigest();image=image_bytes(upload)
    elif data.kind=='evidence':
        asset=db.query_one('SELECT e.*,v.expired FROM evidence e JOIN events v ON v.id=e.event_id WHERE e.id=?',(data.evidence_id,))
        if not asset or asset['expired']:raise HTTPException(410,'证据不存在或已过期')
        try:
            path=resolve(asset['path'])
            if path.stat().st_size>8*1024*1024:raise HTTPException(413,'证据图片超过限制')
            raw=path.read_bytes()
        except (OSError,ValueError):raise HTTPException(410,'证据文件不可用') from None
        sha=hashlib.sha256(raw).hexdigest()
        if sha!=asset['sha256']:raise HTTPException(409,'证据校验失败，不能发送')
        image=image_bytes(raw);event_id=asset['event_id']
    elif data.evidence_id is not None:raise HTTPException(422,'文本请求不能附带证据 ID')
    if image and not cfg['vision']:raise HTTPException(400,'此连接尚未启用图片能力；请先启动视觉服务并在设置中启用图片输入')
    if data.kind=='text' and not data.text.strip():raise HTTPException(422,'请输入待审核文本')
    text='连通性测试' if data.kind=='probe' else data.text
    sha=hashlib.sha256((sha+'\n'+text).encode()).hexdigest()
    fingerprint=hashlib.sha256(json.dumps({'connection':data.connection_id,'version':data.expected_version,'kind':data.kind,'input':sha,'evidence':data.evidence_id},sort_keys=True).encode()).hexdigest()
    prior=db.query_one('SELECT id,fingerprint FROM ai_requests WHERE request_id=?',(data.request_id,))
    if prior:
        if prior['fingerprint']!=fingerprint:raise HTTPException(409,'请求标识已用于其他内容')
        return get_request(prior['id'])
    key=key_for(row)
    if not slots.acquire(blocking=False):raise HTTPException(429,'模型任务已满，请等待现有任务完成')
    id=uuid.uuid4().hex
    try:
        with db.transaction() as c:
            # Recheck idempotency under the write lock before reserving the UTC daily allowance.
            prior=c.execute('SELECT id,fingerprint FROM ai_requests WHERE request_id=?',(data.request_id,)).fetchone()
            if prior:
                if prior['fingerprint']!=fingerprint:raise HTTPException(409,'请求标识已用于其他内容')
                slots.release();return get_request(prior['id'])
            if c.execute('SELECT COUNT(*) FROM ai_requests WHERE connection_id=? AND day=?',(data.connection_id,today())).fetchone()[0]>=cfg['daily_limit']:
                raise HTTPException(429,'此连接已达到每日调用上限（UTC 零点重置，失败调用也计入）')
            c.execute('INSERT INTO ai_requests(id,request_id,fingerprint,connection_id,connection_version,model,protocol,prompt_version,kind,event_id,evidence_id,input_sha256,status,created_at,day) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (id,data.request_id,fingerprint,data.connection_id,data.expected_version,cfg['model'],cfg['protocol'],wire.PROMPT_VERSION,data.kind,event_id,data.evidence_id,sha,'queued',time.time(),today()))
        future=executor.submit(run,id,cfg,key,text,image,data.kind=='probe',guard)
        def cancelled(f):
            if f.cancelled():
                db.execute("UPDATE ai_requests SET status='error',error='服务停止，排队任务未发送',finished_at=? WHERE id=?",(time.time(),id))
                slots.release()
        future.add_done_callback(cancelled)
    except Exception:
        slots.release()
        db.execute("UPDATE ai_requests SET status='error',error='任务未能启动',finished_at=? WHERE id=? AND status='queued'",(time.time(),id))
        raise
    return get_request(id)

def restart_cleanup():
    db.execute("UPDATE ai_requests SET status='error',error='服务重启中断；可能已计费，未自动重试',finished_at=? WHERE status IN ('queued','running')",(time.time(),))

def shutdown():
    stopping.set()
    executor.shutdown(wait=False,cancel_futures=True)

def local_info():
    has_model=LOCAL_MODEL is not None and LOCAL_MODEL.is_file()
    has_projector=LOCAL_MMPROJ is not None and LOCAL_MMPROJ.is_file()
    return {'model_path':str(LOCAL_MODEL) if LOCAL_MODEL else '', 'model_exists':has_model,'model_bytes':LOCAL_MODEL.stat().st_size if has_model else None,
            'server_path':str(LOCAL_EXE) if LOCAL_EXE else '', 'server_exists':LOCAL_EXE is not None and LOCAL_EXE.is_file(),'vision_ready':None,
            'mmproj_path':str(LOCAL_MMPROJ) if LOCAL_MMPROJ else '', 'mmproj_exists':has_projector,
            'message':('已找到 mmproj。使用 start_local_model_vision.bat 加载图片模式；当前服务是否支持图片需通过实际调用确认。' if has_projector else '未找到 mmproj；请补齐匹配文件，再启动图片模式并测试。'),
            'secret_storage':'Windows DPAPI（当前用户）' if os.name=='nt' else '环境变量',
            'daily_reset':'UTC 00:00','max_parallel':2,'max_inflight':6}

