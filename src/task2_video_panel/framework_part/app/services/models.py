import json,re
from threading import RLock
from app.core.config import DATA_DIR
from screen_guard_core import LABELS,ModelPackage
MODEL_DIR=DATA_DIR/'models'
MODEL_DIR.mkdir(exist_ok=True)
_lock=RLock();_cache={}
class DemoModel:
    id='demo-rules';validated=False
    meta={'model_id':id,'validation_status':'demo','preprocessing':'demo_color_rules','description':'颜色演示规则；不代表业务识别能力'}
    def predict(self,frame):
        from app.services.recognizer import FastPolicyRecognizer
        r=FastPolicyRecognizer().classify(frame,context='')
        scores={k:.12 for k in LABELS}
        mapping={'涉黄/低俗内容':'sexual_vulgar','涉赌/广告风险':'gambling','暴力/恐怖内容':'violence_terror','隐私泄露':'privacy_leak'}
        if r.subtype in mapping:scores[mapping[r.subtype]]=float(r.risk_score)
        return scores
def get_model(model_id):
    if model_id=='demo-rules':return DemoModel()
    if not re.fullmatch('[a-zA-Z0-9_-]{1,80}',model_id):raise ValueError('模型 ID 无效')
    with _lock:
        if model_id not in _cache:
            package=ModelPackage(MODEL_DIR/model_id)
            if package.id!=model_id:raise ValueError('模型目录与 ID 不一致')
            _cache[model_id]=package
        return _cache[model_id]
def list_models():
    rows=[DemoModel.meta]
    for directory in sorted(MODEL_DIR.iterdir()):
        if not directory.is_dir():continue
        try:
            meta=json.loads((directory/'manifest.json').read_text(encoding='utf8'))
            rows.append({k:meta.get(k) for k in ('model_id','validation_status','preprocessing','description','sha256','validation_report')})
        except (OSError,ValueError):rows.append({'model_id':directory.name,'validation_status':'invalid','description':'模型包不完整'})
    return rows

def validate_model(model_id):
    from threading import Event
    from types import SimpleNamespace
    from app.services.inference import TimedModel
    model=DemoModel() if model_id=='demo-rules' else TimedModel(model_id,Event())
    try:return SimpleNamespace(id=model.id,validated=model.validated,meta=model.meta)
    finally:
        if isinstance(model,TimedModel):model.close()

def validate_package(directory):
    from threading import Event
    from app.services.inference import TimedModel
    model=TimedModel({'package_path':str(directory)},Event())
    try:return model.meta
    finally:model.close()

