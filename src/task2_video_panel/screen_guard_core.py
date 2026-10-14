"""Shared image preparation, model contract and explicit risk policy."""
from __future__ import annotations
import hashlib,json,math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageOps
LABELS={'sexual_vulgar':'涉黄/低俗','gambling':'涉赌','drugs_contraband':'涉毒/违禁品','violence_terror':'暴力/恐怖','privacy_leak':'隐私泄露'}
PREPROCESSING='letterbox_rgb_v1'
LEGACY_PREPROCESSING='legacy_square_rgb_v0'
MEAN=np.array([.485,.456,.406],dtype=np.float32)
STD=np.array([.229,.224,.225],dtype=np.float32)
def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def prepare_image(image,size,version=PREPROCESSING):
    image=ImageOps.exif_transpose(image).convert('RGB')
    if version==PREPROCESSING:
        return ImageOps.pad(image,(size,size),method=Image.Resampling.BILINEAR,color=(114,114,114),centering=(.5,.5))
    if version==LEGACY_PREPROCESSING:return image.resize((size,size),Image.Resampling.BILINEAR)
    raise ValueError('不支持的预处理版本')
def tensor_array(image,size,version=PREPROCESSING):
    rgb=np.asarray(prepare_image(image,size,version),dtype=np.float32)/255.
    return np.ascontiguousarray(((rgb-MEAN)/STD).transpose(2,0,1)[None])
def validate_points(points):
    a=np.asarray(points,dtype=np.float32)
    if a.shape!=(4,2) or not np.isfinite(a).all() or (a<0).any() or (a>1).any():
        raise ValueError('屏幕区域需要四个 0–1 范围内的坐标')
    if not cv2.isContourConvex(a) or abs(cv2.contourArea(a))<.002:raise ValueError('四点需按边缘顺序排列，不能交叉或面积过小')
    if cv2.contourArea(a,oriented=True)<=0:raise ValueError('请按左上、右上、右下、左下顺序标定')
    return a
def crop_screen(frame,points):
    a=validate_points(points);h,w=frame.shape[:2]
    src=a*np.array([w-1,h-1],np.float32)
    width=max(2,round(max(np.linalg.norm(src[1]-src[0]),np.linalg.norm(src[2]-src[3]))))
    height=max(2,round(max(np.linalg.norm(src[3]-src[0]),np.linalg.norm(src[2]-src[1]))))
    scale=min(1.,1920/max(width,height));width,height=max(2,round(width*scale)),max(2,round(height*scale))
    dst=np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]],np.float32)
    return cv2.warpPerspective(frame,cv2.getPerspectiveTransform(src,dst),(width,height))
def decide(scores,policies,*,model_validated=False,auto_alerts=False,error='',quality_issue=''):
    if error:return {'action':'unknown','labels':[],'high_labels':[],'reason':error}
    if not isinstance(scores,dict) or set(scores)!=set(LABELS):
        return {'action':'unknown','labels':[],'high_labels':[],'reason':'模型标签不完整'}
    if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=1 for x in scores.values()):
        return {'action':'unknown','labels':[],'high_labels':[],'reason':'模型分数无效'}
    labels=[k for k in LABELS if policies[k]['enabled'] and scores[k]>=policies[k]['review']]
    high=[k for k in labels if scores[k]>=policies[k]['alert']]
    if quality_issue:return {'action':'unknown','labels':labels,'high_labels':[],'reason':quality_issue}
    if not labels:return {'action':'normal','labels':[],'high_labels':[],'reason':'未达到复核阈值；仍安排低分抽检'}
    allowed=model_validated and auto_alerts
    action='alert' if high and allowed else 'review'
    reason='达到告警阈值，等待时序确认' if action=='alert' else ('模型尚未通过业务验证，保留人工复核' if not model_validated else '自动告警关闭，保留人工复核' if high and not auto_alerts else '达到复核阈值')
    return {'action':action,'labels':labels,'high_labels':high if allowed else [],'reason':reason}

def assess_quality(frame):
    """Flag extremely low-detail images for review; never discard or call them safe."""
    h,w=frame.shape[:2]
    if min(h,w)<32:return '屏幕区域分辨率过低，无法可靠判断'
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    if max(h,w)>640:gray=cv2.resize(gray,(round(w*640/max(h,w)),round(h*640/max(h,w))))
    if float(cv2.Laplacian(gray,cv2.CV_64F).var())<2:
        return '画面细节不足或严重模糊，需人工确认'
    return ''

class ModelPackage:
    def __init__(self,directory):
        self.directory=Path(directory);self.meta=json.loads((self.directory/'manifest.json').read_text(encoding='utf8'));m=self.meta
        if m.get('format_version')!=1 or m.get('labels')!=list(LABELS):raise ValueError('模型包格式或标签顺序不兼容')
        if m.get('preprocessing') not in (PREPROCESSING,LEGACY_PREPROCESSING):raise ValueError('模型包预处理版本不兼容')
        if m.get('output')!='logits':raise ValueError('模型包必须声明输出为 logits')
        self.size=int(m['image_size'])
        if not 32<=self.size<=2048:raise ValueError('模型输入尺寸无效')
        if hashlib.sha256((self.directory/'model.onnx').read_bytes()).hexdigest()!=m.get('sha256'):raise ValueError('模型权重哈希不一致')
        self.id=m['model_id']
        report=m.get('validation_report') or {}
        self.validated=m.get('validation_status')=='validated' and report.get('approved_for_alerts') is True and report.get('data_kind')=='real' and bool(m.get('policies'))
        if self.validated:
            self.validated=all(report.get('classes',{}).get(k,{}).get('eligible') is True for k,v in m['policies'].items() if v.get('enabled'))
        import onnxruntime as ort
        opts=ort.SessionOptions();opts.intra_op_num_threads=2
        self.session=ort.InferenceSession(str(self.directory/'model.onnx'),sess_options=opts,providers=['CPUExecutionProvider'])
        inp=self.session.get_inputs()
        if len(inp)!=1 or inp[0].type!='tensor(float)' or inp[0].shape[1:]!=[3,self.size,self.size]:raise ValueError('ONNX 输入不符合模型包契约')
        self.input_name=inp[0].name
        self.predict(np.zeros((self.size,self.size,3),np.uint8))
    def predict(self,frame):
        x=tensor_array(Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)),self.size,self.meta['preprocessing'])
        logits=np.asarray(self.session.run(None,{self.input_name:x})[0])
        if logits.shape!=(1,len(LABELS)) or not np.isfinite(logits).all():raise ValueError('模型输出形状或数值无效')
        probs=1/(1+np.exp(-np.clip(logits[0],-80,80)))
        return {k:float(probs[i]) for i,k in enumerate(LABELS)}

