"""Spawn-safe capture process; the parent can reclaim a blocked decoder."""
import math,time,queue,os
def capture_worker(source,is_stream,sample_seconds,output,stop):
    import cv2
    cap=None;dropped=0
    def send(message):
        nonlocal dropped
        while not stop.is_set():
            try:
                packet=(*message,dropped) if message[0]=='frame' else message
                output.put(packet,timeout=.2);return
            except queue.Full:
                if is_stream:
                    try:output.get_nowait();dropped+=1
                    except queue.Empty:pass
    try:
        if isinstance(source,int):
            cap=cv2.VideoCapture(source,cv2.CAP_DSHOW if os.name=='nt' else cv2.CAP_V4L2)
        elif is_stream:
            cap=cv2.VideoCapture(source,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,5000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,5000])
        else:cap=cv2.VideoCapture(source)
        if not cap.isOpened():send(('error','视频源无法打开'));return
        fps=cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps<=0:fps=25.
        count=0;next_sample=0.;origin=time.monotonic()
        while not stop.is_set():
            ok,frame=cap.read()
            if not ok:send(('disconnected' if is_stream else 'eof',));return
            count+=1
            t=time.monotonic()-origin if is_stream else (count-1)/fps
            if t+1e-6<next_sample:continue
            next_sample=t+sample_seconds
            send(('frame',count,t,time.time(),frame))
    except Exception as exc:send(('error','采集失败：'+type(exc).__name__))
    finally:
        if cap is not None:cap.release()

