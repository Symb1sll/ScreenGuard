from __future__ import annotations
import json,time,hashlib,shutil,re
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from fastapi import APIRouter,UploadFile,File,Form,HTTPException,Query,Request
from fastapi.responses import Response,FileResponse
from pydantic import BaseModel,Field,ConfigDict
import cv2,numpy as np
from app.core import database as db
from app.core.config import DATA_DIR,UPLOAD_DIR
from app.services.video_worker import manager
from app.services import settings,regions,events,evidence,models,comparison
from screen_guard_core import LABELS,crop_screen,ModelPackage
router=APIRouter(prefix='/api')
class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class RtspRequest(Strict):
    name:str=Field(min_length=1,max_length=80)
    url:str=Field(min_length=6,max_length=1000)
    auto_start:bool=True
class ConfigRequest(Strict):
    expected_version:int
    settings:settings.Settings
class RegionRequest(Strict):
    expected_version:int=0
    regions:list[dict]=Field(min_length=1,max_length=4)
class PreviewRegion(Strict):
    points:list[list[float]]
class ReviewRequest(Strict):
    expected_version:int
    content_version:int|None=None
    state:str=Field(pattern='^(confirmed|normal|uncertain)$')
    labels:list[str]=Field(default_factory=list,max_length=5)
    note:str=Field('',max_length=1000)
    duration_ms:int=Field(0,ge=0,le=86400000)
class UndoRequest(Strict):
    expected_version:int
    review_id:int
class FreezeRequest(Strict):
    content_version:int=Field(ge=0)
class CompareRequest(Strict):
    candidate_model:str
    candidate_settings:settings.Settings
def protect_error(exc):
    if isinstance(exc,KeyError):return HTTPException(404,str(exc))
    if isinstance(exc,RuntimeError):return HTTPException(409,str(exc))
    return HTTPException(400,str(exc))
def source_or_404(sid):
    row=db.query_one('SELECT * FROM video_sources WHERE id=?',(sid,))
    if not row:raise HTTPException(404,'视频源不存在')
    return row
def safe_source(row):
    row=dict(row);raw=row.pop('source_value','')
    if row['source_type']=='rtsp':
        u=urlsplit(raw)
        row['address_hint']=f'{u.scheme}://{u.hostname or ""}:***/…'
    elif row['source_type']=='webcam':row['address_hint']='本机摄像头 #'+raw
    else:row['address_hint']='本地文件（路径已隐藏）'
    row['frame_url']=f'/api/sources/{row["id"]}/frame'
    row['regions']=regions.get_regions(row['id'])
    version=db.query_one('SELECT body FROM settings_history WHERE version=?',(row['settings_version'],))
    row['sampling_seconds']=json.loads(version['body'])['sample_seconds'] if version else settings.current()['settings']['sample_seconds']
    from app.services import source_profiles,diagnostics
    effective=source_profiles.effective(row['id'])
    running=json.loads(row['running_config'] or '{}')
    row['sampling_seconds']=running.get('sample_seconds',effective['settings']['sample_seconds'])
    row['configuration_pending']=row['settings_version']!=effective['settings_version'] or row['profile_version']!=effective['profile_version']
    row['active_model']=running.get('model_id',effective['settings']['model_id'])
    row['automatic_enabled']=effective['profile']['ai']['enabled']
    row['metrics']=diagnostics.source_status(row)
    row.pop('running_config',None)
    return row

def latest_frame(sid):
    source_or_404(sid);entry=manager.get_frame(sid)
    if entry:return entry
    asset=db.query_one("SELECT v.* FROM evidence v JOIN events e ON e.id=v.event_id WHERE e.source_id=? AND v.role IN ('last_original','first_original') ORDER BY v.captured_at DESC,v.id DESC LIMIT 1",(sid,))
    if asset:
        path=evidence.resolve(asset['path'])
        if path.exists():
            frame=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_COLOR)
            if frame is not None:return frame,asset['captured_at']
    raise HTTPException(404,'还没有采集画面，请先运行视频源')
def jpeg(frame):
    ok,data=cv2.imencode('.jpg',frame)
    if not ok:raise HTTPException(500,'图片编码失败')
    return Response(data.tobytes(),media_type='image/jpeg',headers={'Cache-Control':'no-store'})
