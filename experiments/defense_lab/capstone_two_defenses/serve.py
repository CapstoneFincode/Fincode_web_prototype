"""기본 Mock 로컬 서버. 실제 서버·DB를 시작하거나 재시작하지 않는다."""
import json
import os
import re
import httpx
import uvicorn
from backend.app import create_app

mock=os.environ.get('LAB_MODE','mock')!='live'
def respond(request):
    if request.method=='GET':
        return httpx.Response(200,json={'data':[{'id':'gemini-3.5-flash-lite','owned_by':'local-mock'}]})
    payload=json.loads(request.content)
    # 기준 정답은 Mock에 제공하지 않는다. 공격 취약성/방어 성능도 모사하지 않는다.
    text='[Mock: 실제 모델 호출 없음] '+re.sub(r'\s+',' ',payload['messages'][-1]['content'])[:180]
    return httpx.Response(200,json={'model':payload['model'],'choices':[{'message':{'content':text},'finish_reason':'stop'}]})
app=create_app(api_key='local-mock-placeholder' if mock else None,upstream_transport=httpx.MockTransport(respond) if mock else None,database_url='',run_worker=False,attachment_storage=False,lab_mock=mock)
if __name__=='__main__':
    print('격리 실험 화면: http://127.0.0.1:'+os.environ.get('LAB_PORT','8765')+'/defense-lab.html',flush=True)
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('LAB_PORT','8765')),access_log=False)
