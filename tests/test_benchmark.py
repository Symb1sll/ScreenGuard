import csv,json,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'src/task2_video_panel'
sys.path[:0]=[str(PROJECT),str(PROJECT/'recognition_part/benchmarking')]
import pytest
from PIL import Image
from screen_guard_core import LABELS
import benchmark_five_models as bench
from providers import ProviderResult,cache_key

def test_paired_coverage_and_cached_latency(monkeypatch,tmp_path):
    labels=tmp_path/'labels.yaml';labels.write_text(json.dumps({'labels':[{'key':k} for k in LABELS]}),encoding='utf-8')
    for i in (1,2,3):Image.new('RGB',(64,64),(i*50,20,30)).save(tmp_path/f'{i}.jpg')
    manifest=tmp_path/'manifest.csv'
    with manifest.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['image_path','labels','split','status']);w.writeheader()
        for i in (1,2,3):w.writerow({'image_path':f'{i}.jpg','labels':'gambling' if i==1 else '', 'split':'test','status':'approved'})
        w.writerow({'image_path':'not-present.jpg','labels':'','split':'test','status':'candidate'})
    calls=[]
    class Provider:
        configured=True
        def __init__(self,name):self.model=name;self.name=name
        def cache_identity(self):return {'test_contract':2}
        def predict(self,path):
            i=int(path.stem);calls.append((self.name,i))
            error=(self.name=='openai' and i==2) or (self.name=='claude' and i==1)
            return ProviderResult(self.name,self.model,{} if error else {k:.1 for k in LABELS},None if error else .1,error,[],1000,10,10,error='synthetic failure' if error else '')
    monkeypatch.setattr(bench,'OpenAIResponsesProvider',lambda *args:Provider('openai'))
    monkeypatch.setattr(bench,'AnthropicProvider',lambda *args:Provider('claude'))
    out=tmp_path/'out';cache=tmp_path/'cache.jsonl'
    monkeypatch.setattr(sys,'argv',['benchmark','--manifest',str(manifest),'--data-root',str(tmp_path),'--labels',str(labels),'--providers','openai,claude','--out',str(out),'--cache',str(cache)])
    bench.main();first=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    assert first['test_samples_requested']==3 and first['common_sample_ids']==[3]
    assert all(r['successful_samples']==2 and r['common_samples']==1 and r['coverage']==pytest.approx(2/3) for r in first['results'].values())
    assert all(r['latency_ms_avg']==1000 and r['fresh_latency_samples']==2 for r in first['results'].values())
    assert all(int(row['samples'])==1 for row in csv.DictReader((out/'ranking.csv').open(encoding='utf-8-sig')))
    calls.clear();bench.main();cached=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    assert set(calls)=={('openai',2),('claude',1)}
    assert all(r['latency_ms_avg'] is None and r['cached_samples']==2 and r['fresh_latency_samples']==0 and r['input_tokens']==0 for r in cached['results'].values())
    image=tmp_path/'1.jpg'
    assert cache_key('local','same-name',image,{'weights_sha256':'a'})!=cache_key('local','same-name',image,{'weights_sha256':'b'})
