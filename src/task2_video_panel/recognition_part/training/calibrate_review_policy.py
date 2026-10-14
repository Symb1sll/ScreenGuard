"""Select separate review/alert candidates using validation predictions only."""
import argparse,csv,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from screen_guard_core import LABELS
def select_policy(truth,scores,min_precision=.98,review_recall=.9,min_support=20):
    policies={};classes={}
    for i,k in enumerate(LABELS):
        y=np.asarray(truth)[:,i].astype(bool);p=np.asarray(scores)[:,i]
        if not np.isfinite(p).all() or ((p<0)|(p>1)).any():raise ValueError('预测分数无效')
        positives=int(y.sum());negatives=int((~y).sum());rows=[]
        for t in sorted(set([.01,.5,.99,1.]+[float(v) for v in p if 0<v<=1])):
            pred=p>=t;tp=int((pred&y).sum());fp=int((pred&~y).sum())
            rows.append({'t':t,'precision':tp/(tp+fp) if tp+fp else 0,'recall':tp/positives if positives else 0,'predicted':tp+fp})
        reviews=[r for r in rows if r['recall']>=review_recall]
        review=max(reviews,key=lambda r:r['t'])['t'] if reviews else .5
        review=min(review,.98)
        candidates=[r for r in rows if r['t']>review and r['precision']>=min_precision and r['predicted']>=min_support]
        eligible=positives>=min_support and negatives>=min_support and bool(candidates)
        best=max(candidates,key=lambda r:(r['recall'],r['precision'])) if eligible else None
        policies[k]={'enabled':True,'review':round(review,6),'alert':round(best['t'],6) if best else 1.}
        classes[k]={'positives':positives,'negatives':negatives,'eligible':eligible,'alert_validation':best,
                    'reason':'候选阈值满足样本内目标，仍需独立业务验收' if eligible else '样本不足或没有满足精确率与支持数的阈值'}
    return {'policies':policies,'validation_report':{'approved_for_alerts':False,'classes':classes,'min_precision':min_precision,'review_recall':review_recall,'min_support':min_support,'data_kind':'unknown'}}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--predictions',required=True);ap.add_argument('--out',required=True)
    ap.add_argument('--min-precision',type=float,default=.98);ap.add_argument('--review-recall',type=float,default=.9)
    ap.add_argument('--min-support',type=int,default=20);ap.add_argument('--data-kind',choices=['real','synthetic','unknown'],default='unknown')
    args=ap.parse_args()
    if not 0<args.min_precision<=1 or not 0<args.review_recall<=1 or args.min_support<1:raise SystemExit('目标参数无效')
    p=Path(args.predictions)
    metrics=json.loads(p.with_name('metrics.json').read_text(encoding='utf8'))
    if metrics.get('split')!='val':raise SystemExit('只允许使用 val 预测标定，禁止用 test 选阈值')
    with p.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows:raise SystemExit('验证集不能为空')
    y=np.array([[int(r['true_'+k]) for k in LABELS] for r in rows])
    scores=np.array([[float(r['prob_'+k]) for k in LABELS] for r in rows])
    if not np.isin(y,[0,1]).all():raise SystemExit('标签必须为 0 或 1')
    result=select_policy(y,scores,args.min_precision,args.review_recall,args.min_support)
    result['validation_report']['data_kind']=args.data_kind
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()

