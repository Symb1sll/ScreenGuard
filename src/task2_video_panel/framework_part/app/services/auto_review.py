"""Durable, bounded event-level AI scheduling; never changes a human verdict."""
import json
import time
from uuid import uuid4
from fastapi import HTTPException
from app.core import database as db
from app.services import ai_connections as ai, source_profiles

QUEUE_LIMIT = 64
ACTIVE = ('pending', 'submitting', 'waiting', 'cloud_pending')


def finish(job_id, status, reason, outcome='unknown'):
    db.execute('UPDATE auto_jobs SET status=?,reason=?,outcome=?,finished_at=? WHERE id=?',
               (status, reason, outcome, time.time(), job_id))


def enqueue(event_id):
    event = db.query_one('SELECT * FROM events WHERE id=?', (event_id,))
    if not event or event['expired'] or event['lifecycle'] != 'closed':
        return
    profile = source_profiles.get(event['source_id'])
    policy = dict(profile['profile']['ai'])
    if not policy['enabled'] or (event['kind'] == 'audit' and not policy['include_audits']):
        return
    # All authorization and model destinations are pinned for this task.
    versions = {}
    for key in ('local_connection', 'cloud_connection'):
        if policy[key]:
            row = db.query_one('SELECT version FROM ai_connections WHERE id=?', (policy[key],))
            versions[policy[key]] = row['version'] if row else 0
    policy['connection_versions'] = versions
    policy['labels'] = [k for k, v in source_profiles.effective(event['source_id'])['settings']['policies'].items() if v['enabled']]
    policy['thresholds'] = {k: v['review'] for k, v in source_profiles.effective(event['source_id'])['settings']['policies'].items() if v['enabled']}
    asset = db.query_one("SELECT id,sha256 FROM evidence WHERE event_id=? AND role IN ('peak_roi','first_roi') ORDER BY CASE role WHEN 'peak_roi' THEN 0 ELSE 1 END LIMIT 1", (event_id,))
    now = time.time()
    with db.transaction() as c:
        if c.execute('SELECT id FROM auto_jobs WHERE event_id=?', (event_id,)).fetchone():
            return
        status, reason, related = 'pending', '等待本地模型', None
        if not asset:
            status, reason = 'error', '证据不可用；请人工复核'
        elif event['review_state'] not in ('pending', 'uncertain'):
            status, reason = 'skipped', '已有人工作出结论'
        else:
            prior = c.execute("SELECT j.id FROM auto_jobs j JOIN events e ON e.id=j.event_id WHERE j.source_id=? AND e.roi_id=? AND j.sha256=? AND j.created_at>? AND j.profile_version=? AND j.settings_version=? AND j.status IN ('pending','submitting','waiting','cloud_pending','completed') ORDER BY j.created_at DESC LIMIT 1",
                              (event['source_id'], event['roi_id'], asset['sha256'], now-policy['dedupe_seconds'],profile['version'],event['settings_version'])).fetchone()
            if prior:
                status, reason, related = 'skipped', '相同证据已排队或完成；本事件仍保留人工复核', prior['id']
            elif c.execute("SELECT COUNT(*) FROM auto_jobs WHERE status IN ('pending','submitting','waiting','cloud_pending')").fetchone()[0] >= QUEUE_LIMIT:
                status, reason = 'skipped', '自动队列已满；证据保留，待人工复核'
        c.execute('INSERT INTO auto_jobs(id,event_id,source_id,evidence_id,sha256,content_version,profile_version,settings_version,policy,status,created_at,expires_at,reason,related_job,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (uuid4().hex, event_id, event['source_id'], asset['id'] if asset else None, asset['sha256'] if asset else '', event['content_version'], profile['version'], event['settings_version'], json.dumps(policy), status, now, (event['sealed_at'] or now)+policy['ttl_seconds'], reason, related, None if status == 'pending' else now))


def allowed(job):
    event = db.query_one('SELECT e.*,s.archived FROM events e JOIN video_sources s ON s.id=e.source_id WHERE e.id=?', (job['event_id'],))
    if not event or event['expired'] or event['archived']:
        return '事件已过期或视频源已归档'
    if event['review_state'] not in ('pending', 'uncertain'):
        return '已有人工作出结论，停止后续自动请求'
    if event['content_version'] != job['content_version']:
        return '证据范围已变化'
    current = source_profiles.get(job['source_id'])
    if current['version'] != job['profile_version'] or not current['profile']['ai']['enabled']:
        return '每路配置或自动审核许可已变化'
    if job['stage'] == 'cloud' and not current['profile']['ai']['allow_cloud']:
        return '该视频源已撤回云端许可'
    return ''


def recover():
    # A crash may occur after reserving a provider request but before saving its ID.
    for job in db.query("SELECT * FROM auto_jobs WHERE status='submitting'"):
        req = db.query_one('SELECT id FROM ai_requests WHERE request_id=?', ('auto_'+job['id']+'_'+job['stage'],))
        if req:
            column = 'local_request' if job['stage'] == 'local' else 'cloud_request'
            db.execute(f"UPDATE auto_jobs SET status='waiting',{column}=? WHERE id=?", (req['id'], job['id']))
        else:
            finish(job['id'], 'error', '提交被重启中断；结果未知，未重复发送')


def reconcile(job):
    request_id = job['cloud_request'] if job['stage'] == 'cloud' else job['local_request']
    if not request_id:
        finish(job['id'], 'error', '缺少模型调用记录；请人工复核')
        return
    request = ai.get_request(request_id)
    if request['status'] in ('queued', 'running'):
        return
    reason = allowed(job)
    if reason:
        finish(job['id'], 'cancelled', reason)
        return
    policy = json.loads(job['policy'])
    result = request['result'] or {}
    uncertain = request['status'] != 'completed' or result.get('needs_review', True)
    if uncertain and job['stage'] == 'local' and policy['cloud_connection'] and policy['allow_cloud']:
        db.execute("UPDATE auto_jobs SET status='cloud_pending',stage='cloud',reason='本地无法确定，等待已获许可的云端建议' WHERE id=?", (job['id'],))
        return
    if request['status'] != 'completed':
        finish(job['id'], 'error', request['error'] or '模型结果未知；请人工复核')
        return
    risk = any(result.get('scores', {}).get(k, 0) >= policy.get('thresholds', {}).get(k, .55) for k in policy['labels'])
    outcome = 'unknown' if uncertain else 'risk' if risk else 'low'
    finish(job['id'], 'completed', '辅助建议已保存；告警沿用经过验证的原有策略，人工结论保持独立', outcome)


def dispatch(job):
    reason = allowed(job)
    if reason:
        finish(job['id'], 'cancelled', reason)
        return
    policy = json.loads(job['policy'])
    key = 'cloud_connection' if job['stage'] == 'cloud' else 'local_connection'
    connection = policy[key]
    row = db.query_one('SELECT * FROM ai_connections WHERE id=?', (connection,))
    if not row or row['version'] != policy['connection_versions'].get(connection):
        finish(job['id'], 'cancelled', '模型连接版本已变化；不会将旧任务发送到新地址')
        return
    if db.query_one("SELECT id FROM ai_requests WHERE connection_id=? AND status IN ('queued','running') LIMIT 1", (connection,)):
        return  # One automatic request per destination, including manual use.
    from app.services.settings import current
    from datetime import datetime, timezone, timedelta
    tz = timezone(timedelta(hours=8)) if current()['settings']['timezone'] == 'Asia/Shanghai' else timezone.utc
    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    calls = db.query_one('SELECT COUNT(*) n,MAX(r.created_at) last_at FROM auto_jobs j JOIN ai_requests r ON r.id IN (j.local_request,j.cloud_request) WHERE j.source_id=? AND r.created_at>=?', (job['source_id'], day_start))
    if calls['n'] >= policy['daily_limit']:
        finish(job['id'], 'skipped', '达到本路每日自动调用上限；仍保留人工复核')
        return
    if calls['last_at'] and time.time()-calls['last_at'] < policy['min_interval_seconds']:
        return
    db.execute("UPDATE auto_jobs SET status='submitting' WHERE id=?", (job['id'],))
    try:
        req = ai.submit(ai.Submit(connection_id=connection, expected_version=row['version'],
                        request_id='auto_'+job['id']+'_'+job['stage'], kind='evidence', evidence_id=job['evidence_id'],
                        allow_cloud=policy['allow_cloud'], text='审核此屏幕事件的固定证据；缺少上下文请明确保留不确定。'),
                        guard=lambda: before_send(job, connection, row['version']))
    except HTTPException as exc:
        if exc.status_code == 429 and '任务已满' in str(exc.detail):
            db.execute('UPDATE auto_jobs SET status=? WHERE id=?', ('pending' if job['stage'] == 'local' else 'cloud_pending', job['id']))
        else:
            finish(job['id'], 'error', str(exc.detail))
        return
    except Exception:
        finish(job['id'], 'error', '自动请求未能完成提交；结果未知，请人工复核')
        return
    column = 'cloud_request' if job['stage'] == 'cloud' else 'local_request'
    db.execute(f"UPDATE auto_jobs SET status='waiting',{column}=?,reason='模型调用中' WHERE id=?", (req['id'], job['id']))


def before_send(job, connection, version):
    if time.time() > job['expires_at']:
        return '任务排队超时，未发送；证据保留待人工复核'
    reason = allowed(job)
    if reason:
        return reason
    row = db.query_one('SELECT version,body FROM ai_connections WHERE id=?', (connection,))
    if not row or row['version'] != version or not json.loads(row['body'])['enabled']:
        return '连接已修改或停用，排队请求未发送'
    return ''


def tick():
    now = time.time()
    # Recover closed events if a crash occurred between sealing and enqueueing.
    for row in db.query("SELECT e.id FROM events e JOIN source_profiles p ON p.source_id=e.source_id LEFT JOIN auto_jobs j ON j.event_id=e.id WHERE j.id IS NULL AND e.lifecycle='closed' AND e.expired=0 AND e.review_state IN ('pending','uncertain') AND e.sealed_at>? AND e.sealed_at>=p.updated_at AND json_extract(p.body,'$.ai.enabled')=1 ORDER BY e.id LIMIT 100", (now-3600,)):
        enqueue(row['id'])
    for job in db.query("SELECT * FROM auto_jobs WHERE status='waiting' ORDER BY created_at"):
        reconcile(job)
    db.execute("UPDATE auto_jobs SET status='expired',reason='排队超时；证据保留待人工复核',finished_at=? WHERE status IN ('pending','cloud_pending') AND expires_at<?", (now, now))
    # Oldest last-served source first prevents a busy source monopolizing the queue.
    jobs = db.query("SELECT j.* FROM auto_jobs j WHERE j.status IN ('pending','cloud_pending') ORDER BY COALESCE((SELECT MAX(r.created_at) FROM auto_jobs a JOIN ai_requests r ON r.id IN (a.local_request,a.cloud_request) WHERE a.source_id=j.source_id),0),j.created_at LIMIT 64")
    for job in jobs:
        dispatch(job)


def for_event(event_id):
    job = db.query_one('SELECT * FROM auto_jobs WHERE event_id=?', (event_id,))
    if not job:
        return None
    job.pop('policy')
    job['requests'] = [ai.get_request(job[k]) for k in ('local_request', 'cloud_request') if job[k]]
    return job


def status():
    return {'queue_limit': QUEUE_LIMIT, 'counts': {r['status']: r['n'] for r in db.query('SELECT status,COUNT(*) n FROM auto_jobs GROUP BY status')},
            'recent': db.query('SELECT id,event_id,source_id,status,outcome,reason,created_at FROM auto_jobs ORDER BY created_at DESC LIMIT 20')}
