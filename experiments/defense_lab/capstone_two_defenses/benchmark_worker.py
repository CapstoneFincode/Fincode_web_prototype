"""고정 스냅샷을 네 방어 설정으로 평가한다. 기본 Mock, DB 쓰기 없음."""
import argparse
from collections import Counter
import csv
import json
import math
import os
from pathlib import Path
import statistics
import time
import httpx
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.defenses import MODES,InputFilter

ROOT=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--live',action='store_true')
parser.add_argument('--sample',type=int)
parser.add_argument('--repeats',type=int,default=3)
parser.add_argument('--max-calls',type=int,default=200)
parser.add_argument('--output',default='results/mock_comparison')
args=parser.parse_args()
out=(ROOT/args.output).resolve()
if not out.is_relative_to(ROOT/'results') or out.exists():raise SystemExit('results 아래 새 결과 경로만 허용합니다.')
if args.repeats<1 or args.max_calls<1 or (args.sample is not None and args.sample<1):raise SystemExit('횟수는 1 이상')
cases=json.loads((ROOT/'snapshots/cases.json').read_text())
if args.sample is not None:
 cases=[x for kind in ['attack','normal'] for x in [r for r in cases if r['kind']==kind][:args.sample]]
planned=len(cases)*len(MODES)*args.repeats
print(json.dumps({'simulation':not args.live,'cases':len(cases),'logical_run_upper_bound':planned,'hard_request_cap':args.max_calls,'repeats':args.repeats,'cost':'단가 미확인. 실제 호출 금액은 계산하지 않음.'},ensure_ascii=False),flush=True)
if args.live and (planned>args.max_calls or not os.environ.get('LAB_BAZE_API_KEY')):
 raise SystemExit('실호출은 계획이 호출 상한 이내이고 LAB_BAZE_API_KEY가 있을 때만 허용합니다.')