@router.get('/health')
def health():return {'ok':bool(db.query_one('SELECT 1 ok')['ok']),'service':'screen-guard','version':'4.1.0','scope':'HTTP 与数据库存活；监控状态见 /api/runtime/status'}
@router.get('/sources')
def sources():return [safe_source(x) for x in db.query('SELECT * FROM video_sources WHERE archived=0 ORDER BY id DESC')]
@router.post('/video/local')
def upload(name:str=Form('本地视频'),auto_start:bool=Form(True),file:UploadFile=File(...)):
    name=name.strip()
    if not 1<=len(name)<=80:raise HTTPException(400,'名称需为 1–80 字')
    ext=Path(file.filename or '').suffix.lower()
    if ext not in {'.mp4','.avi','.mov','.mkv','.flv','.webm'}:raise HTTPException(400,'不支持的视频格式')
    target=UPLOAD_DIR/(uuid4().hex+ext);total=0
    try:
        with target.open('wb') as dest:
            while chunk:=file.file.read(1024*1024):
                total+=len(chunk)
                if total>500*1024**2:raise ValueError('单个视频限 500 MiB')
                if shutil.disk_usage(DATA_DIR).free-len(chunk)<settings.current()['settings']['min_free_gib']*1024**3:raise ValueError('磁盘剩余空间不足')
                dest.write(chunk)
        if not total:raise ValueError('文件为空')
        sid=db.execute('INSERT INTO video_sources(name,source_type,source_value,last_message) VALUES(?,?,?,?)',(name,'local',str(target),'等待开始'))
    except Exception as exc:
        target.unlink(missing_ok=True);raise protect_error(exc)
    started=False
    if auto_start:
        try:started=manager.start(sid)
        except ValueError:pass
    return {'source_id':sid,'started':started}
@router.post('/video/rtsp')
def rtsp(req:RtspRequest):
    u=urlsplit(req.url)
    if u.scheme not in ('rtsp','rtsps','http','https') or not u.hostname:raise HTTPException(400,'视频流地址无效')
    sid=db.execute('INSERT INTO video_sources(name,source_type,source_value,last_message) VALUES(?,?,?,?)',(req.name,'rtsp',req.url,'等待开始'))
    started=False
    if req.auto_start:
        try:started=manager.start(sid)
        except ValueError:pass
    return {'source_id':sid,'started':started}
@router.post('/video/{sid}/start')
def start(sid:int):
    source_or_404(sid)
    try:return {'started':manager.start(sid),'source_id':sid}
    except Exception as exc:raise protect_error(exc)
@router.post('/video/{sid}/stop')
def stop(sid:int):
    source_or_404(sid);return {'stopping':manager.stop(sid),'source_id':sid}
@router.get('/sources/{sid}/frame')
def frame(sid:int):
    image,_=latest_frame(sid);return jpeg(image)
@router.get('/sources/{sid}/roi')
def roi(sid:int):
    source_or_404(sid)
    return {**regions.get_regions(sid),'history':db.query('SELECT id,body,created_at FROM roi_history WHERE source_id=? ORDER BY id DESC LIMIT 20',(sid,))}
@router.post('/sources/{sid}/roi')
def set_roi(sid:int,req:RegionRequest):
    source_or_404(sid)
    try:return regions.save_regions(sid,req.regions,req.expected_version)
    except Exception as exc:raise protect_error(exc)
@router.post('/sources/{sid}/roi/preview')
def preview_roi(sid:int,req:PreviewRegion):
    image,_=latest_frame(sid)
    try:return jpeg(crop_screen(image,req.points))
    except Exception as exc:raise protect_error(exc)
@router.get('/overview')
def overview():
    now=time.time();cfg=settings.current();batch=settings.batch_info()
    return {'sources':sources(),'settings_version':cfg['version'],'model_id':cfg['settings']['model_id'],
            'counts':{
                'pending':db.query_one("SELECT COUNT(*) n FROM events WHERE review_state='pending' AND expired=0")['n'],
                'uncertain':db.query_one("SELECT COUNT(*) n FROM events WHERE review_state='uncertain' AND expired=0")['n'],
                'alerts':db.query_one("SELECT COUNT(*) n FROM events WHERE kind='alert' AND review_state IN ('pending','uncertain') AND expired=0")['n'],
                'confirmed':db.query_one("SELECT COUNT(*) n FROM events WHERE review_state='confirmed'")['n'],
                'expiring':db.query_one("SELECT COUNT(*) n FROM events WHERE review_state IN ('pending','uncertain') AND expired=0 AND updated_at<?",(now-(cfg['settings']['retention_days']-1)*86400,))['n'],
                'expired_unreviewed':db.query_one("SELECT COUNT(*) n FROM events WHERE review_state='expired_unreviewed'")['n'],
                'legacy_records':db.query_one('SELECT COUNT(*) n FROM detection_records')['n'],
            },'review_seconds_mean':db.query_one("SELECT AVG(duration_ms)/1000 AS mean FROM reviews WHERE operation='review' AND duration_ms>0")['mean'],'storage':evidence.storage_status(),'batch':batch,'server_time':now}
