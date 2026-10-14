"""Optional enterprise accounts; local preview remains loopback-only."""
import hashlib,hmac,json,os,secrets,time
from app.core import database as db

def enabled():return os.environ.get('SCREEN_GUARD_AUTH_MODE','local')=='accounts'
def init():
    with db.transaction() as c:
        c.execute("CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY,password_hash TEXT NOT NULL,role TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL)")
        c.execute('CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,csrf_hash TEXT NOT NULL,created_at REAL NOT NULL,last_at REAL NOT NULL)')
        if 'csrf_value' not in {r[1] for r in c.execute('PRAGMA table_info(sessions)')}:c.execute("ALTER TABLE sessions ADD COLUMN csrf_value TEXT NOT NULL DEFAULT ''")
        c.execute('CREATE TABLE IF NOT EXISTS login_limits(identity TEXT PRIMARY KEY,failures INTEGER NOT NULL,start_at REAL NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY,created_at REAL NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,status INTEGER NOT NULL,detail TEXT NOT NULL)')
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    value=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    return salt+':'+value
def create_user(username,password,role='reviewer'):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',username):raise ValueError('账号需为 3–64 位字母、数字、点、横线或下划线')
    if not 12<=len(password)<=256:raise ValueError('密码需为 12–256 个字符')
    if role not in ('admin','reviewer'):raise ValueError('角色无效')
    hashed=password_hash(password)
    with db.transaction() as c:
        if c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():raise ValueError('账号已存在')
        c.execute('INSERT INTO users VALUES(?,?,?,1,?)',(username,hashed,role,time.time()))
def authenticate(username,password,peer):
    now=time.time();identity=digest(peer+'\n'+username.lower())
    peer_identity=digest('peer:'+peer)
    with db.transaction() as c:
        peer_limit=c.execute('SELECT * FROM login_limits WHERE identity=?',(peer_identity,)).fetchone()
        if peer_limit and now-peer_limit['start_at']<900 and peer_limit['failures']>=30:raise ValueError('登录尝试过多，请 15 分钟后重试')
        limit=c.execute('SELECT * FROM login_limits WHERE identity=?',(identity,)).fetchone()
        if limit and now-limit['start_at']<900 and limit['failures']>=5:raise ValueError('登录尝试过多，请 15 分钟后重试')
        user=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        # Also do the password work for an unknown account.
        stored=user['password_hash'] if user else '00'*16+':'+('00'*64)
        matched=hmac.compare_digest(password_hash(password,stored.split(':')[0]),stored)
        if not user or not user['enabled'] or not matched:
            if not peer_limit or now-peer_limit['start_at']>=900:c.execute('INSERT OR REPLACE INTO login_limits VALUES(?,1,?)',(peer_identity,now))
            else:c.execute('UPDATE login_limits SET failures=failures+1 WHERE identity=?',(peer_identity,))
            if not limit or now-limit['start_at']>=900:c.execute('INSERT OR REPLACE INTO login_limits VALUES(?,1,?)',(identity,now))
            else:c.execute('UPDATE login_limits SET failures=failures+1 WHERE identity=?',(identity,))
            result=None
        else:
            c.execute('DELETE FROM login_limits WHERE identity=?',(identity,));result=dict(user)
    if result is None:
        audit(username,'login','session',401);raise ValueError('账号或密码错误')
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    db.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?)',(digest(token),username,digest(csrf),now,now,csrf))
    audit(username,'login','session',200)
    return token,csrf
def session(token,touch=False):
    if not token:return None
    row=db.query_one('SELECT s.*,u.role,u.enabled FROM sessions s JOIN users u ON u.username=s.username WHERE token_hash=?',(digest(token),))
    now=time.time()
    if not row or not row['enabled'] or now-row['created_at']>86400 or now-row['last_at']>1800:return None
    if touch and now-row['last_at']>30:db.execute('UPDATE sessions SET last_at=? WHERE token_hash=?',(now,row['token_hash']))
    return row
def csrf_valid(row,token):return bool(token) and hmac.compare_digest(row['csrf_hash'],digest(token))
def rotate_csrf(row):
    if row.get('csrf_value'):return row['csrf_value']
    token=secrets.token_urlsafe(32)
    db.execute('UPDATE sessions SET csrf_hash=?,csrf_value=? WHERE token_hash=?',(digest(token),token,row['token_hash']))
    return token
def logout(token):db.execute('DELETE FROM sessions WHERE token_hash=?',(digest(token or ''),))
def audit(actor,action,target,status=200,detail=''):
    db.execute('INSERT INTO audit_log(created_at,actor,action,target,status,detail) VALUES(?,?,?,?,?,?)',(time.time(),actor,action,target,status,detail))
def set_enabled(username,value):
    with db.transaction() as c:
        row=c.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        if not row:raise ValueError('账号不存在')
        if not value and row['role']=='admin' and c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:raise ValueError('至少保留一个启用的管理员')
        c.execute('UPDATE users SET enabled=? WHERE username=?',(int(value),username))
        if not value:c.execute('DELETE FROM sessions WHERE username=?',(username,))

def change_password(username,password):
    if not 12<=len(password)<=256:raise ValueError('密码需为 12–256 个字符')
    hashed=password_hash(password)
    with db.transaction() as c:
        if not c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():raise ValueError('账号不存在')
        c.execute('UPDATE users SET password_hash=? WHERE username=?',(hashed,username))
        c.execute('DELETE FROM sessions WHERE username=?',(username,))