request_count=0
class Budget(httpx.AsyncBaseTransport):
 def __init__(self):self.transport=httpx.AsyncHTTPTransport(proxy=os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy'))
 async def handle_async_request(self,request):
  global request_count
  if request_count>=args.max_calls:raise httpx.RequestError('LAB_REQUEST_LIMIT',request=request)
  request_count+=1;return await self.transport.handle_async_request(request)
 async def aclose(self):pass

def respond(request):
 global request_count
 request_count+=1;payload=json.loads(request.content)
 text='[Mock: 기능 검사 전용] '+payload['messages'][-1]['content'][:160]
 return httpx.Response(200,json={'model':payload['model'],'choices':[{'message':{'content':text},'finish_reason':'stop'}]})
app=create_app(api_key=None if args.live else 'mock-placeholder',upstream_transport=Budget() if args.live else httpx.MockTransport(respond),database_url='',run_worker=False,attachment_storage=False,lab_mock=not args.live)
records=[]
with TestClient(app) as client:
 for repeat in range(1,args.repeats+1):
  order=MODES[repeat-1:]+MODES[:repeat-1] if repeat<=len(MODES) else MODES
  for mode in order:
   for case in cases:
    response=client.post('/api/lab/evaluate',json={'caseId':case['id'],'defense':mode})
    if response.status_code==200:
     row=response.json()
    else:
     row={'caseId':case['id'],'kind':case['kind'],'category':case['category'],'vector':case['vector'],'defense':mode,'apiError':response.json().get('error',{}).get('code','HTTP_ERROR'),'httpStatus':response.status_code}
    row['repeat']=repeat;records.append(row)
  print(json.dumps({'completed_repeat':repeat,'rows':len(records)},ensure_ascii=False),flush=True)
def mean(xs):
 xs=[x for x in xs if x is not None];return statistics.mean(xs) if xs else None
def percentile(xs):
 xs=sorted(xs);return xs[max(0,math.ceil(len(xs)*.95)-1)] if xs else None
summary=[]
for mode in MODES:
 rows=[x for x in records if x['defense']==mode];valid=[x for x in rows if not x.get('apiError')];a=[x for x in valid if x['kind']=='attack'];n=[x for x in valid if x['kind']=='normal'];judged=[x for x in a if x['assessment']['attackSuccess'] is not None]
 item={'config':mode,'simulation':not args.live,'runs':len(rows),'apiErrors':len(rows)-len(valid),'preModelBlockedAttacks':sum(x['blocked'] for x in a),'preModelBlockedNormals':sum(x['blocked'] for x in n),'asr':mean([float(x['assessment']['attackSuccess']) for x in judged]) if args.live else None,'fpr':mean([float(x['assessment']['normalRejected']) for x in n]) if args.live else None,'quality':mean([x['assessment']['rougeL'] for x in n]) if args.live else None,'tokensIn':mean([x['usage'].get('inputTokens') for x in valid]) if args.live else None,'tokensOut':mean([x['usage'].get('outputTokens') for x in valid]) if args.live else None,'latencyMeanMs':mean([x['endToEndMs'] for x in valid]) if args.live else None,'latencyP95Ms':percentile([x['endToEndMs'] for x in valid]) if args.live else None,'decision':'보류: 실제 모델 평가 필요' if not args.live else '보류: 보고서와 표본을 검토해야 함'}
 item['byVector']={v:{'runs':sum(x['vector']==v for x in a),'preModelBlocks':sum(x['vector']==v and x['blocked'] for x in a)} for v in ['direct','indirect']}
 item['byCategory']={v:{'runs':sum(x['category']==v for x in a),'preModelBlocks':sum(x['category']==v and x['blocked'] for x in a)} for v in sorted({x['category'] for x in a})}
 if args.live:
  item['repeatStatistics']={k:{'mean':mean(vals),'std':statistics.stdev([v for v in vals if v is not None]) if sum(v is not None for v in vals)>1 else None} for k,vals in [('latencyMeanMs',[mean([x['endToEndMs'] for x in valid if x['repeat']==i]) for i in range(1,args.repeats+1)])]}
 summary.append(item)
filtering=InputFilter();screen=[{k:case[k] for k in ['id','kind','vector','category']}|filtering.check(case['body']) for case in cases]
# 이는 입력 탐지기의 정적 차단율이다. LLM의 ASR나 종단 간 FPR로 표시하지 않는다.
static={'normalCount':sum(x['kind']=='normal' for x in screen),'normalBlocked':sum(x['kind']=='normal' and x['blocked'] for x in screen),'attackCount':sum(x['kind']=='attack' for x in screen),'attackFlagged':sum(x['kind']=='attack' and x['blocked'] for x in screen),'modelEffectMeasured':False,'rows':screen}
out.mkdir(parents=True)
def write(name,data):
 text=json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)
 key=os.environ.get('LAB_BAZE_API_KEY')
 if key and key in text:raise RuntimeError('비밀값 저장 차단')
 (out/name).write_text(text,encoding='utf-8')
write('summary.json',summary);write('rows.json',records);write('input_screening.json',static);write('manifest.json',{'simulation':not args.live,'repeats':args.repeats,'planned':planned,'mockTransportCalls':request_count if not args.live else 0,'liveModelCalls':request_count if args.live else 0,'cases':len(cases),'modes':list(MODES),'snapshotManifest':json.loads((ROOT/'snapshots/manifest.json').read_text())})
with (out/'comparison.csv').open('w',newline='',encoding='utf-8-sig') as f:
 keys=['config','simulation','runs','apiErrors','preModelBlockedAttacks','preModelBlockedNormals','asr','fpr','quality','tokensIn','tokensOut','latencyMeanMs','latencyP95Ms','decision'];w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(summary)
print(json.dumps({'rows':len(records),'apiErrors':sum(x['apiErrors'] for x in summary),'liveCalls':request_count if args.live else 0,'staticNormalBlocks':static['normalBlocked'],'staticNormalCount':static['normalCount'],'staticAttackFlags':static['attackFlagged'],'staticAttackCount':static['attackCount']},ensure_ascii=False),flush=True)
