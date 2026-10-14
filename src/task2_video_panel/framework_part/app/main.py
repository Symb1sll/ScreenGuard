import os,secrets,threading,logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import FastAPI,Request
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from app.core.database import init_db
from app.core import database as db
from app.core.config import STATIC_DIR
from app.services import settings,evidence,comparison
from app.services.video_worker import manager
from app.routers.api import router
from app.routers.ai import router as ai_router
from app.services import ai_connections
from app.services import accounts,operations,runtime_lock,maintenance,auto_review
from app.routers.accounts import router as accounts_router
from app.routers.operations import router as operations_router
from screen_guard_core import LABELS
init_db();settings.current();ai_connections.init();accounts.init();operations.init()
TOKEN=secrets.token_urlsafe(32)
@asynccontextmanager
async def runtime_lifespan(application):
    ai_connections.restart_cleanup()
    operations.recover()
    auto_review.recover()
    db.execute("UPDATE video_sources SET status='stopped',last_message='服务已重启，按期望运行状态恢复' WHERE status IN ('running','starting','stopping','reconnecting')")
    db.execute("UPDATE events SET lifecycle='closed',sealed_at=?,content_version=content_version+1,reason=reason || '；服务重启，末尾上下文可能不完整' WHERE lifecycle='open'",(__import__('time').time(),))
    db.execute("UPDATE comparisons SET status='error',error='服务重启中断了对照，请重新提交' WHERE status IN ('queued','running')")
    manager.restore()
    maintenance.start()
    try:yield
    finally:
        maintenance.shutdown();comparison.stop.set();manager.shutdown();comparison.executor.shutdown(wait=True,cancel_futures=True)
        ai_connections.shutdown()

@asynccontextmanager
async def lifespan(application):
    with runtime_lock.locked():
        async with runtime_lifespan(application):yield
app=FastAPI(title='屏幕内容审核工作台',version='4.1.0',lifespan=lifespan,docs_url=None,redoc_url=None)
@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    # Pydantic's default error input can contain a submitted API key.
    return JSONResponse({'detail':[{'loc':e['loc'],'msg':e['msg'],'type':e['type']} for e in exc.errors()]},status_code=422)
@app.middleware('http')
async def local_session(request:Request,call_next):
    host=request.url.hostname
    allowed={'127.0.0.1','localhost','::1'}
    enterprise=accounts.enabled()
    if enterprise:allowed.update(x.strip() for x in os.environ.get('SCREEN_GUARD_ALLOWED_HOSTS','').split(',') if x.strip())
    if os.environ.get('SCREEN_GUARD_TESTING')=='1':allowed.add('testserver')
    if host not in allowed:return JSONResponse({'detail':'当前仅开放本机工作台'},status_code=403)
    peers={'127.0.0.1','::1'}
    if os.environ.get('SCREEN_GUARD_TESTING')=='1':peers.add('testclient')
    if not request.client or (not enterprise and request.client.host not in peers):
        return JSONResponse({'detail':'当前仅接受本机连接'},status_code=403)
    if enterprise and request.client.host not in peers and request.url.scheme!='https':
        return JSONResponse({'detail':'企业远程访问需要 HTTPS'},status_code=403)
    path=request.url.path
    origin=request.headers.get('origin')
    if origin and (urlsplit(origin).netloc!=request.url.netloc or urlsplit(origin).scheme!=request.url.scheme):
        return JSONResponse({'detail':'拒绝跨站请求'},status_code=403)
    if path=='/api/bootstrap' and request.headers.get('sec-fetch-site')=='cross-site':
        return JSONResponse({'detail':'拒绝跨站会话'},status_code=403)
    request.state.actor='本机用户';request.state.role='admin'
    if enterprise:
        row=accounts.session(request.cookies.get('sg_session'),touch=request.method not in ('GET','HEAD') or request.headers.get('X-SG-Interaction')=='1')
        request.state.session=row
        if path.startswith('/api/') and path not in ('/api/health','/api/auth/login'):
            if not row:return JSONResponse({'detail':'请登录或重新登录'},status_code=401)
            request.state.actor=row['username'];request.state.role=row['role']
            if request.method not in ('GET','HEAD') and not accounts.csrf_valid(row,request.headers.get('X-SG-CSRF')):
                return JSONResponse({'detail':'请求校验失败，请刷新页面'},status_code=403)
            admin_read=path.startswith(('/api/auth/users','/api/operations/backups','/api/operations/audit','/api/operations/webhook'))
            reviewer_write=path=='/api/auth/logout' or path.startswith('/api/ai/requests') or (path.startswith('/api/events/') and path.rsplit('/',1)[-1] in ('freeze','review','undo','disposition')) or (path.startswith('/api/operations/notifications/') and path.endswith('/ack'))
            if row['role']!='admin' and (admin_read or (request.method not in ('GET','HEAD') and not reviewer_write)):
                return JSONResponse({'detail':'此操作需要管理员权限'},status_code=403)
    elif path.startswith('/api/') and path not in ('/api/health','/api/preview','/api/bootstrap'):
        if request.cookies.get('sg_session')!=TOKEN:return JSONResponse({'detail':'会话已失效，请刷新页面'},status_code=401)
        if request.method not in ('GET','HEAD') and request.headers.get('X-SG-CSRF')!=TOKEN:
            return JSONResponse({'detail':'请求校验失败'},status_code=403)
    response=await call_next(request)
    export_read=request.method=='GET' and (path=='/api/operations/report.csv' or (path.startswith('/api/operations/backups/') and path.endswith('.zip')))
    if (path.startswith('/api/') and request.method not in ('GET','HEAD') and path!='/api/auth/login') or export_read:
        accounts.audit(request.state.actor,request.method,path,response.status_code)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    if path.startswith('/api/') or path.startswith('/static/'):response.headers.setdefault('Cache-Control','no-store')
    return response
@app.get('/api/bootstrap')
def bootstrap(request:Request):
    enterprise=accounts.enabled()
    csrf=accounts.rotate_csrf(request.state.session) if enterprise else TOKEN
    response=JSONResponse({'csrf':csrf,'labels':LABELS,'mode':'enterprise' if enterprise else 'local_workbench','operator':request.state.actor,'role':request.state.role,'scope':'单位账号审核' if enterprise else '本机单用户会话'})
    if not enterprise:response.set_cookie('sg_session',TOKEN,httponly=True,samesite='strict')
    return response
app.include_router(router)
app.include_router(ai_router)
app.include_router(accounts_router)
app.include_router(operations_router)
from app.routers.runtime import router as runtime_router
app.include_router(runtime_router)
app.mount('/static',StaticFiles(directory=str(STATIC_DIR)),name='static')
@app.get('/')
def index():return FileResponse(STATIC_DIR/'index.html',headers={'Cache-Control':'no-store'})
@app.get('/login')
def login_page():return FileResponse(STATIC_DIR/'login.html',headers={'Cache-Control':'no-store'})

