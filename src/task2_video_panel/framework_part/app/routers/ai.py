"""Authenticated by the workbench session/CSRF middleware."""
import json
from fastapi import APIRouter,File,Form,HTTPException,UploadFile,Query
from pydantic import ValidationError
from app.core import database as db
from app.services import ai_connections as ai
from app.services.ai_protocols import PROVIDERS
router=APIRouter(prefix='/api/ai',tags=['model-interfaces'])

@router.get('/providers')
def providers():return {'providers':PROVIDERS,'local':ai.local_info()}

@router.get('/connections')
def connections():return ai.connections()

@router.put('/connections/{id}')
def save(id:str,data:ai.SaveConnection):return ai.save(id,data)

@router.post('/connections/{id}/models')
def models(id:str,expected_version:int=Query(ge=1)):return ai.models(id,expected_version)

@router.get('/connections/{id}/history')
def history(id:str):
    ai.get_row(id)
    rows=db.query('SELECT version,body,created_at FROM ai_connection_history WHERE connection_id=? ORDER BY version DESC LIMIT 50',(id,))
    return [{'version':r['version'],'config':json.loads(r['body']),'created_at':r['created_at']} for r in rows]

@router.post('/requests',status_code=202)
def submit(data:ai.Submit):return ai.submit(data)

@router.post('/requests/image',status_code=202)
async def upload(metadata:str=Form(...),file:UploadFile=File(...)):
    try:
        if len(metadata)>20000:raise HTTPException(413,'参数过长')
        try:data=ai.Submit.model_validate_json(metadata)
        except ValidationError:raise HTTPException(422,'图片调用参数无效') from None
        raw=await file.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise HTTPException(413,'图片不能超过 8 MiB')
        # Image decoding + SQLite work belong on a worker, not the server's event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(ai.submit,data,raw)
    finally:await file.close()

@router.get('/requests')
def requests(event_id:int|None=Query(default=None,ge=1),limit:int=Query(default=20,ge=1,le=100)):
    rows=db.query('SELECT id FROM ai_requests '+('WHERE event_id=? ' if event_id is not None else '')+'ORDER BY created_at DESC LIMIT ?',
                  (event_id,limit) if event_id is not None else (limit,))
    return [ai.get_request(r['id']) for r in rows]

@router.get('/requests/{id}')
def request(id:str):return ai.get_request(id)

