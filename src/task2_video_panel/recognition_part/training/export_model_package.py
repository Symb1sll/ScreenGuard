"""Export an immutable self-contained ONNX package for the workbench."""
import argparse,json,hashlib,sys
from pathlib import Path
import torch
from multilabel_common import load_checkpoint
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from screen_guard_core import LABELS,LEGACY_PREPROCESSING
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--weights',required=True);ap.add_argument('--out',required=True);ap.add_argument('--model-id',required=True)
    ap.add_argument('--policy-report');ap.add_argument('--description',default='候选模型；尚未业务验收')
    args=ap.parse_args()
    model,labels,arch,size,thresholds,ckpt=load_checkpoint(args.weights,'cpu')
    if labels!=list(LABELS):raise SystemExit('标签顺序必须与工作台五类契约一致')
    out=Path(args.out)
    if out.exists():raise SystemExit('模型包目录已存在，请使用新版本目录')
    out.mkdir(parents=True)
    torch.onnx.export(model,torch.zeros(1,3,size,size),str(out/'model.onnx'),input_names=['image'],output_names=['logits'],dynamic_axes={'image':{0:'batch'},'logits':{0:'batch'}},opset_version=18,dynamo=False,external_data=False)
    policy_report=json.loads(Path(args.policy_report).read_text(encoding='utf8')) if args.policy_report else {}
    meta={'format_version':1,'model_id':args.model_id,'labels':labels,'arch':arch,'image_size':size,
          'preprocessing':ckpt.get('preprocessing',LEGACY_PREPROCESSING),'output':'logits',
          'sha256':hashlib.sha256((out/'model.onnx').read_bytes()).hexdigest(),'description':args.description,
          'validation_status':'candidate','validation_report':policy_report.get('validation_report',{}),
          'policies':policy_report.get('policies',{})}
    # Export never promotes an unreviewed model to validated.
    (out/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'directory':str(out),'model_id':args.model_id,'preprocessing':meta['preprocessing']},ensure_ascii=False))
if __name__=='__main__':main()

