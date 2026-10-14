"""Offline full-video acceptance replay with the same event accumulator as the service.

The manifest freezes video hashes and exhaustive interval annotations. It never activates a model.
Outputs and its isolated SQLite/evidence are stored in a NEW --out directory.
"""
import argparse,json,os,sys,time,hashlib,random,math
from pathlib import Path
from uuid import uuid4

def match_events(predicted,truth,label,min_iou=.3):
    p=[x for x in predicted if label in x['labels']]
    t=[x for x in truth if label in x['labels']]
    pairs=[]
    for i,a in enumerate(p):
        for j,b in enumerate(t):
            intersection=max(0,min(a['end'],b['end'])-max(a['start'],b['start']))
            union=max(a['end'],b['end'])-min(a['start'],b['start'])
            iou=intersection/union if union>0 else 0
            if iou>=min_iou:pairs.append((iou,i,j))
    used_p=set();used_t=set()
    for _,i,j in sorted(pairs,reverse=True):
        if i not in used_p and j not in used_t:used_p.add(i);used_t.add(j)
    tp=len(used_p)
    return {'tp':tp,'fp':len(p)-tp,'fn':len(t)-tp,'precision':tp/len(p) if p else None,'recall':tp/len(t) if t else None}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--manifest',required=True)
    ap.add_argument('--baseline',required=True,help='Model package directory or demo-rules')
    ap.add_argument('--candidate',required=True,help='Model package directory or demo-rules')
    ap.add_argument('--baseline-settings',help='Settings JSON or API response containing settings')
    ap.add_argument('--candidate-settings')
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    project=Path(__file__).resolve().parents[2]
    manifest_path=Path(args.manifest).resolve()
    manifest=json.loads(manifest_path.read_text(encoding='utf8'))
    if manifest.get('split')!='test':raise SystemExit('完整验收回放必须使用冻结的 test；阈值选择使用独立 val')
    clips=manifest.get('clips',[])
    if not clips:raise SystemExit('至少需要一个视频')
    out=Path(args.out).resolve()
    if out.exists():raise SystemExit('输出目录已存在，请用新的目录')
    out.mkdir(parents=True)
    os.environ.update(SCREEN_GUARD_BASE_DIR=str(project),SCREEN_GUARD_DATA_DIR=str(out/'runtime'),PYTHONDONTWRITEBYTECODE='1')
    sys.path[:0]=[str(project),str(project/'framework_part')]
    import cv2,numpy as np
    from screen_guard_core import ModelPackage,LABELS,decide,crop_screen,assess_quality,digest,validate_points
    from app.core import database as db
    db.init_db()
    from app.services import settings,events
    from app.services.models import DemoModel
    settings.current()
    frozen=[]
    ids=set()
    for item in clips:
        clip=dict(item)
        if not clip.get('id') or clip['id'] in ids:raise ValueError('视频 ID 必须唯一')
        ids.add(clip['id'])
        path=(manifest_path.parent/clip['path']).resolve()
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        if sha!=clip.get('sha256'):raise ValueError('视频哈希不一致：'+clip['id'])
        cap=cv2.VideoCapture(str(path));fps=cap.get(cv2.CAP_PROP_FPS);frames=cap.get(cv2.CAP_PROP_FRAME_COUNT);cap.release()
        if not math.isfinite(fps) or fps<=0 or frames<=0:raise ValueError('无法确认视频时长：'+clip['id'])
        duration=frames/fps
        points=clip.get('points',[[0,0],[1,0],[1,1],[0,1]]);validate_points(points)
        truth=clip.get('truth',[])
        for e in truth:
            if not (0<=e['start']<e['end']<=duration+.01) or not e['labels'] or any(k not in LABELS for k in e['labels']):raise ValueError('事件标注无效')
        clip.update(path=str(path),sha256=sha,fps=fps,frames=int(frames),duration=duration,points=points,truth=truth)
        frozen.append(clip)
    report={'scope':'完整文件回放，使用在线相同的预处理、画质判定、双阈值和事件聚合；不验证 RTSP 或真实墙钟延迟',
            'data_kind':manifest.get('data_kind','unknown'),'split':'test','manifest_hash':digest(manifest),
            'clips':frozen,'settings':{},'sides':{},'activated':False}
    for side in ('baseline','candidate'):
        model_arg=getattr(args,side);model=DemoModel() if model_arg=='demo-rules' else ModelPackage(model_arg)
        settings_arg=getattr(args,side+'_settings')
        body=json.loads(Path(settings_arg).read_text(encoding='utf8')) if settings_arg else settings.Settings().model_dump()
        body=body.get('settings',body);body['model_id']=model.id
        cfg=settings.Settings.model_validate(body).model_dump()
        report['settings'][side]=cfg
        config_row=settings.save(cfg,settings.current()['version'])
        valid=model.validated and digest(cfg['policies'])==digest(model.meta.get('policies',{}))
        rows=[];latencies=[];decoded=sampled=failed=0;review_total=alert_total=0
        totals={k:{'tp':0,'fp':0,'fn':0,'alert_tp':0,'alert_fp':0} for k in LABELS}
        labeled_hours=0.;all_hours=0.;complete=True;false_alert_events=0
        for clip in frozen:
            random.seed(int(clip['sha256'][:16],16))
            sid=db.execute('INSERT INTO video_sources(name,source_type,source_value) VALUES(?,?,?)',(clip['id'],'local',clip['path']))
            a=events.EventAccumulator(sid,uuid4().hex,{'id':'screen-1'},0,config_row['version'],cfg,model.id)
            cap=cv2.VideoCapture(clip['path']);n=0;next_sample=0;local_sampled=0;begin=time.monotonic()
            try:
                while True:
                    ok,frame=cap.read()
                    if not ok:break
                    t=n/clip['fps'];n+=1
                    if t+1e-6<next_sample:continue
                    next_sample=t+cfg['sample_seconds'];sampled+=1;local_sampled+=1
                    crop=crop_screen(frame,clip['points']);start=time.perf_counter()
                    try:
                        scores=model.predict(crop)
                        decision=decide(scores,cfg['policies'],model_validated=valid,auto_alerts=cfg['auto_alerts'],quality_issue=assess_quality(crop))
                    except Exception as exc:
                        failed+=1;scores={};decision=decide({},cfg['policies'],error=type(exc).__name__)
                    latencies.append((time.perf_counter()-start)*1000)
                    a.consume(frame,crop,t,n,scores,decision)
            finally:cap.release();a.close()
            decoded+=n
            success=n==clip['frames'];complete=complete and success
            ev=db.query('SELECT id,kind,labels,media_start,media_end,reason FROM events WHERE source_id=?',(sid,))
            predictions=[{'id':e['id'],'kind':e['kind'],'labels':json.loads(e['labels']),'start':e['media_start'],'end':min(clip['duration'],e['media_end']+cfg['sample_seconds']),'reason':e['reason']} for e in ev]
            detected=[e for e in predictions if e['kind'] in ('review','alert')]
            alarms=[e for e in predictions if e['kind']=='alert']
            review_total+=len(predictions);alert_total+=len(alarms)
            all_hours+=clip['duration']/3600
            row={'id':clip['id'],'decoded':n,'expected_frames':clip['frames'],'sampled':local_sampled,'complete':success,'elapsed_seconds':time.monotonic()-begin,'events':predictions}
            if clip.get('exhaustively_annotated') is True and success:
                labeled_hours+=clip['duration']/3600
                per_label={}
                for k in LABELS:
                    det=match_events(detected,clip['truth'],k);alarm=match_events(alarms,clip['truth'],k)
                    per_label[k]={'detection':det,'alert':alarm}
                    for key in ('tp','fp','fn'):totals[k][key]+=det[key]
                    totals[k]['alert_tp']+=alarm['tp'];totals[k]['alert_fp']+=alarm['fp']
                # One alert event is false if none of its labels overlaps a truth interval at the IoU gate.
                for alarm in alarms:
                    if not any(match_events([alarm],clip['truth'],k)['tp'] for k in alarm['labels']):false_alert_events+=1
                row['per_label']=per_label
            rows.append(row)
        for k,m in totals.items():
            m['event_recall']=m['tp']/(m['tp']+m['fn']) if m['tp']+m['fn'] else None
            m['alert_precision']=m['alert_tp']/(m['alert_tp']+m['alert_fp']) if m['alert_tp']+m['alert_fp'] else None
        report['sides'][side]={'model_id':model.id,'model_hash':model.meta.get('sha256',model.id),'policy_validated':valid,
            'complete':complete,'camera_hours':all_hours,'fully_labeled_camera_hours':labeled_hours,'decoded_frames':decoded,'sampled_frames':sampled,
            'inference_failures':failed,'inference_success_ratio':(sampled-failed)/sampled if sampled else None,
            'review_events':review_total,'alert_events':alert_total,'review_events_per_camera_day_estimate':review_total/all_hours*24 if all_hours else None,
            'false_alert_events_per_camera_hour':false_alert_events/labeled_hours if labeled_hours else None,
            'per_label':totals,'latency_ms':{'mean':float(np.mean(latencies)) if latencies else None,'p95':float(np.percentile(latencies,95)) if latencies else None},
            'human_review_seconds_mean':None,'human_review_note':'离线回放无人审核；工作台另行统计实际审核耗时',
            'rows':rows}
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'report':str(out/'report.json'),'data_kind':report['data_kind'],'complete':all(s['complete'] for s in report['sides'].values())},ensure_ascii=False))
if __name__=='__main__':main()

