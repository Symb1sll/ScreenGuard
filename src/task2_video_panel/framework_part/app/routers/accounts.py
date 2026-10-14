from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field,SecretStr
from app.services import accounts
from app.core import database as db
router=APIRouter(prefix='/api/auth')
class Login(BaseModel):
    username:str=Field(min_length=1,max_length=64)
    password:SecretStr=Field(min_length=1,max_length=256)
class NewUser(Login):role:str='reviewer'
class Enabled(BaseModel):enabled:bool
class Password(BaseModel):password:SecretStr=Field(min_length=12,max_length=256)
@router.post('/login')
def login(req:Login,request:Request):
    if not accounts.enabled():raise HTTPException(400,'当前使用本机模式')
    try:token,csrf=accounts.authenticate(req.username,req.password.get_secret_value(),request.client.host)
    except ValueError as exc:raise HTTPException(401,str(exc)) from None
    response=JSONResponse({'csrf':csrf})
    response.set_cookie('sg_session',token,httponly=True,samesite='strict',secure=request.url.scheme=='https',max_age=86400)
    return response
@router.post('/logout')
def logout(request:Request):
    accounts.logout(request.cookies.get('sg_session'))
    response=JSONResponse({'ok':True});response.delete_cookie('sg_session');return response
@router.get('/users')
def users():return db.query('SELECT username,role,enabled,created_at FROM users ORDER BY username')
@router.post('/users')
def create(req:NewUser):
    try:accounts.create_user(req.username,req.password.get_secret_value(),req.role)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return {'username':req.username,'role':req.role}
@router.put('/users/{username}')
def enable(username:str,req:Enabled):
    try:accounts.set_enabled(username,req.enabled)
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return {'ok':True}
@router.put('/users/{username}/password')
def password(username:str,req:Password):
    try:accounts.change_password(username,req.password.get_secret_value())
    except ValueError as exc:raise HTTPException(400,str(exc)) from None
    return {'ok':True,'message':'密码已更新，该账号全部会话已失效'}