@router.get('/events')
def list_events(state:str='all',source_id:int|None=None,label:str='',kind:str='',batch_only:bool=False,expiring:bool=False,page:int=Query(1,ge=1),limit:int=Query(20,ge=1,le=100)):
    clauses=[];args=[]
    if state!='all':
        if state=='queue':clauses.append("e.review_state IN ('pending','uncertain') AND e.expired=0")
        elif state in ('pending','confirmed','normal','uncertain','expired_unreviewed'):clauses.append('e.review_state=?');args.append(state)
        else:raise HTTPException(400,'状态筛选无效')
    if source_id is not None:clauses.append('e.source_id=?');args.append(source_id)
    if label:
        if label not in LABELS:raise HTTPException(400,'类别无效')
        clauses.append('e.labels LIKE ?');args.append('%"'+label+'"%')
    if kind:
        if kind not in ('alert','review','unknown','audit'):raise HTTPException(400,'事件类型无效')
        clauses.append('e.kind=?');args.append(kind)
    if batch_only:clauses.append('e.started_at<=?');args.append(settings.batch_info()['cutoff'])
    if expiring:
        clauses.append('e.updated_at<? AND e.expired=0 AND e.locked=0');args.append(time.time()-(settings.current()['settings']['retention_days']-1)*86400)
    where='WHERE '+' AND '.join(clauses) if clauses else ''
    total=db.query_one('SELECT COUNT(*) n FROM events e '+where,tuple(args))['n']
    rows=db.query('SELECT e.*,s.name source_name,(SELECT id FROM evidence WHERE event_id=e.id AND role=\'first_roi\') thumbnail_id FROM events e JOIN video_sources s ON s.id=e.source_id '+where+" ORDER BY CASE e.kind WHEN 'alert' THEN 0 WHEN 'unknown' THEN 1 WHEN 'review' THEN 2 ELSE 3 END,e.updated_at ASC,e.id ASC LIMIT ? OFFSET ?",tuple(args+[limit,(page-1)*limit]))
    for row in rows:
        for k in ('labels','scores','review_labels'):row[k]=json.loads(row[k])
    return {'items':rows,'total':total,'page':page,'limit':limit,'latest_id':db.query_one('SELECT COALESCE(MAX(id),0) n FROM events')['n']}
@router.get('/events/{eid}')
def detail(eid:int):
    row=events.event_detail(eid)
    if not row:raise HTTPException(404,'事件不存在')
    return row
@router.post('/events/{eid}/review')
def submit_review(eid:int,req:ReviewRequest,request:Request):
    if any(k not in LABELS for k in req.labels) or len(set(req.labels))!=len(req.labels):raise HTTPException(400,'审核类别无效')
    try:return events.review(eid,req.state,req.labels,req.note,req.expected_version,req.duration_ms,req.content_version,request.state.actor)
    except Exception as exc:raise protect_error(exc)
@router.post('/events/{eid}/freeze')
def freeze_event(eid:int,req:FreezeRequest):
    try:return events.freeze(eid,req.content_version)
    except Exception as exc:raise protect_error(exc)
@router.post('/events/{eid}/undo')
def undo_review(eid:int,req:UndoRequest,request:Request):
    try:return events.undo(eid,req.review_id,req.expected_version,request.state.actor)
    except Exception as exc:raise protect_error(exc)
