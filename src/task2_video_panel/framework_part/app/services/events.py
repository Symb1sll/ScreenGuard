"""Bounded evidence segments; human verdicts apply only to sealed content."""
import json,time,threading,hashlib
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import cv2
from app.core import database as db
from app.services import evidence
_lock=threading.RLock()
_live={}

class EventAccumulator:
    def __init__(self,source_id,job_id,roi,roi_version,settings_version,cfg,model_id):
        self.source_id=source_id;self.job_id=job_id;self.roi=roi;self.roi_version=roi_version
        self.settings_version=settings_version;self.cfg=cfg;self.model_id=model_id
        self.active=None;self.previous=None;self.high={};self.episode_id='';self.clock_origin=None
    def pair(self,event_id,role,item,replace=False):
        if item is None:return
        raw,crop,t,n,captured_at=item
        for suffix,frame in (('original',raw),('roi',crop)):
            try:evidence.attach(event_id,role+'_'+suffix,frame,t,n,captured_at=captured_at,replace=replace)
            except OSError:
                db.execute("UPDATE events SET evidence_error='证据保存失败，请检查存储' WHERE id=?",(event_id,))
                break
    def close(self,after=None,continue_episode=False):
        with _lock:
            if self.active:
                a=self.active
                self.pair(a['id'],'peak',a['peak_item'],replace=True)
                self.pair(a['id'],'last',a['last_item'],replace=True)
                self.pair(a['id'],'after',after or a.get('after'))
                db.execute("UPDATE events SET lifecycle='closed',sealed_at=?,content_version=content_version+1 WHERE id=?",(time.time(),a['id']))
                _live.pop(a['id'],None);self.active=None
                try:
                    from app.services.auto_review import enqueue
                    enqueue(a['id'])
                except Exception:
                    __import__('logging').exception('Automatic review enqueue failed event_id=%s',a['id'])
            self.high={}
            if not continue_episode:self.episode_id=''
    def _audit(self,clock,crop):
        budget=self.cfg.get('audit_daily_per_source',24)
        if budget<=0:return None
        tz=timezone(timedelta(hours=8)) if self.cfg['timezone']=='Asia/Shanghai' else timezone.utc
        stamp=datetime.fromtimestamp(clock,tz);seconds=stamp.hour*3600+stamp.minute*60+stamp.second
        bucket=min(budget-1,int(seconds*budget/86400));day=stamp.date().isoformat()
        signature=hashlib.sha256((cv2.resize(crop,(16,16))//32).tobytes()).hexdigest()
        with db.transaction() as c:
            if c.execute('SELECT 1 FROM audit_buckets WHERE source_id=? AND day=? AND bucket=?',(self.source_id,day,bucket)).fetchone():return None
            if c.execute('SELECT 1 FROM audit_buckets WHERE source_id=? AND day=? AND signature=?',(self.source_id,day,signature)).fetchone():return None
            c.execute('INSERT INTO audit_buckets(source_id,day,bucket,signature) VALUES(?,?,?,?)',(self.source_id,day,bucket,signature))
        return day,bucket
    def consume(self,frame,crop,media_time,frame_no,scores,decision,captured_at=None):
        with _lock:
            if self.clock_origin is None:self.clock_origin=time.time()-media_time
            clock=captured_at if captured_at is not None else self.clock_origin+media_time
            item=(frame.copy(),crop.copy(),media_time,frame_no,clock)
            kind=decision['action'];labels=set(decision['labels']);a=self.active;audit=None
            if a and media_time-a['start']>=self.cfg.get('segment_seconds',60):
                self.close(continue_episode=True);a=None
            if kind=='normal':
                self.high={}
                if a:
                    a.setdefault('after',item)
                    if media_time-a['last_item'][2]>=self.cfg['merge_gap_seconds']:self.close()
                    self.previous=item;return
                audit=self._audit(clock,crop)
                if audit is None:self.previous=item;return
                kind='audit';decision=dict(decision,reason='每日预算抽检：时段首个不同的低风险画面；非均匀随机样本')
            group='unknown' if kind=='unknown' else 'audit' if kind=='audit' else 'risk'
            if a and (a['group']!=group or (group=='risk' and not labels.intersection(a['labels'])) or media_time-a['last_item'][2]>self.cfg['merge_gap_seconds']):
                self.close(item);a=None
            peak=max(scores.values(),default=0) if scores else 0
            if not a:
                if not self.episode_id:self.episode_id=uuid4().hex
                eid=db.execute('INSERT INTO events(source_id,job_id,roi_id,roi_version,settings_version,model_id,kind,labels,scores,reason,started_at,updated_at,media_start,media_end,peak,episode_id,content_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (self.source_id,self.job_id,self.roi['id'],self.roi_version,self.settings_version,self.model_id,'review' if kind=='alert' else kind,json.dumps(sorted(labels)),json.dumps(scores),decision['reason'],clock,time.time(),media_time,media_time,peak,self.episode_id,1))
                a={'id':eid,'group':group,'labels':labels,'scores':dict(scores),'peak':peak,'saved_peak':peak,'peak_item':item,'last_item':item,'checkpoint':media_time,'start':media_time,'alerted':set(),'context_count':0}
                self.active=a;_live[eid]=self
                db.execute('UPDATE events SET source_profile_version=? WHERE id=?',(self.cfg.get('_source_profile_version',0),eid))
                self.pair(eid,'before',self.previous);self.pair(eid,'first',item)
                self.pair(eid,'peak',item);self.pair(eid,'last',item)
                if audit:db.execute('UPDATE audit_buckets SET event_id=? WHERE source_id=? AND day=? AND bucket=?',(eid,self.source_id,*audit))
            else:
                a.pop('after',None);a['labels'].update(labels)
                for k,v in scores.items():a['scores'][k]=max(a['scores'].get(k,0),v)
                db.execute('UPDATE events SET samples=samples+1,updated_at=?,media_end=?,labels=?,scores=?,peak=MAX(peak,?),content_version=content_version+1 WHERE id=?',
                    (time.time(),media_time,json.dumps(sorted(a['labels'])),json.dumps(a['scores']),peak,a['id']))
                if peak>a['peak']:a['peak']=peak;a['peak_item']=item
                a['last_item']=item
                if media_time-a['checkpoint']>=self.cfg['evidence_interval_seconds'] or peak>=a['saved_peak']+.05:
                    self.pair(a['id'],'peak',a['peak_item'],replace=True);self.pair(a['id'],'last',item,replace=True)
                    a['checkpoint']=media_time;a['saved_peak']=a['peak']
            context_max=self.cfg.get('context_frames',8)
            if context_max and media_time-a['start'] >= (a['context_count']+1)*self.cfg.get('segment_seconds',60)/(context_max+1) and a['context_count']<context_max:
                a['context_count']+=1
                self.pair(a['id'],'context%02d'%a['context_count'],item)
            high=set(decision.get('high_labels',[])) if kind=='alert' else set()
            self.high={k:v for k,v in self.high.items() if k in high}
            for label in high:
                count,since=self.high.get(label,(0,media_time));self.high[label]=(count+1,since)
                if count+1<self.cfg['confirm_samples'] or media_time-since<self.cfg['confirm_seconds'] or label in a['alerted']:continue
                with db.transaction() as c:
                    prior=c.execute('SELECT last_at FROM alert_cooldowns WHERE source_id=? AND roi_id=? AND label=?',(self.source_id,self.roi['id'],label)).fetchone()
                    if prior and clock-prior['last_at']<self.cfg['cooldown_seconds']:continue
                    c.execute('INSERT INTO alert_cooldowns VALUES(?,?,?,?) ON CONFLICT(source_id,roi_id,label) DO UPDATE SET last_at=excluded.last_at',(self.source_id,self.roi['id'],label,clock))
                    c.execute("UPDATE events SET kind='alert',reason='逐类多帧高阈值确认；等待人工处理' WHERE id=?",(a['id'],))
                a['alerted'].add(label)
                from app.services.operations import emit
                emit('event:'+str(a['id']),'alert','存在经时序确认的风险片段，请及时复核',a['id'],self.source_id)
            if kind=='audit':self.close()
            self.previous=item

def freeze(event_id,content_version):
    with _lock:
        row=db.query_one('SELECT * FROM events WHERE id=?',(event_id,))
        if not row:raise KeyError('事件不存在')
        if row['expired']:raise ValueError('证据已过期')
        if row['content_version']!=content_version:raise RuntimeError('画面已更新，请重载后固定最新片段')
        if row['lifecycle']=='open':
            owner=_live.get(event_id)
            if owner:owner.close(continue_episode=True)
            else:db.execute("UPDATE events SET lifecycle='closed',sealed_at=?,content_version=content_version+1,reason=reason || '；固定已落盘证据，采集上下文可能不完整' WHERE id=?",(time.time(),event_id))
        return event_detail(event_id)

def event_detail(event_id):
    with _lock:
        e=db.query_one('SELECT e.*,s.name AS source_name FROM events e JOIN video_sources s ON s.id=e.source_id WHERE e.id=?',(event_id,))
        if not e:return None
        for k in ('labels','scores','review_labels'):e[k]=json.loads(e[k])
        e['evidence']=db.query("SELECT id,role,sha256,captured_at,written_at,media_time,frame_no FROM evidence WHERE event_id=? AND role NOT LIKE 'history_%' ORDER BY id",(event_id,))
        e['reviews']=db.query('SELECT id,result,actor,created_at,duration_ms,operation,undo_of FROM reviews WHERE event_id=? ORDER BY id DESC',(event_id,))
        for row in e['reviews']:row['result']=json.loads(row['result'])
        from app.services import auto_review,cases
        e['automatic_review']=auto_review.for_event(event_id)
        e['disposition']=cases.get(event_id)
        e['related_segments']=db.query("SELECT id,review_state,kind,media_start,media_end FROM events WHERE episode_id=? AND id!=? ORDER BY media_start LIMIT 50",(e['episode_id'],event_id)) if e['episode_id'] else []
        return e

def review(event_id,state,labels,note,version,duration_ms,content_version=None,actor='本机用户'):
    if state=='confirmed' and not labels:raise ValueError('确认风险需选择至少一类')
    if state=='normal':labels=[]
    with _lock,db.transaction() as c:
        row=c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
        if not row:raise KeyError('事件不存在')
        if row['expired']:raise ValueError('证据已过期，不能提交新的审核结论')
        if row['lifecycle']!='closed':raise RuntimeError('请先固定当前片段，审核只覆盖固定的证据范围')
        if row['review_version']!=version or (content_version is not None and row['content_version']!=content_version):raise RuntimeError('事件已被其他操作修改，请重新加载')
        previous={'state':row['review_state'],'labels':json.loads(row['review_labels']),'note':row['review_note'],'reviewed_at':row['reviewed_at']}
        assets=c.execute("SELECT id,sha256,media_time FROM evidence WHERE event_id=? AND role NOT LIKE 'history_%' ORDER BY id",(event_id,)).fetchall()
        if state in ('confirmed','normal'):
            usable=c.execute("SELECT path FROM evidence WHERE event_id=? AND role IN ('first_roi','peak_roi','last_roi')",(event_id,)).fetchall()
            if not usable or not any(evidence.resolve(a['path']).is_file() for a in usable):raise ValueError('缺少可查看的证据，请保留不确定结论并检查存储')
        result={'state':state,'labels':labels,'note':note,'content_version':row['content_version'],'media_start':row['media_start'],'media_end':row['media_end'],'evidence':[dict(a) for a in assets]}
        rid=c.execute('INSERT INTO reviews(event_id,previous,result,actor,created_at,duration_ms,operation) VALUES(?,?,?,?,?,?,?)',
            (event_id,json.dumps(previous),json.dumps(result,ensure_ascii=False),actor,time.time(),duration_ms,'review')).lastrowid
        c.execute('UPDATE events SET review_state=?,review_labels=?,review_note=?,reviewed_at=?,review_version=review_version+1 WHERE id=?',
            (state,json.dumps(labels),note,time.time(),event_id))
        if state=='confirmed' and (previous['state']!='confirmed' or set(previous['labels'])!=set(labels)):
            from app.services.cases import reopen
            reopen(c,event_id,actor)
    return {'review_id':rid,'version':version+1}

def undo(event_id,review_id,version,actor='本机用户'):
    with _lock,db.transaction() as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
        r=c.execute('SELECT * FROM reviews WHERE event_id=? ORDER BY id DESC LIMIT 1',(event_id,)).fetchone()
        if not e or not r:raise KeyError('审核记录不存在')
        if e['expired']:raise ValueError('证据已过期，不能撤销审核')
        if e['review_version']!=version or r['id']!=review_id or r['operation']!='review':raise RuntimeError('仅能撤销尚未被后续操作修改的最近一次审核')
        prev=json.loads(r['previous'])
        current={'state':e['review_state'],'labels':json.loads(e['review_labels']),'note':e['review_note'],'reviewed_at':e['reviewed_at']}
        c.execute('INSERT INTO reviews(event_id,previous,result,actor,created_at,duration_ms,operation,undo_of) VALUES(?,?,?,?,?,?,?,?)',
            (event_id,json.dumps(current),json.dumps(prev),actor,time.time(),0,'undo',review_id))
        c.execute('UPDATE events SET review_state=?,review_labels=?,review_note=?,reviewed_at=?,review_version=review_version+1 WHERE id=?',
            (prev['state'],json.dumps(prev['labels']),prev['note'],prev['reviewed_at'],event_id))
        if prev['state']=='confirmed' and (current['state']!='confirmed' or set(current['labels'])!=set(prev['labels'])):
            from app.services.cases import reopen
            reopen(c,event_id,actor)
    return {'version':version+1}

