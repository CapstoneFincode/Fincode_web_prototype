"""원본 요약 콜백 앞에서 두 방어를 독립적으로 적용한다. DB 의존성 없음."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import time
from .input_filter import InputFilter
from .separation import PromptSeparation

MODES=('off','filter','separate','both')
VERSION='capstone-two-defenses-v1'
SAFE_RESPONSE='안전 검사에서 요약 작업을 변경하려는 지시가 발견되어 이 입력의 처리를 중단했습니다.'

@dataclass(frozen=True)
class DefenseSettings:
    input_filter:bool=False
    prompt_separation:bool=False
    @classmethod
    def from_mode(cls,mode='off'):
        if mode not in MODES:raise ValueError('defense는 off/filter/separate/both 중 하나여야 합니다.')
        return cls(mode in ('filter','both'),mode in ('separate','both'))
    @property
    def mode(self):
        return 'both' if self.input_filter and self.prompt_separation else 'filter' if self.input_filter else 'separate' if self.prompt_separation else 'off'


def digest(messages):
    return hashlib.sha256(json.dumps(messages,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


class DefenseGateway:
    def __init__(self,rules_path=None):
        self.input_filter=InputFilter(rules_path)
        self.separation=PromptSeparation()
    def prepare(self,messages,mode='off'):
        settings=DefenseSettings.from_mode(mode)
        if not isinstance(messages,list) or not 1<=len(messages)<=16 or any(not isinstance(m,dict) or not isinstance(m.get('content'),str) or m.get('role') not in ('system','user','assistant') for m in messages):
            raise ValueError('지원되는 문자열 메시지 목록이 필요합니다.')
        if not any(m['role']=='system' for m in messages):raise ValueError('시스템 지침이 필요합니다.')
        if not any(m['role']=='user' for m in messages):raise ValueError('사용자 데이터가 필요합니다.')
        original=deepcopy(messages);forwarded=deepcopy(messages);start=time.perf_counter()
        result={'mode':mode,'version':VERSION,'blocked':False,'reason':'off' if mode=='off' else 'allowed','score':0.0,'inputFilterEnabled':settings.input_filter,'promptSeparationEnabled':settings.prompt_separation,'inputFilter':None,'separation':None,'modelCalled':False,'originalSha256':digest(original)}
        if settings.input_filter:
            text='\n'.join(m['content'] for m in original if m['role']!='system')
            checked=self.input_filter.check(text);result['inputFilter']=checked
            if checked['blocked']:
                result.update(blocked=True,reason=checked['reason'],score=checked['score'])
                forwarded=[]
        if settings.prompt_separation and not result['blocked']:
            forwarded,result['separation']=self.separation.apply(original)
        result['forwardedSha256']=digest(forwarded)
        result['preprocessingMs']=(time.perf_counter()-start)*1000
        return {'originalMessages':original,'messages':forwarded,'defenseResult':result}
    async def execute(self,generate,model,messages,max_tokens=16384,mode='off'):
        prepared=self.prepare(messages,mode)
        return await self.execute_prepared(generate,model,prepared,max_tokens)
    async def execute_prepared(self,generate,model,prepared,max_tokens=16384):
        decision=deepcopy(prepared['defenseResult'])
        if decision['blocked']:
            return {'mode':'blocked','summary':SAFE_RESPONSE,'model':model,'requestedModel':model,'provider':'baze','defense':decision['mode'],'blocked':True,'defenseResult':decision,'usage':{'inputTokens':0,'outputTokens':0,'totalTokens':0},'latencyMs':0,'endToEndMs':decision['preprocessingMs']}
        start=time.perf_counter()
        result=await generate(model,prepared['messages'],max_tokens)
        decision['modelCalled']=True
        return {**result,'defense':decision['mode'],'blocked':False,'defenseResult':decision,'endToEndMs':(time.perf_counter()-start)*1000+decision['preprocessingMs']}