@router.get('/evidence/{eid}')
def get_evidence(eid:int):
    row=db.query_one('SELECT * FROM evidence WHERE id=?',(eid,))
    if not row:raise HTTPException(404,'证据已过期或不存在')
    path=evidence.resolve(row['path'])
    if not path.exists():raise HTTPException(410,'证据文件缺失')
    return FileResponse(path,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
@router.get('/settings')
def get_settings():return settings.current()
@router.post('/settings')
def put_settings(req:ConfigRequest,request:Request):
    try:
        models.validate_model(req.settings.model_id)
        old=settings.current()['settings'];new=req.settings.model_dump()
        if old['model_id']!=new['model_id'] or old['policies']!=new['policies']:
            runs=db.query("SELECT spec,report FROM comparisons WHERE status='completed' ORDER BY created_at DESC")
            approved=False
            for run in runs:
                spec=json.loads(run['spec']);report=json.loads(run['report'])
                if spec['candidate_model']==new['model_id'] and spec['candidate_settings']['policies']==new['policies'] and report.get('paired_success',0)>0 and not report['candidate']['errors'] and not report['baseline']['errors']:
                    approved=True;break
            if not approved:raise ValueError('模型或阈值变更前，请先在“素材与模型”完成对应候选的离线对照')
        return settings.save(req.settings.model_dump(),req.expected_version,request.state.actor)
    except Exception as exc:raise protect_error(exc)
@router.get('/settings/history')
def history():
    rows=db.query('SELECT * FROM settings_history ORDER BY version DESC LIMIT 20')
    for r in rows:r['settings']=json.loads(r.pop('body'))
    return rows
@router.get('/models')
def get_models():return models.list_models()
@router.post('/models/import')
def import_model(manifest:UploadFile=File(...),weights:UploadFile=File(...)):
    staging=models.MODEL_DIR/('staging_'+uuid4().hex);staging.mkdir()
    try:
        raw=manifest.file.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('元数据过大')
        meta=json.loads(raw);mid=meta['model_id']
        if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',mid) or mid=='demo-rules':raise ValueError('模型 ID 无效')
        target=models.MODEL_DIR/mid
        if target.exists():raise ValueError('模型版本已存在，请使用新的不可变 ID')
        total=0
        with (staging/'model.onnx').open('wb') as dest:
            while chunk:=weights.file.read(1024*1024):
                total+=len(chunk)
                if total>200*1024**2:raise ValueError('模型限 200 MiB')
                if shutil.disk_usage(DATA_DIR).free-len(chunk)<settings.current()['settings']['min_free_gib']*1024**3:raise ValueError('磁盘剩余空间不足')
                dest.write(chunk)
        (staging/'manifest.json').write_bytes(raw)
        # Only self-contained ONNX files are accepted.
        import onnx
        graph=onnx.load(str(staging/'model.onnx'),load_external_data=False)
        if any(t.external_data for t in graph.graph.initializer):raise ValueError('请导出不依赖外部数据文件的 ONNX')
        models.validate_package(staging);staging.rename(target)
        return {'model_id':mid,'message':'模型包已校验并导入，尚未切换运行模型'}
    except Exception as exc:raise protect_error(exc)
    finally:
        if staging.exists():
            for name in ('manifest.json','model.onnx'):(staging/name).unlink(missing_ok=True)
            staging.rmdir()
@router.post('/comparisons')
def compare(req:CompareRequest):
    try:return {'id':comparison.submit(req.candidate_model,req.candidate_settings.model_dump())}
    except Exception as exc:raise protect_error(exc)
@router.get('/comparisons')
def compare_list():
    rows=db.query('SELECT id,created_at,status,error FROM comparisons ORDER BY created_at DESC LIMIT 30')
    return rows
@router.get('/comparisons/{job}')
def compare_detail(job:str):
    row=db.query_one('SELECT * FROM comparisons WHERE id=?',(job,))
    if not row:raise HTTPException(404,'对照任务不存在')
    row['spec']=json.loads(row['spec']);row['report']=json.loads(row['report'])
    # Do not reveal internal evidence filesystem paths.
    for sample in row['spec'].get('samples',[]):sample.pop('path',None)
    return row
@router.get('/review-export')
def review_export():
    rows=db.query("SELECT id,source_id,model_id,review_state,review_labels,review_note,reviewed_at FROM events WHERE review_state IN ('confirmed','normal') AND expired=0 ORDER BY id")
    for r in rows:
        r['labels']=json.loads(r.pop('review_labels'));r['status']='candidate'
        r['evidence']=db.query("SELECT id,sha256 FROM evidence WHERE event_id=? AND role='first_roi'",(r['id'],))
    return {'purpose':'人工审核候选数据；需再次验收后才能用于训练','items':rows}

