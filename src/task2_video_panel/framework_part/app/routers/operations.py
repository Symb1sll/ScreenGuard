import csv,io,json,time
from urllib.parse import urlsplit
from fastapi import APIRouter,Request,HTTPException,Query
from fastapi.responses import FileResponse,Response
from pydantic import BaseModel,Field,SecretStr,ConfigDict
from app.core import database as db
from app.services import operations,backups,settings,evidence,events,diagnostics
from app.services.video_worker import manager
router=APIRouter(prefix='/api')
class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Webhook(Strict):
    expected_version:int
    enabled:bool
    url:SecretStr|None=None
    key_env:str='SCREEN_GUARD_WEBHOOK_URL'
    clear:bool=False
class SourceEdit(Strict):
    name:str=Field(min_length=1,max_length=80)
    location:str=Field('',max_length=200)
    url:SecretStr|None=None
    archived:bool=False
class Hold(Strict):
    locked:bool
    reason:str=Field(min_length=1,max_length=500)
def error(exc):return HTTPException(409 if isinstance(exc,RuntimeError) else 400,str(exc))
@router.get('/operations/status')
def status():
    cfg=settings.current()['settings']
    pending=db.query_one("SELECT COUNT(*) n FROM events WHERE review_state IN ('pending','uncertain') AND expired=0")['n']
    minutes=round(pending*cfg['review_seconds_per_item']/60,1)
    return {'pending':pending,'estimated_minutes':minutes,'budget_minutes':cfg['review_minutes'],'over_budget':minutes>cfg['review_minutes'],
        'unacknowledged':db.query_one('SELECT COUNT(*) n FROM notifications WHERE acked_at IS NULL')['n'],
        'delivery_failures':db.query_one("SELECT COUNT(*) n FROM notifications WHERE delivery='failed'")['n'],
        'storage':diagnostics.storage(),'evidence':evidence.storage_status(),
        'desired_sources':db.query_one('SELECT COUNT(*) n FROM video_sources WHERE desired_running=1 AND archived=0')['n']}
@router.get('/operations/notifications')
def notifications(page:int=Query(1,ge=1),unacked:bool=False):
    where='WHERE acked_at IS NULL' if unacked else ''
    return {'items':db.query('SELECT * FROM notifications '+where+' ORDER BY created_at DESC LIMIT 30 OFFSET ?',((page-1)*30,)),
        'total':db.query_one('SELECT COUNT(*) n FROM notifications '+where)['n'],'page':page}
@router.post('/operations/notifications/{nid}/ack')
def ack(nid:str,request:Request):
    try:operations.acknowledge(nid,request.state.actor)
    except ValueError as exc:raise error(exc)
    return {'ok':True}
@router.post('/operations/notifications/{nid}/retry')
def retry(nid:str):
    try:operations.retry(nid)
    except ValueError as exc:raise error(exc)
    return {'ok':True}
@router.get('/operations/webhook')
def webhook():return operations.webhook_status()
@router.put('/operations/webhook')
def put_webhook(req:Webhook):
    try:return operations.save_webhook(req.expected_version,req.enabled,req.url.get_secret_value() if req.url else None,req.key_env,req.clear)
    except (ValueError,RuntimeError) as exc:raise error(exc)
@router.get('/operations/audit')
def audit(page:int=Query(1,ge=1)):
    return db.query('SELECT * FROM audit_log ORDER BY id DESC LIMIT 50 OFFSET ?',((page-1)*50,))
@router.get('/operations/backups')
def backup_list():return backups.list_backups()
@router.post('/operations/backups')
def backup_create():
    try:return backups.create()
    except (ValueError,OSError) as exc:raise HTTPException(400,str(exc) if isinstance(exc,ValueError) else '创建备份失败，请检查磁盘空间')
@router.get('/operations/backups/{name}')
def backup_download(name:str):
    try:path=backups.resolve(name)
    except ValueError as exc:raise error(exc)
    if not path.is_file():raise HTTPException(404,'备份不存在')
    return FileResponse(path,filename=name,media_type='application/zip')
@router.delete('/operations/backups/{name}')
def backup_delete(name:str):
    try:
        with backups._lock:backups.resolve(name).unlink(missing_ok=True)
    except ValueError as exc:raise error(exc)
    return {'ok':True}
@router.get('/operations/sources')
def all_sources():
    from app.routers.api import safe_source
    return [safe_source(r) for r in db.query('SELECT * FROM video_sources ORDER BY archived,id DESC')]
@router.put('/operations/sources/{sid}')
def edit_source(sid:int,req:SourceEdit):
    with manager.lock:
        row=db.query_one('SELECT * FROM video_sources WHERE id=?',(sid,))
        if not row:raise HTTPException(404,'视频源不存在')
        if sid in manager.stop_flags:raise HTTPException(409,'请先停止采集，再修改或归档')
        value=row['source_value']
        if req.url and req.url.get_secret_value():
            if row['source_type']!='rtsp':raise HTTPException(400,'本地文件地址不能修改')
            value=req.url.get_secret_value();u=urlsplit(value)
            if u.scheme not in ('rtsp','rtsps','https','http') or not u.hostname or len(value)>1000:raise HTTPException(400,'视频流地址无效')
        if not req.name.strip():raise HTTPException(400,'名称不能为空')
        db.execute('UPDATE video_sources SET name=?,location=?,source_value=?,archived=?,desired_running=0 WHERE id=?',(req.name.strip(),req.location,value,int(req.archived),sid))
        manager.latest.pop(sid,None)
    return {'ok':True,'message':'视频源已保存；归档保留历史证据'}
@router.post('/events/{eid}/hold')
def hold(eid:int,req:Hold,request:Request):
    with events._lock,db.transaction() as c:
        row=c.execute('SELECT expired FROM events WHERE id=?',(eid,)).fetchone()
        if not row:raise HTTPException(404,'事件不存在')
        if row['expired']:raise HTTPException(409,'证据已过期，不能恢复保留')
        c.execute('UPDATE events SET locked=? WHERE id=?',(int(req.locked),eid))
        c.execute('INSERT INTO audit_log(created_at,actor,action,target,status,detail) VALUES(?,?,?,?,?,?)',
            (time.time(),request.state.actor,'hold' if req.locked else 'release',str(eid),200,req.reason))
    return {'locked':req.locked}
@router.get('/operations/report.csv')
def report():
    fields=['id','source_name','location','kind','review_state','media_start','media_end','content_version','started_at','reviewed_at','review_labels','review_note']
    rows=db.query('SELECT e.*,s.name source_name,s.location FROM events e JOIN video_sources s ON s.id=e.source_id ORDER BY e.id DESC LIMIT 10000')
    out=io.StringIO(newline='');writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader()
    for row in rows:
        for k in fields:
            if isinstance(row[k],str) and row[k].lstrip().startswith(('=','+','-','@')):row[k]="'"+row[k]
        writer.writerow(row)
    return Response(out.getvalue().encode('utf-8-sig'),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="review-report.csv"'})
