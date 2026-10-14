"""Persistent in-app notifications, optional webhook delivery and operational summaries."""
import json,os,time,uuid,hmac,hashlib,shutil,threading
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from app.core import database as db
from app.core.config import DATA_DIR
from app.services import settings,evidence
_initialized=False
_delivery_lock=threading.Lock()
def init():
    global _initialized
    if _initialized:return
    with db.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,dedupe TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,title TEXT NOT NULL,event_id INTEGER,source_id INTEGER,created_at REAL NOT NULL,acked_at REAL,acked_by TEXT NOT NULL DEFAULT '',delivery TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',webhook_version INTEGER NOT NULL DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS webhook_config(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,enabled INTEGER NOT NULL,secret BLOB,key_env TEXT NOT NULL)")
        c.execute("INSERT OR IGNORE INTO webhook_config VALUES(1,0,0,NULL,'SCREEN_GUARD_WEBHOOK_URL')")
        c.execute('CREATE TABLE IF NOT EXISTS source_health(source_id INTEGER PRIMARY KEY,state TEXT NOT NULL,incident TEXT NOT NULL)')
    _initialized=True
def webhook_status():
    init();row=db.query_one('SELECT * FROM webhook_config WHERE id=1')
    return {'version':row['version'],'enabled':bool(row['enabled']),'has_saved_url':bool(row['secret']),'key_env':row['key_env'],'has_env_url':bool(os.environ.get(row['key_env'],''))}
def validate_url(url):
    try:u=urlsplit(url);u.port
    except ValueError:raise ValueError('Webhook 地址无效') from None
    if len(url)>4096 or u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or any(x.isspace() for x in url):raise ValueError('Webhook 需要有效 HTTPS 地址')
    return url
def save_webhook(expected_version,enabled,url=None,key_env='SCREEN_GUARD_WEBHOOK_URL',clear=False):
    import re
    if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,99}',key_env):raise ValueError('环境变量名无效')
    if url:
        validate_url(url)
        from app.services.ai_connections import protect
        secret=protect(url.encode())
    else:secret=None
    with db.transaction() as c:
        row=c.execute('SELECT * FROM webhook_config WHERE id=1').fetchone()
        if row['version']!=expected_version:raise RuntimeError('Webhook 设置已变化，请重载')
        if row['key_env']!=key_env and row['secret'] and not (clear or url):raise ValueError('修改地址来源时请重新填写地址或清除旧地址')
        value=secret if url else None if clear else row['secret']
        if enabled and not value and not os.environ.get(key_env):raise ValueError('启用前请配置 Webhook 地址')
        if enabled and not value:validate_url(os.environ[key_env])
        c.execute('UPDATE webhook_config SET version=version+1,enabled=?,secret=?,key_env=? WHERE id=1',(int(enabled),value,key_env))
    return webhook_status()
def emit(dedupe,kind,title,event_id=None,source_id=None):
    init();cfg=webhook_status()
    db.execute('INSERT OR IGNORE INTO notifications(id,dedupe,kind,title,event_id,source_id,created_at,delivery,webhook_version) VALUES(?,?,?,?,?,?,?,?,?)',
        (uuid.uuid4().hex,dedupe,kind,title,event_id,source_id,time.time(),'pending' if cfg['enabled'] else 'disabled',cfg['version']))
def acknowledge(id,actor):
    if not db.query_one('SELECT id FROM notifications WHERE id=?',(id,)):raise ValueError('通知不存在')
    db.execute("UPDATE notifications SET acked_at=?,acked_by=? WHERE id=? AND acked_at IS NULL",(time.time(),actor,id))
def retry(id):
    cfg=webhook_status()
    if not cfg['enabled']:raise ValueError('请先配置并启用 Webhook')
    if not db.query_one("SELECT id FROM notifications WHERE id=? AND delivery IN ('failed','disabled')",(id,)):raise ValueError('只有失败或未配置渠道的通知可以重新提交')
    db.execute("UPDATE notifications SET delivery='pending',attempts=0,next_at=0,error='',webhook_version=? WHERE id=?",(cfg['version'],id))

def in_quiet_hours():
    from datetime import datetime,timezone,timedelta
    cfg=settings.current()['settings']
    if not cfg['quiet_hours']:return False
    tz=timezone(timedelta(hours=8)) if cfg['timezone']=='Asia/Shanghai' else timezone.utc
    clock=datetime.now(tz).strftime('%H:%M');start,end=cfg['quiet_start'],cfg['quiet_end']
    return start<=clock<end if start<end else clock>=start or clock<end if start>end else False
