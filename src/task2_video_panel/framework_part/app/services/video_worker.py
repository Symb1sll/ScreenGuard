from concurrent.futures import ThreadPoolExecutor
from threading import Event,Lock
import multiprocessing as mp
import queue,time,logging,json
from uuid import uuid4
import cv2
from app.core import database as db
from app.services.settings import current
from app.services import source_profiles
from app.services.regions import get_regions
from app.services.models import get_model
from app.services.events import EventAccumulator
from app.services.capture import capture_worker
from app.services.inference import TimedModel
from screen_guard_core import decide,crop_screen,digest,assess_quality
logger=logging.getLogger(__name__)
class JobManager:
    def __init__(self):
        self.executor=ThreadPoolExecutor(max_workers=4,thread_name_prefix='video')
        self.stop_flags={};self.lock=Lock();self.latest={};self.processes={};self.restart_after={}
    def start(self,source_id):
        with self.lock:
            if source_id in self.stop_flags:return False
            if len(self.stop_flags)>=4:raise ValueError('最多同时运行四路视频')
            source=db.query_one('SELECT * FROM video_sources WHERE id=?',(source_id,))
            if not source or source['archived']:raise ValueError('视频源不存在或已归档')
            effective=source_profiles.effective(source_id)
            if source['source_type']!='local' and not source_profiles.within_schedule(effective['profile'],effective['settings']['timezone']):
                db.execute("UPDATE video_sources SET status='scheduled',desired_running=1,inference_state='idle',last_message='等待计划运行时段' WHERE id=?",(source_id,))
                return True
            flag=Event();job=uuid4().hex;self.stop_flags[source_id]=flag
            db.execute("UPDATE video_sources SET status='starting',job_id=?,inference_state='loading',last_message='正在启动',desired_running=? WHERE id=?",(job,int(source['source_type']!='local'),source_id))
            try:self.executor.submit(self._run,source_id,flag,job)
            except BaseException:self.stop_flags.pop(source_id,None);raise
            return True
    def stop(self,source_id,preserve_desired=False):
        with self.lock:
            if not preserve_desired:
                db.execute("UPDATE video_sources SET desired_running=0,status=CASE WHEN status='scheduled' THEN 'stopped' ELSE status END WHERE id=?",(source_id,))
            flag=self.stop_flags.get(source_id)
            if flag:
                flag.set();db.execute("UPDATE video_sources SET status='stopping',last_message='正在停止采集' WHERE id=?",(source_id,))
            return bool(flag)
    def get_frame(self,source_id):
        with self.lock:
            entry=self.latest.get(source_id)
            return (entry[0].copy(),entry[1]) if entry else None
    def _run(self,source_id,stop_flag,job):
        status='finished';message='文件检测完成';accumulators=[];process=None;model=None
        try:
            source=db.query_one('SELECT * FROM video_sources WHERE id=?',(source_id,))
            if not source:return
            effective=source_profiles.effective(source_id);cfg=effective['settings'];cfgrow={'version':effective['settings_version']}
            cfg['_source_profile_version']=effective['profile_version'];regions=get_regions(source_id)
            db.execute('UPDATE video_sources SET profile_version=?,running_config=?,last_inference_at=NULL,inference_ms=NULL,queue_lag_seconds=NULL,dropped_frames=0 WHERE id=?',(effective['profile_version'],json.dumps(cfg),source_id))
            model=get_model(cfg['model_id']) if cfg['model_id']=='demo-rules' else TimedModel(cfg['model_id'],stop_flag)
            is_stream=source['source_type']!='local'
            policy_validated=model.validated and digest(cfg['policies'])==digest(model.meta.get('policies',{}))
            accumulators=[EventAccumulator(source_id,job,r,regions['version'],cfgrow['version'],cfg,model.id) for r in regions['regions']]
            db.execute("UPDATE video_sources SET settings_version=?,processed=0,last_message='模型已加载',inference_state=? WHERE id=?",(cfgrow['version'],'ready' if policy_validated else 'unvalidated',source_id))
            retry=0;processed=0;stream_origin=time.monotonic();expected_shape=None
            while not stop_flag.is_set():
                context=mp.get_context('spawn');output=context.Queue(maxsize=2);child_stop=context.Event()
                process=context.Process(target=capture_worker,args=(int(source['source_value']) if source['source_type']=='webcam' else source['source_value'],is_stream,cfg['sample_seconds'],output,child_stop),daemon=True)
                process.start()
                with self.lock:self.processes[source_id]=process
                disconnected=False;last_received=time.monotonic()
                try:
                    while not stop_flag.is_set():
                        try:packet=output.get(timeout=.25)
                        except queue.Empty:
                            if not process.is_alive():disconnected=True;message='采集进程退出';break
                            if time.monotonic()-last_received>max(10,cfg['sample_seconds']+10):disconnected=True;message='读取超时，正在回收采集进程';break
                            continue
                        tag=packet[0]
                        if tag!='frame':
                            disconnected=tag!='eof'
                            if tag=='error':message=packet[1]
                            break
                        _,frame_no,media_time,captured_at,frame,*extra=packet;last_received=time.monotonic();retry=0
                        dropped=extra[0] if extra else 0
                        db.execute('UPDATE video_sources SET last_frame_at=?,queue_lag_seconds=?,dropped_frames=? WHERE id=? AND job_id=?',(captured_at,max(0,time.time()-captured_at),dropped,source_id,job))
                        infer_started=time.monotonic()
                        t=time.monotonic()-stream_origin if is_stream else media_time
                        h,w=frame.shape[:2]
                        with self.lock:self.latest[source_id]=(frame.copy(),captured_at)
                        if expected_shape and expected_shape!=(w,h):
                            raise ValueError('视频分辨率变化，请检查屏幕标定并重新启动')
                        expected_shape=(w,h);inference_state='ready' if policy_validated else 'unvalidated'
                        info='采集中；模型或阈值尚未业务验证' if not policy_validated else '采集与推理正常'
                        for r,a in zip(regions['regions'],accumulators):
                            crop=crop_screen(frame,r['points'])
                            try:
                                scores=model.predict(crop)
                                decision=decide(scores,cfg['policies'],model_validated=policy_validated,auto_alerts=cfg['auto_alerts'],quality_issue=assess_quality(crop))
                            except Exception as exc:
                                scores={};decision=decide({},cfg['policies'],error='推理异常：'+type(exc).__name__)
                                inference_state='error';info='推理异常，已进入待复核'
                            a.consume(frame,crop,t,frame_no,scores,decision,captured_at=captured_at if is_stream else None)
                        if inference_state!='error':
                            db.execute('UPDATE video_sources SET last_inference_at=?,inference_ms=? WHERE id=? AND job_id=?',(time.time(),(time.monotonic()-infer_started)*1000,source_id,job))
                        processed+=1
                        db.execute("UPDATE video_sources SET status='running',last_frame_at=?,frame_width=?,frame_height=?,processed=?,inference_state=?,last_message=? WHERE id=? AND job_id=?",
                            (captured_at,w,h,processed,inference_state,info,source_id,job))
                        if getattr(model,'broken',False):raise ValueError('推理超时或进程异常，已保存无法判断片段；等待恢复')
                finally:
                    child_stop.set();process.join(timeout=.7)
                    if process.is_alive():process.terminate();process.join(timeout=1)
                    output.close();output.cancel_join_thread()
                    with self.lock:self.processes.pop(source_id,None)
                if stop_flag.is_set():break
                if not is_stream:
                    if disconnected:status='error'
                    else:message=f'文件检测完成，共处理 {processed} 个采样帧'
                    break
                for a in accumulators:a.close()
                retry+=1;delay=min(30,2**min(retry,5))
                db.execute("UPDATE video_sources SET status='reconnecting',inference_state='waiting',last_message=? WHERE id=? AND job_id=?",(f'视频流断开，{delay} 秒后重连',source_id,job))
                stop_flag.wait(delay)
        except Exception as exc:
            status='error';message=str(exc) if isinstance(exc,ValueError) else '任务异常：'+type(exc).__name__
            logger.exception('Video job failed source_id=%s',source_id)
        finally:
            if isinstance(model,TimedModel):model.close()
            if process is not None and process.is_alive():process.terminate();process.join(timeout=1)
            for a in accumulators:
                try:a.close()
                except Exception:logger.exception('Finalize event failed source_id=%s',source_id)
            if stop_flag.is_set():status='stopped';message='采集已停止'
            with self.lock:
                db.execute('UPDATE video_sources SET status=?,last_message=? WHERE id=? AND job_id=?',(status,message,source_id,job))
                if self.stop_flags.get(source_id) is stop_flag:self.stop_flags.pop(source_id,None)
                self.processes.pop(source_id,None)
                self.latest.pop(source_id,None)
                self.restart_after[source_id]=time.monotonic()+30
    def restore(self):
        auto_restore=current()['settings'].get('auto_restore_sources',True)
        rows=db.query("SELECT id,status FROM video_sources WHERE desired_running=1 AND archived=0 AND source_type!='local' ORDER BY id")
        for row in rows:
            effective=source_profiles.effective(row['id'])
            if not source_profiles.within_schedule(effective['profile'],effective['settings']['timezone']):
                self.stop(row['id'],preserve_desired=True)
                with self.lock:
                    if row['id'] not in self.stop_flags:db.execute("UPDATE video_sources SET status='scheduled',inference_state='idle',last_message='等待计划运行时段' WHERE id=?",(row['id'],))
                continue
            if not auto_restore and row['status'] not in ('running','starting','scheduled'):continue
            if time.monotonic()<self.restart_after.get(row['id'],0):continue
            try:self.start(row['id'])
            except ValueError:continue
    def shutdown(self):
        with self.lock:
            for flag in self.stop_flags.values():flag.set()
        self.executor.shutdown(wait=True,cancel_futures=True)
manager=JobManager()

