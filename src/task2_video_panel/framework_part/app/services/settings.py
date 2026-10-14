import json,time,re
from datetime import datetime,timedelta,timezone
from pydantic import BaseModel,Field,ConfigDict,model_validator
from app.core import database as db
from screen_guard_core import LABELS
class LabelPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    enabled:bool=True
    review:float=Field(.55,ge=0,lt=1)
    alert:float=Field(.9,gt=0,le=1)
    @model_validator(mode='after')
    def order(self):
        if self.review>=self.alert:raise ValueError('复核阈值必须小于告警阈值')
        return self
class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    review_time:str='09:00'
    timezone:str='Asia/Shanghai'
    retention_days:int=Field(7,ge=1,le=365)
    quota_gib:float=Field(20,ge=.1,le=1000)
    min_free_gib:float=Field(5,ge=0,le=1000)
    sample_seconds:float=Field(1,ge=.1,le=60)
    merge_gap_seconds:float=Field(8,ge=.5,le=120)
    confirm_samples:int=Field(2,ge=1,le=20)
    confirm_seconds:float=Field(1,ge=0,le=30)
    cooldown_seconds:float=Field(60,ge=0,le=3600)
    evidence_interval_seconds:float=Field(2,ge=.1,le=60)
    context_frames:int=Field(8,ge=0,le=24)
    segment_seconds:float=Field(60,ge=5,le=300)
    audit_daily_per_source:int=Field(24,ge=0,le=1000)
    review_minutes:int=Field(60,ge=1,le=480)
    review_seconds_per_item:int=Field(15,ge=1,le=300)
    auto_restore_sources:bool=True
    quiet_hours:bool=False
    quiet_start:str='22:00'
    quiet_end:str='08:00'
    audit_sample_rate:float=Field(.02,ge=0,le=.5)
    auto_alerts:bool=False
    model_id:str='demo-rules'
    policies:dict[str,LabelPolicy]=Field(default_factory=lambda:{k:LabelPolicy() for k in LABELS})
    @model_validator(mode='after')
    def validate_all(self):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',self.review_time):raise ValueError('复核时间需为 HH:MM')
        if any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',t) for t in (self.quiet_start,self.quiet_end)):raise ValueError('静默时间需为 HH:MM')
        if self.timezone not in ('Asia/Shanghai','UTC'):raise ValueError('支持 Asia/Shanghai 或 UTC')
        if set(self.policies)!=set(LABELS):raise ValueError('必须配置全部五类标签')
        if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',self.model_id):raise ValueError('模型 ID 无效')
        return self
def current():
    row=db.query_one('SELECT * FROM settings_history ORDER BY version DESC LIMIT 1')
    if not row:
        with db.transaction() as c:
            if not c.execute('SELECT version FROM settings_history LIMIT 1').fetchone():
                c.execute('INSERT INTO settings_history(body,created_at,actor) VALUES(?,?,?)',(Settings().model_dump_json(),time.time(),'初始化'))
        return current()
    body=json.loads(row['body'])
    parsed=Settings.model_validate(body).model_dump()
    if parsed!=body:
        # Preserve the old version and make new default policy explicit in history.
        with db.transaction() as c:
            latest=c.execute('SELECT version FROM settings_history ORDER BY version DESC LIMIT 1').fetchone()
            if latest['version']==row['version']:
                c.execute('INSERT INTO settings_history(body,created_at,actor) VALUES(?,?,?)',(json.dumps(parsed,ensure_ascii=False),time.time(),'配置结构升级：抽检按每日预算'))
        return current()
    return {'version':row['version'],'settings':parsed,'created_at':row['created_at']}
def save(body,expected_version,actor='本机用户'):
    parsed=Settings.model_validate(body).model_dump()
    with db.transaction() as c:
        row=c.execute('SELECT version FROM settings_history ORDER BY version DESC LIMIT 1').fetchone()
        if row['version']!=expected_version:raise RuntimeError('配置已被修改，请重新加载')
        version=c.execute('INSERT INTO settings_history(body,created_at,actor) VALUES(?,?,?)',(json.dumps(parsed,ensure_ascii=False),time.time(),actor)).lastrowid
    return {'version':version,'settings':parsed,'effective':'采样、模型与事件策略在任务下次启动时生效；复核时间及留存设置立即生效'}
def batch_info():
    cfg=current()['settings'];tz=timezone(timedelta(hours=8)) if cfg['timezone']=='Asia/Shanghai' else timezone.utc
    now=datetime.now(tz);h,m=map(int,cfg['review_time'].split(':'))
    cutoff=now.replace(hour=h,minute=m,second=0,microsecond=0)
    if now<cutoff:cutoff-=timedelta(days=1)
    db.execute('INSERT OR IGNORE INTO review_batches(day,cutoff,created_at) VALUES(?,?,?)',(cutoff.date().isoformat(),cutoff.timestamp(),time.time()))
    return {'day':cutoff.date().isoformat(),'cutoff':cutoff.timestamp(),'next_at':(cutoff+timedelta(days=1)).timestamp(),'timezone':cfg['timezone']}


def require_comparison(new):
    for run in db.query("SELECT spec,report FROM comparisons WHERE status='completed' ORDER BY created_at DESC"):
        spec=json.loads(run['spec']);report=json.loads(run['report'])
        if spec['candidate_model']==new['model_id'] and spec['candidate_settings']['policies']==new['policies'] and report.get('paired_success',0)>0 and not report['candidate']['errors'] and not report['baseline']['errors']:
            return
    raise ValueError('模型或阈值变更前，请先完成对应候选的离线对照')
