"""Consistent, portable backups. Restore is deliberately offline into a new directory."""
import hashlib,json,os,sqlite3,time,uuid,zipfile,shutil,threading
from pathlib import Path,PurePosixPath
from contextlib import closing
from app.core import database as db
from app.core.config import DATA_DIR,DB_PATH
from app.services import events,evidence,settings
_lock=threading.Lock()
MAX_BACKUPS=3

def folder():
    path=DATA_DIR/'backups';path.mkdir(exist_ok=True);return path

def resolve(name):
    if not isinstance(name,str) or not __import__('re').fullmatch(r'backup-[0-9-]+-[a-f0-9]{8}\.zip',name):raise ValueError('备份名称无效')
    return folder()/name

def list_backups():
    return [{'name':p.name,'bytes':p.stat().st_size,'created_at':p.stat().st_mtime} for p in sorted(folder().glob('backup-*.zip'),reverse=True)]

def create():
    if not _lock.acquire(False):raise ValueError('备份正在进行')
    stage=folder()/('stage-'+uuid.uuid4().hex);stage.mkdir()
    name=time.strftime('backup-%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8]+'.zip'
    target=resolve(name);part=stage/'archive.part'
    try:
        if len(list_backups())>=MAX_BACKUPS:raise ValueError('已保留 3 份备份，请下载并移除旧备份后再创建')
        with events._lock,evidence._lock,db._lock:
            candidates=set();expected_sha={}
            for row in db.query('SELECT DISTINCT path,sha256 FROM evidence'):
                path=evidence.resolve(row['path'])
                if not path.is_file():raise ValueError('存在缺失证据，请先检查存储状态')
                candidates.add(path.resolve())
                if path.resolve() in expected_sha and expected_sha[path.resolve()]!=row['sha256']:raise ValueError('证据摘要不一致')
                expected_sha[path.resolve()]=row['sha256']
            for row in db.query("SELECT source_value,archived FROM video_sources WHERE source_type='local'"):
                video=Path(row['source_value']).resolve()
                if video.is_relative_to(DATA_DIR.resolve()) and video.is_file():candidates.add(video)
            for model in (DATA_DIR/'models').iterdir():
                if model.is_dir() and not model.name.startswith('staging_') and (model/'manifest.json').is_file():
                    candidates.update(p.resolve() for p in model.rglob('*') if p.is_file())
            candidates.update(p.resolve() for p in (DATA_DIR/'snapshots').rglob('*') if p.is_file())
            if any(not p.is_relative_to(DATA_DIR.resolve()) or p.is_symlink() for p in candidates):raise ValueError('备份文件超出数据目录')
            # Do not silently omit externally referenced local video files.
            for row in db.query("SELECT source_value FROM video_sources WHERE source_type='local' AND archived=0"):
                p=Path(row['source_value']).resolve()
                if not p.is_relative_to(DATA_DIR.resolve()):raise ValueError('存在数据目录外的本地视频，请先导入视频或归档该视频源')
            estimated=sum(p.stat().st_size for p in candidates)+DB_PATH.stat().st_size
            reserve=settings.current()['settings']['min_free_gib']*1024**3
            if shutil.disk_usage(DATA_DIR).free<estimated*2+reserve:raise ValueError('剩余空间不足以创建完整备份')
            snapshot=stage/'screen_guard.db'
            with closing(sqlite3.connect(DB_PATH)) as src,closing(sqlite3.connect(snapshot)) as dst:src.backup(dst)
            entries=[('screen_guard.db',snapshot,None)]
            for path in sorted(candidates):
                rel=path.relative_to(DATA_DIR.resolve()).as_posix();stable=stage/'assets'/rel
                stable.parent.mkdir(parents=True,exist_ok=True)
                # Immutable files remain readable even if retention deletes the original link.
                try:os.link(path,stable)
                except OSError:shutil.copyfile(path,stable)
                entries.append((rel,stable,expected_sha.get(path)))
        # Compression does not block camera event writes.
        with closing(sqlite3.connect(snapshot)) as dst:
            dst.execute('DELETE FROM sessions');dst.execute('DELETE FROM login_limits');dst.commit()
        manifest={'format':1,'created_at':time.time(),'original_data_dir':str(DATA_DIR.resolve()),'files':[]}
        with zipfile.ZipFile(part,'w',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
            for rel,path,expected in entries:
                h=hashlib.sha256()
                with path.open('rb') as src,z.open(rel,'w',force_zip64=True) as dst:
                    while chunk:=src.read(1024**2):h.update(chunk);dst.write(chunk)
                if expected and h.hexdigest()!=expected:raise ValueError('证据文件校验失败，请检查存储')
                manifest['files'].append({'path':rel,'bytes':path.stat().st_size,'sha256':h.hexdigest()})
            z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
        os.replace(part,target)
        return {'name':name,'bytes':target.stat().st_size,'files':len(entries)}
    finally:
        try:
            if stage.resolve().parent!=folder().resolve():raise RuntimeError('暂存目录超出备份目录')
            shutil.rmtree(stage)
        finally:_lock.release()

def restore(archive,destination):
    """No overwrite, no extraction outside destination, no automatic camera restart."""
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('恢复目录必须是尚不存在的新目录')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if len(names)!=len(set(names)) or 'manifest.json' not in names:raise ValueError('备份目录无效')
        if z.getinfo('manifest.json').file_size>20*1024**2:raise ValueError('清单过大')
        manifest=json.loads(z.read('manifest.json'))
        if manifest.get('format')!=1:raise ValueError('不支持的备份版本')
        entries=manifest['files']
        expected={r['path'] for r in entries}
        if len(expected)!=len(entries) or set(names)!=expected|{'manifest.json'} or 'screen_guard.db' not in expected:raise ValueError('备份清单不匹配')
        for row in entries:
            rel=PurePosixPath(row['path'])
            if rel.is_absolute() or '..' in rel.parts or ':' in str(rel) or '\\' in str(rel) or not rel.parts:raise ValueError('备份路径无效')
            if rel.parts[0] not in ('screen_guard.db','evidence','snapshots','models','uploads'):raise ValueError('备份包含未允许文件')
            if z.getinfo(row['path']).file_size!=row['bytes']:raise ValueError('备份大小不匹配')
        if shutil.disk_usage(destination.parent).free<sum(r['bytes'] for r in entries)+100*1024**2:raise ValueError('恢复空间不足')
        # Validate every byte before creating the destination.
        for row in entries:
            h=hashlib.sha256()
            with z.open(row['path']) as src:
                while chunk:=src.read(1024**2):h.update(chunk)
            if h.hexdigest()!=row['sha256']:raise ValueError('备份校验失败：'+row['path'])
        destination.mkdir()
        for row in entries:
            path=destination/row['path'];path.parent.mkdir(parents=True,exist_ok=True)
            with z.open(row['path']) as src,path.open('xb') as dst:shutil.copyfileobj(src,dst)
    with closing(sqlite3.connect(destination/'screen_guard.db')) as c:
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('数据库一致性校验失败')
        for sid,value in c.execute("SELECT id,source_value FROM video_sources WHERE source_type='local'").fetchall():
            # Supports backups created on Windows and restored on Linux.
            old=manifest['original_data_dir'].replace('\\','/').rstrip('/')+'/'
            normalized=value.replace('\\','/')
            if normalized.lower().startswith(old.lower()):
                c.execute('UPDATE video_sources SET source_value=? WHERE id=?',(str(destination/normalized[len(old):]),sid))
        c.execute("UPDATE video_sources SET desired_running=0,status='stopped',last_message='已从备份恢复，请检查配置后手动开始'")
        c.execute("UPDATE events SET lifecycle='closed',sealed_at=COALESCE(sealed_at,?),reason=reason || '；从备份恢复，末尾上下文可能不完整' WHERE lifecycle='open'",(time.time(),))
        c.execute('DELETE FROM sessions')
        if c.execute("SELECT name FROM sqlite_master WHERE name='auto_jobs'").fetchone():
            c.execute("UPDATE auto_jobs SET status='cancelled',reason='从备份恢复；需重新配置自动审核',finished_at=? WHERE status IN ('pending','submitting','waiting','cloud_pending')",(time.time(),))
            for sid,version,body in c.execute('SELECT source_id,version,body FROM source_profiles').fetchall():
                profile=json.loads(body);profile['ai']['enabled']=False;stamp=time.time()
                encoded=json.dumps(profile,ensure_ascii=False)
                c.execute('UPDATE source_profiles SET body=?,version=?,actor=?,updated_at=? WHERE source_id=?',(encoded,version+1,'备份恢复',stamp,sid))
                c.execute('INSERT INTO source_profile_history(source_id,version,body,updated_at,actor) VALUES(?,?,?,?,?)',(sid,version+1,encoded,stamp,'备份恢复'))
        
        c.execute("UPDATE notifications SET delivery='failed',error='恢复后的通知请人工确认后重新提交' WHERE delivery IN ('pending','sending')")
        c.commit()
    return {'destination':str(destination),'files':len(entries),'cameras_started':False}
