import hashlib,time,shutil,threading
from uuid import uuid4
import cv2
from app.core import database as db
from app.core.config import DATA_DIR
from app.services.settings import current
DIRECTORY=DATA_DIR/'evidence'
DIRECTORY.mkdir(exist_ok=True)
_lock=threading.RLock()
_storage_error=''
def storage_status():
    used=db.query_one('SELECT COALESCE(SUM(n),0) AS n FROM (SELECT MAX(bytes) n FROM evidence GROUP BY path)')['n']
    cfg=current()['settings']
    return {'used_bytes':used,'quota_bytes':int(cfg['quota_gib']*1024**3),'free_bytes':shutil.disk_usage(DATA_DIR).free,'error':_storage_error}
def resolve(path):
    target=(DATA_DIR/path).resolve()
    if target.parent!=DIRECTORY.resolve():raise ValueError('证据路径无效')
    return target
def attach(event_id,role,frame,media_time,frame_no,captured_at=None,replace=False):
    global _storage_error
    h,w=frame.shape[:2]
    if max(h,w)>1920:frame=cv2.resize(frame,(round(w*1920/max(h,w)),round(h*1920/max(h,w))))
    ok,encoded=cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,90])
    if not ok:raise OSError('证据编码失败')
    data=encoded.tobytes()
    with _lock:
        sha=hashlib.sha256(data).hexdigest()
        existing=db.query_one('SELECT * FROM evidence WHERE event_id=? AND role=?',(event_id,role))
        if existing and (not replace or (existing['sha256']==sha and existing['media_time']==media_time)):return existing['id']
        shared=db.query_one('SELECT path FROM evidence WHERE sha256=? LIMIT 1',(sha,))
        path=resolve(shared['path']) if shared else DIRECTORY/(sha+'.jpg')
        added=0 if path.exists() else len(data)
        cfg=current()['settings'];status=storage_status()
        if status['used_bytes']+added>status['quota_bytes'] or status['free_bytes']-added<cfg['min_free_gib']*1024**3:
            _storage_error='证据容量或磁盘剩余空间不足；新证据未保存'
            raise OSError(_storage_error)
        temp=DIRECTORY/(uuid4().hex+'.tmp');created=False
        try:
            if added:temp.write_bytes(data);temp.replace(path);created=True
            with db.transaction() as c:
                # Previous IDs and bytes remain immutable for already issued AI requests.
                if existing:c.execute('UPDATE evidence SET role=? WHERE id=?',('history_'+role+'_'+str(existing['id']),existing['id']))
                eid=c.execute('INSERT INTO evidence(event_id,role,path,sha256,captured_at,media_time,frame_no,bytes,written_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (event_id,role,path.relative_to(DATA_DIR).as_posix(),sha,captured_at if captured_at is not None else time.time(),media_time,frame_no,len(data),time.time())).lastrowid
            _storage_error='';return eid
        except BaseException:
            if created and not db.query_one('SELECT id FROM evidence WHERE path=?',(path.relative_to(DATA_DIR).as_posix(),)):path.unlink(missing_ok=True)
            raise
        finally:temp.unlink(missing_ok=True)
def cleanup(limit=50):
    """Expire only registered evidence of closed, unlocked events; preserve review history."""
    with _lock:
        cutoff=time.time()-current()['settings']['retention_days']*86400
        events=db.query("SELECT id,review_state FROM events WHERE updated_at<? AND lifecycle='closed' AND locked=0 AND expired=0 AND review_state!='confirmed' ORDER BY updated_at LIMIT ?",(cutoff,limit))
        for event in events:
            with db.transaction() as c:
                # Recheck while holding the write lock: a concurrent human confirmation wins.
                row=c.execute('SELECT * FROM events WHERE id=?',(event['id'],)).fetchone()
                if row['locked'] or row['expired'] or row['review_state']=='confirmed' or row['lifecycle']!='closed':continue
                assets=c.execute('SELECT * FROM evidence WHERE event_id=?',(event['id'],)).fetchall()
                c.execute('DELETE FROM evidence WHERE event_id=?',(event['id'],))
                c.execute("UPDATE events SET expired=1,review_state=CASE WHEN review_state IN ('pending','uncertain') THEN 'expired_unreviewed' ELSE review_state END WHERE id=?",(event['id'],))
                c.execute('INSERT INTO maintenance_log(created_at,reason,event_id) VALUES(?,?,?)',(time.time(),'证据保留期到期',event['id']))
            for asset in assets:
                if not db.query_one('SELECT id FROM evidence WHERE path=?',(asset['path'],)):resolve(asset['path']).unlink(missing_ok=True)
        return len(events)

