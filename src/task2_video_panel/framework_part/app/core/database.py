import sqlite3
from contextlib import contextmanager
from threading import RLock
from .config import DB_PATH
_lock=RLock()
SCHEMA="""
CREATE TABLE IF NOT EXISTS video_sources(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,source_type TEXT NOT NULL,source_value TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'created',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,last_message TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS detection_records(id INTEGER PRIMARY KEY AUTOINCREMENT,source_id INTEGER NOT NULL,frame_no INTEGER NOT NULL,label TEXT NOT NULL,subtype TEXT NOT NULL,risk_score REAL NOT NULL,is_violation INTEGER NOT NULL,snapshot_path TEXT DEFAULT '',detail TEXT DEFAULT '',created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,source_id INTEGER NOT NULL,record_id INTEGER NOT NULL,level TEXT NOT NULL,title TEXT NOT NULL,message TEXT NOT NULL,snapshot_path TEXT DEFAULT '',handled INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings_history(version INTEGER PRIMARY KEY AUTOINCREMENT,body TEXT NOT NULL,created_at REAL NOT NULL,actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roi_history(id INTEGER PRIMARY KEY AUTOINCREMENT,source_id INTEGER NOT NULL,body TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,source_id INTEGER NOT NULL,job_id TEXT NOT NULL,roi_id TEXT NOT NULL,
 roi_version INTEGER NOT NULL,settings_version INTEGER NOT NULL,model_id TEXT NOT NULL,kind TEXT NOT NULL,
 labels TEXT NOT NULL,scores TEXT NOT NULL,reason TEXT NOT NULL,started_at REAL NOT NULL,updated_at REAL NOT NULL,
 media_start REAL NOT NULL,media_end REAL NOT NULL,samples INTEGER NOT NULL DEFAULT 1,peak REAL NOT NULL DEFAULT 0,
 lifecycle TEXT NOT NULL DEFAULT 'open',review_state TEXT NOT NULL DEFAULT 'pending',review_labels TEXT NOT NULL DEFAULT '[]',
 review_note TEXT NOT NULL DEFAULT '',review_version INTEGER NOT NULL DEFAULT 0,reviewed_at REAL,
 expired INTEGER NOT NULL DEFAULT 0,locked INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS events_queue ON events(review_state,expired,updated_at);
CREATE INDEX IF NOT EXISTS events_source ON events(source_id,started_at);
CREATE TABLE IF NOT EXISTS evidence(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id INTEGER NOT NULL REFERENCES events(id),role TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,captured_at REAL NOT NULL,media_time REAL NOT NULL,frame_no INTEGER NOT NULL,bytes INTEGER NOT NULL,UNIQUE(event_id,role));
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,event_id INTEGER NOT NULL REFERENCES events(id),previous TEXT NOT NULL,result TEXT NOT NULL,actor TEXT NOT NULL,created_at REAL NOT NULL,duration_ms INTEGER NOT NULL,operation TEXT NOT NULL,undo_of INTEGER);
CREATE TABLE IF NOT EXISTS comparisons(id TEXT PRIMARY KEY,created_at REAL NOT NULL,status TEXT NOT NULL,spec TEXT NOT NULL,report TEXT NOT NULL DEFAULT '{}',error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS review_batches(day TEXT PRIMARY KEY,cutoff REAL NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS maintenance_log(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at REAL NOT NULL,reason TEXT NOT NULL,event_id INTEGER);
CREATE TABLE IF NOT EXISTS alert_cooldowns(source_id INTEGER NOT NULL,roi_id TEXT NOT NULL,label TEXT NOT NULL,last_at REAL NOT NULL,PRIMARY KEY(source_id,roi_id,label));
CREATE TABLE IF NOT EXISTS audit_buckets(source_id INTEGER NOT NULL,day TEXT NOT NULL,bucket INTEGER NOT NULL,event_id INTEGER,PRIMARY KEY(source_id,day,bucket));
"""
def _conn():
    c=sqlite3.connect(str(DB_PATH),timeout=15);c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=15000')
    return c
@contextmanager
def transaction():
    with _lock:
        c=_conn()
        try:c.execute('BEGIN IMMEDIATE');yield c;c.commit()
        except BaseException:c.rollback();raise
        finally:c.close()
def init_db():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with _lock:
        c=_conn()
        migration_lock=None
        try:
            from .migrations import LATEST
            version=c.execute('PRAGMA user_version').fetchone()[0]
            if version>LATEST:raise RuntimeError('数据库版本高于程序版本')
            if version<LATEST:
                from app.services import runtime_lock
                migration_lock=runtime_lock.acquire()
                if c.execute("SELECT name FROM sqlite_master WHERE name='video_sources'").fetchone():
                    # A SQLite online backup includes the WAL; copying only .db would not.
                    from uuid import uuid4
                    backup_dir=DB_PATH.parent/'backups';backup_dir.mkdir(exist_ok=True)
                    snapshot=backup_dir/('schema-v'+str(version)+'-'+uuid4().hex+'.db')
                    destination=sqlite3.connect(str(snapshot))
                    try:c.backup(destination)
                    finally:destination.close()
            c.execute('PRAGMA journal_mode=WAL');c.executescript(SCHEMA)
            columns={r[1] for r in c.execute('PRAGMA table_info(video_sources)')}
            additions={'job_id':"TEXT DEFAULT ''",'last_frame_at':'REAL','frame_width':'INTEGER DEFAULT 0','frame_height':'INTEGER DEFAULT 0','processed':'INTEGER DEFAULT 0','inference_state':"TEXT DEFAULT 'idle'",'settings_version':'INTEGER DEFAULT 0'}
            for name,decl in additions.items():
                if name not in columns:c.execute(f'ALTER TABLE video_sources ADD COLUMN {name} {decl}')
            for table, additions in {
                'events':{'episode_id':"TEXT NOT NULL DEFAULT ''",'content_version':'INTEGER NOT NULL DEFAULT 0','sealed_at':'REAL','evidence_error':"TEXT NOT NULL DEFAULT ''"},
                'evidence':{'written_at':'REAL'},
                'audit_buckets':{'signature':"TEXT NOT NULL DEFAULT ''"},
                'video_sources':{'desired_running':'INTEGER NOT NULL DEFAULT 0','archived':'INTEGER NOT NULL DEFAULT 0','location':"TEXT NOT NULL DEFAULT ''"},
            }.items():
                columns={r[1] for r in c.execute(f'PRAGMA table_info({table})')}
                for name,decl in additions.items():
                    if name not in columns:c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {decl}')
            from .migrations import apply
            apply(c);c.commit()
        finally:
            c.close()
            if migration_lock:
                from app.services.runtime_lock import release
                release(migration_lock)
def execute(sql,params=()):
    with transaction() as c:return int(c.execute(sql,params).lastrowid or 0)
def query(sql,params=()):
    with _lock:
        c=_conn()
        try:return [dict(r) for r in c.execute(sql,params).fetchall()]
        finally:c.close()
def query_one(sql,params=()):
    rows=query(sql,params);return rows[0] if rows else None

