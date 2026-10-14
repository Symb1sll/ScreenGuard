"""Offline adapters using the same transport, image preparation and parser as the workbench."""
from __future__ import annotations
import hashlib,json,os,re,sys,time
from dataclasses import dataclass,asdict
from pathlib import Path
from typing import Any,Dict,Sequence
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import model_api as wire
from screen_guard_core import LABELS
DEFAULT_LABELS=list(LABELS)

@dataclass
class ProviderResult:
    provider:str
    model:str
    scores:dict
    overall_risk:float|None
    needs_review:bool
    evidence:list
    latency_ms:float
    input_tokens:int|None=None
    output_tokens:int|None=None
    raw_text:str=''
    error:str=''
    cached:bool=False
    def to_dict(self):return asdict(self)

def extract_json_object(text):
    raw=text.strip()
    match=re.fullmatch(r'```(?:json)?\s*(.*?)\s*```',raw,re.S|re.I)
    if match:raw=match.group(1)
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('duplicate JSON key')
            result[k]=v
        return result
    value=json.loads(raw,object_pairs_hook=unique)
    if not isinstance(value,dict):raise ValueError('Expected JSON object')
    return value

def normalize_payload(payload,label_keys):
    if list(label_keys)!=list(LABELS):raise ValueError('Labels must match the shared five-label contract')
    result=wire.parse_result(json.dumps(payload))
    return result['scores'],result['overall_risk'],result['needs_review'],result['evidence']

def build_prompt(label_keys):
    if list(label_keys)!=list(LABELS):raise ValueError('Unsupported label order')
    return wire.PROMPT

class VisionProvider:
    provider_name='base';env_prefix='';protocol='chat';default_base_url=''
    def __init__(self,label_keys,timeout=60.,retries=0):
        if list(label_keys)!=list(LABELS):raise ValueError('Unsupported label order')
        if retries:raise ValueError('Automatic paid-call retries are disabled; use --retries 0')
        self.label_keys=list(label_keys);self.timeout=timeout;self.retries=0
        prefix=self.env_prefix
        self.model=os.getenv(prefix+'_MODEL','')
        self.api_key=os.getenv(prefix+'_API_KEY','')
        self.base_url=os.getenv(prefix+'_BASE_URL',self.default_base_url).rstrip('/')
    @property
    def configured(self):return bool(self.api_key and self.base_url and self.model)
    def profile(self):
        from urllib.parse import urlsplit
        u=urlsplit(self.base_url)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:raise wire.ProviderError('Cloud API requires an HTTPS base URL without credentials')
        return {'provider':self.provider_name,'protocol':self.protocol,'base_url':self.base_url,'model':self.model,'timeout_seconds':self.timeout,'max_output_tokens':2048,'json_mode':False}
    def cache_identity(self):
        return {'profile':self.profile(),'prompt':wire.PROMPT_VERSION,'image':'jpeg1280-q88-v1','labels':self.label_keys}
    def predict(self,image_path):
        start=time.perf_counter()
        try:
            result=wire.invoke(self.profile(),self.api_key,'',wire.prepare_image(Path(image_path).read_bytes()))
            usage=result['usage']
            return ProviderResult(self.provider_name,self.model,result['scores'],result['overall_risk'],result['needs_review'],result['evidence'],round((time.perf_counter()-start)*1000,3),
                usage.get('input_tokens',usage.get('prompt_tokens')),usage.get('output_tokens',usage.get('completion_tokens')))
        except Exception as exc:
            message=str(exc) if isinstance(exc,wire.ProviderError) else type(exc).__name__
            return ProviderResult(self.provider_name,self.model,{},None,True,[],round((time.perf_counter()-start)*1000,3),error=message)

class OpenAIResponsesProvider(VisionProvider):
    provider_name='openai';env_prefix='OPENAI';protocol='responses';default_base_url='https://api.openai.com/v1'
class AnthropicProvider(VisionProvider):
    provider_name='claude';env_prefix='ANTHROPIC';protocol='anthropic';default_base_url='https://api.anthropic.com/v1'
class OpenAICompatibleVisionProvider(VisionProvider):
    provider_name='compatible';env_prefix='GENERIC'
class KimiProvider(OpenAICompatibleVisionProvider):
    provider_name='kimi';env_prefix='KIMI';default_base_url='https://api.moonshot.cn/v1'
class QwenProvider(OpenAICompatibleVisionProvider):
    provider_name='qwen';env_prefix='QWEN'
class DeepSeekProvider(OpenAICompatibleVisionProvider):
    provider_name='deepseek';env_prefix='DEEPSEEK';default_base_url='https://api.deepseek.com/v1'
class GLMProvider(OpenAICompatibleVisionProvider):
    provider_name='glm';env_prefix='ZHIPU';default_base_url='https://open.bigmodel.cn/api/paas/v4'

def cache_key(provider,model,image_path,prompt_version=wire.PROMPT_VERSION,configuration=None):
    identity={'provider':provider,'model':model,'prompt':prompt_version,'configuration':configuration or {},'contract':'strict-v2'}
    h=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode());h.update(Path(image_path).read_bytes())
    return h.hexdigest()

class JsonlCache:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.items={}
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                try:
                    item=json.loads(line)
                    if item.get('cache_key'):self.items[item['cache_key']]=item
                except (ValueError,AttributeError):pass
    def get(self,key):
        item=self.items.get(key)
        if not item:return None
        data=dict(item['result']);data['cached']=True
        return ProviderResult(**data)
    def put(self,key,result):
        if result.error:return
        payload={'cache_key':key,'result':result.to_dict()}
        with self.path.open('a',encoding='utf-8') as f:f.write(json.dumps(payload,ensure_ascii=False)+'\n')
        self.items[key]=payload

