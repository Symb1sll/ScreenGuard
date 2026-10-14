"""Frozen event-evidence comparisons. Candidate results never change live settings."""
import json,time,hashlib,threading
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import cv2,numpy as np
from app.core import database as db
from app.services import evidence
from app.services.settings import Settings,current
from app.services.models import get_model,validate_model
from app.services.inference import TimedModel
from screen_guard_core import LABELS,decide,digest
executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='comparison')
_lock=threading.Lock()
stop=threading.Event()
def metrics(rows,side):
    labeled=[r for r in rows if r['truth_state'] in ('confirmed','normal') and not r[side].get('error')]
    output={}
    for label in LABELS:
        tp=fp=fn=0
        for r in labeled:
            true=label in r['truth_labels'];pred=label in r[side]['decision']['high_labels']
            tp+=int(true and pred);fp+=int(not true and pred);fn+=int(true and not pred)
        output[label]={'tp':tp,'fp':fp,'fn':fn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None}
    return {'labeled':len(labeled),'per_label':output}
def submit(candidate_model,candidate_settings):
    cfg=current();candidate=Settings.model_validate(candidate_settings).model_dump()
    metadata={mid:validate_model(mid).meta for mid in {candidate_model,cfg['settings']['model_id']}}
    with _lock:
        if db.query_one("SELECT id FROM comparisons WHERE status IN ('queued','running')"):raise ValueError('已有对照任务正在运行')
        samples=db.query("""SELECT e.id,e.review_state,e.review_labels,v.path,v.sha256,evidence_id FROM events e JOIN
          (SELECT event_id,MIN(id) AS evidence_id FROM evidence WHERE role='first_roi' GROUP BY event_id) chosen ON chosen.event_id=e.id
          JOIN evidence v ON v.id=chosen.evidence_id WHERE e.expired=0 AND e.lifecycle='closed' ORDER BY e.id LIMIT 200""")
        if not samples:raise ValueError('需要至少一个已完成且证据仍保留的事件')
        spec={'baseline_model':cfg['settings']['model_id'],'candidate_model':candidate_model,
              'baseline_settings':cfg['settings'],'candidate_settings':candidate,'samples':samples,
              'sample_set_hash':digest(samples),'settings_version':cfg['version']}
        for side,mid in [('baseline',spec['baseline_model']),('candidate',candidate_model)]:
            spec[side+'_model_hash']=metadata[mid].get('sha256',mid)
        job=uuid4().hex
        db.execute('INSERT INTO comparisons(id,created_at,status,spec) VALUES(?,?,?,?)',(job,time.time(),'queued',json.dumps(spec,ensure_ascii=False)))
        executor.submit(run,job,spec)
    return job
def run(job,spec):
    db.execute("UPDATE comparisons SET status='running' WHERE id=?",(job,))
    rows=[];started=time.monotonic();loaded={}
    try:
        for mid in {spec['baseline_model'],spec['candidate_model']}:
            loaded[mid]=get_model(mid) if mid=='demo-rules' else TimedModel(mid,stop)
        for item in spec['samples']:
            if stop.is_set():raise RuntimeError('服务停止，对照已中断')
            row={'event_id':item['id'],'truth_state':item['review_state'],'truth_labels':json.loads(item['review_labels'])}
            for side in ('baseline','candidate'):
                begin=time.perf_counter()
                try:
                    path=evidence.resolve(item['path']);data=path.read_bytes()
                    if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('证据哈希不一致')
                    frame=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
                    if frame is None:raise ValueError('证据无法解码')
                    model=loaded[spec[side+'_model']];scores=model.predict(frame)
                    cfg=spec[side+'_settings']
                    # Compare threshold candidates even for an unvalidated package; never emit live alerts.
                    decision=decide(scores,cfg['policies'],model_validated=True,auto_alerts=True)
                    row[side]={'scores':scores,'decision':decision,'latency_ms':round((time.perf_counter()-begin)*1000,2),'model_validated':model.validated}
                except Exception as exc:row[side]={'error':type(exc).__name__+': '+str(exc)}
            rows.append(row)
        common=[r for r in rows if not r['baseline'].get('error') and not r['candidate'].get('error')]
        report={'sample_set_hash':spec['sample_set_hash'],'total':len(rows),'paired_success':len(common),
                'elapsed_seconds':round(time.monotonic()-started,2),'rows':rows,
                'scope':'事件首帧候选阈值对照；高阈值数量为模拟结果，未应用业务准入与连续确认，不会产生实际告警；不是完整时序验收，不代表部署精度；未标注样本只统计数量'}
        for side in ('baseline','candidate'):
            report[side]={'errors':sum(bool(r[side].get('error')) for r in rows),
                          'counts':{kind:sum(r[side].get('decision',{}).get('action')==kind for r in rows) for kind in ('normal','review','alert','unknown')},
                          'metrics_on_common_labeled':metrics(common,side)}
        db.execute("UPDATE comparisons SET status='completed',report=? WHERE id=?",(json.dumps(report,ensure_ascii=False),job))
    except Exception as exc:db.execute("UPDATE comparisons SET status='error',error=? WHERE id=?",(type(exc).__name__+': '+str(exc),job))
    finally:
        for model in loaded.values():
            if isinstance(model,TimedModel):model.close()