def deliver_one():
    init()
    if not _delivery_lock.acquire(blocking=False):return
    try:
        cfg=db.query_one('SELECT * FROM webhook_config WHERE id=1')
        if not cfg['enabled']:return
        quiet=in_quiet_hours()
        with db.transaction() as c:
            row=c.execute("SELECT * FROM notifications WHERE delivery='pending' AND next_at<=? AND (?=0 OR kind NOT IN ('summary','recovery')) ORDER BY CASE kind WHEN 'alert' THEN 0 ELSE 1 END,created_at LIMIT 1",(time.time(),int(quiet))).fetchone()
            if not row:return
            row=dict(row)
            if row['webhook_version']!=cfg['version']:
                c.execute("UPDATE notifications SET delivery='failed',error='地址配置已变更，请确认后重新提交' WHERE id=?",(row['id'],));return
            c.execute("UPDATE notifications SET delivery='sending',attempts=attempts+1 WHERE id=?",(row['id'],))
        try:
            if cfg['secret']:
                from app.services.ai_connections import protect
                url=protect(cfg['secret'],True).decode()
            else:url=os.environ.get(cfg['key_env'],'')
            validate_url(url)
            payload={k:row[k] for k in ('id','kind','title','event_id','source_id','created_at')}
            body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
            headers={'Content-Type':'application/json','X-ScreenGuard-Delivery':row['id']}
            signing=os.environ.get('SCREEN_GUARD_WEBHOOK_SIGNING_KEY','')
            if signing:headers['X-ScreenGuard-Signature']='sha256='+hmac.new(signing.encode(),body,hashlib.sha256).hexdigest()
            with httpx.Client(timeout=5,follow_redirects=False,trust_env=False) as client:
                with client.stream('POST',url,content=body,headers=headers) as response:
                    if not 200<=response.status_code<300:raise ValueError('远端 HTTP '+str(response.status_code))
            db.execute("UPDATE notifications SET delivery='delivered',error='' WHERE id=?",(row['id'],))
        except Exception as exc:
            attempts=row['attempts']+1
            # Never log the URL or remote response (webhook tokens may be in either).
            message=str(exc) if isinstance(exc,ValueError) and str(exc).startswith('远端 HTTP ') else '投递失败，请检查地址配置和网络'
            db.execute('UPDATE notifications SET delivery=?,next_at=?,error=? WHERE id=?',('failed' if attempts>=3 else 'pending',time.time()+[5,30,300][min(attempts-1,2)],message,row['id']))
    finally:_delivery_lock.release()
def sweep():
    init();cfg=settings.current()['settings'];now=time.time()
    for row in db.query('SELECT * FROM video_sources WHERE archived=0'):
        old=db.query_one('SELECT * FROM source_health WHERE source_id=?',(row['id'],))
        from app.services.diagnostics import source_status
        fault=row['desired_running'] and row['status'] not in ('starting','scheduled') and not source_status(row,now)['healthy']
        if row['desired_running'] and row['status']=='starting' and old and old['state']=='fault':fault=True
        state='fault' if fault else 'ok'
        if not old or old['state']!=state:
            incident=uuid.uuid4().hex
            db.execute('INSERT INTO source_health VALUES(?,?,?) ON CONFLICT(source_id) DO UPDATE SET state=excluded.state,incident=excluded.incident',(row['id'],state,incident))
            if fault:emit('source:'+incident,'device','视频源异常，请检查采集和推理状态',source_id=row['id'])
            elif old and row['status']=='running':emit('source:'+incident,'recovery','视频源已恢复',source_id=row['id'])
    # Catch an alert created just before a process crash, as well as legacy records.
    for row in db.query("SELECT id,source_id FROM events WHERE kind='alert' AND review_state IN ('pending','uncertain') AND expired=0"):
        emit('event:'+str(row['id']),'alert','存在经时序确认的风险片段，请及时复核',row['id'],row['source_id'])
    batch=settings.batch_info()
    count=db.query_one("SELECT COUNT(*) n FROM events WHERE review_state IN ('pending','uncertain') AND expired=0 AND started_at<=?",(batch['cutoff'],))['n']
    if count:emit('batch:'+str(batch['cutoff']),'summary','本次集中复核已有待审片段，请打开工作台处理')
    status=evidence.storage_status()
    if status['error'] or status['free_bytes']<cfg['min_free_gib']*1024**3 or status['used_bytes']>=status['quota_bytes']*.9:
        emit('storage:'+batch['day'],'storage','证据空间不足或接近上限，请检查留存状态')
    db.execute('DELETE FROM sessions WHERE created_at<? OR last_at<?',(now-86400,now-1800))
    db.execute('DELETE FROM login_limits WHERE start_at<?',(now-86400,))
def recover():
    init();db.execute("UPDATE notifications SET delivery='pending',next_at=0,error='重启后待确认重投；接收端请按通知 ID 去重' WHERE delivery='sending'")
def storage_breakdown():
    categories={}
    def size(path):
        # WAL/checkpoint files and expired evidence can disappear during a scan.
        try:return path.stat().st_size if path.is_file() else 0
        except FileNotFoundError:return 0
    for name in ('evidence','uploads','models','backups'):
        folder=DATA_DIR/name
        categories[name]=sum(size(p) for p in folder.rglob('*')) if folder.exists() else 0
    categories['database']=sum(size(p) for p in DATA_DIR.glob('screen_guard.db*'))
    return {'categories':categories,'total_bytes':sum(categories.values()),'free_bytes':shutil.disk_usage(DATA_DIR).free}
