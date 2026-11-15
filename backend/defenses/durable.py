"""방어 조건과 전송문을 큐 등록 시 고정한다. 기존 실행 기록은 변경하지 않는다."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
import hashlib,json
from .gateway import DefenseGateway,DefenseSettings

def mode(value='off'):
    return DefenseSettings.from_mode(value).mode

def freeze(snapshot,selected):
    selected=mode(selected)
    if selected=='off':return deepcopy(snapshot)
    prepared=DefenseGateway().prepare(snapshot['messages'],selected)
    result=deepcopy(snapshot)
    result.update(messages=prepared['messages'],defenseMode=selected,defensePlan=prepared)
    return result

def profile(conn,selected,actor):
    selected=mode(selected)
    if selected=='off':return None
    from psycopg.types.json import Jsonb
    gateway=DefenseGateway();steps=[]
    if selected in ('filter','both'):steps.append({'name':'input_filter','version':gateway.input_filter.config['version'],'config':gateway.input_filter.config})
    if selected in ('separate','both'):
        from .separation import VERSION,POLICY
        steps.append({'name':'prompt_separation','version':VERSION,'config':{'policy':POLICY}})
    code=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py')))+Path(__file__).with_name('rules.yaml').read_bytes()).hexdigest()
    key='builtin-'+selected+'-'+code[:16];identifier=str(uuid5(NAMESPACE_URL,'fincode/defense/'+key))
    conn.execute('''INSERT INTO defense_profiles(id,profile_key,revision,name,implementation_status,ordered_steps,code_ref,created_by)
        VALUES(%s,%s,1,%s,'ready',%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
        (identifier,key,selected,Jsonb(steps),'sha256:'+code,actor))
    return identifier
