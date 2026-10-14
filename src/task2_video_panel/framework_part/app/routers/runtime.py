"""Runtime health, source profiles and incident response APIs."""
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from app.core import database as db
from app.services import source_profiles, diagnostics, auto_review, cases, device_probe
from app.services.video_worker import manager
from app.routers.api import source_or_404, protect_error, jpeg

router = APIRouter(prefix='/api')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ProfileUpdate(Strict):
    expected_version: int = Field(ge=0)
    profile: source_profiles.SourceProfile


class Webcam(Strict):
    name: str = Field(min_length=1, max_length=80)
    device_index: int = Field(0, ge=0, le=15)


class Disposition(Strict):
    state: str = Field(pattern='^(pending|in_progress|resolved)$')
    expected_version: int = Field(ge=0)
    review_version: int = Field(ge=0)
    owner: str = Field('', max_length=80)
    note: str = Field(min_length=1, max_length=1000)


@router.get('/runtime/status')
def status():
    return diagnostics.snapshot()


@router.get('/runtime/ready')
def ready():
    result = diagnostics.snapshot()
    return JSONResponse({'ready': result['ready'], 'monitoring_ok': result['monitoring_ok']}, status_code=200 if result['ready'] else 503)


@router.get('/operations/badge')
def badge():
    return {'unacknowledged': db.query_one('SELECT COUNT(*) n FROM notifications WHERE acked_at IS NULL')['n']}


@router.get('/sources/{sid}/profile')
def profile(sid: int):
    source = source_or_404(sid)
    import json
    return {**source_profiles.get(sid), 'effective': source_profiles.effective(sid),
            'running': json.loads(source['running_config'] or '{}'), 'running_profile_version': source['profile_version'],
            'history': db.query('SELECT version,updated_at,actor FROM source_profile_history WHERE source_id=? ORDER BY version DESC LIMIT 20', (sid,))}


@router.put('/sources/{sid}/profile')
def save_profile(sid: int, payload: ProfileUpdate, request: Request):
    source_or_404(sid)
    try:
        return source_profiles.save(sid, payload.profile.model_dump(), payload.expected_version, request.state.actor)
    except HTTPException:
        raise
    except (ValueError, RuntimeError, KeyError) as exc:
        raise protect_error(exc)


@router.post('/video/webcam')
def webcam(payload: Webcam):
    sid = db.execute('INSERT INTO video_sources(name,source_type,source_value,last_message) VALUES(?,?,?,?)',
                     (payload.name.strip(), 'webcam', str(payload.device_index), '已添加本机摄像头；请先测试画面并标定屏幕'))
    return {'source_id': sid, 'started': False}


@router.post('/sources/{sid}/probe')
def probe(sid: int):
    source = source_or_404(sid)
    if source['archived']:
        raise HTTPException(409, '视频源已归档')
    with manager.lock:
        active = sid in manager.stop_flags
        entry = manager.latest.get(sid)
    if active:
        if not entry:
            raise HTTPException(409, '摄像头正在启动，请等待画面或停止后测试')
        return jpeg(entry[0])
    try:
        frame, stamp = device_probe.probe(source)
        with manager.lock:
            manager.latest[sid] = (frame.copy(), stamp)
        return jpeg(frame)
    except (RuntimeError, ValueError) as exc:
        raise protect_error(exc)


@router.get('/automatic-review')
def automatic():
    return auto_review.status()


@router.get('/events/{eid}/disposition')
def disposition(eid: int):
    try:
        return cases.get(eid)
    except KeyError as exc:
        raise protect_error(exc)


@router.post('/events/{eid}/disposition')
def set_disposition(eid: int, payload: Disposition, request: Request):
    try:
        return cases.save(eid, payload.state, payload.expected_version, payload.owner, payload.note, request.state.actor, payload.review_version)
    except (ValueError, RuntimeError, KeyError) as exc:
        raise protect_error(exc)


@router.get('/dispositions')
def disposition_queue(state: str = 'open', page: int = Query(1, ge=1)):
    if state not in ('open', 'all', 'pending', 'in_progress', 'resolved'):
        raise HTTPException(400, '处置状态无效')
    where = "e.review_state='confirmed'"
    args = []
    if state == 'open':
        where += " AND COALESCE(c.state,'pending')!='resolved'"
    elif state != 'all':
        where += " AND COALESCE(c.state,'pending')=?"
        args.append(state)
    query = ' FROM events e JOIN video_sources s ON s.id=e.source_id LEFT JOIN case_state c ON c.event_id=e.id WHERE '+where
    return {'items': db.query("SELECT e.id,s.name source_name,e.review_labels,COALESCE(c.state,'pending') state,c.owner,c.updated_at"+query+' ORDER BY e.reviewed_at DESC LIMIT 30 OFFSET ?', (*args, (page-1)*30)),
            'total': db.query_one('SELECT COUNT(*) n'+query, args)['n'], 'page': page}
