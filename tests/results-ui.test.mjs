import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as seeds from '../public/data.js';
import * as categoryTools from '../public/categories.js';

const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/gm,'')
  .replace(/^/,'const {historicalRuns,attackSeeds,normalSeeds,historicalTypes}=seeds; const {categorySeeds,categoryNameKey,categoryFor,normalizeCategoryData,categoryGroups,categoryScopes,categoryScope,categoryLayout,selectionPlan,attackVectors,vectorCategoryKey,vectorSelectionPlan}=categoryTools;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const clone=value=>JSON.parse(JSON.stringify(value));
const job=(id,extra={})=>({id,name:'실험 '+id,purpose:'attack',models:['model-a'],status:'completed',created_at:'2026-11-11T05:00:00Z',finished:2,total:2,...extra});
const history=(page=1,extra={})=>({experiments:[job('page-'+page)],page,pageSize:5,total:11,totalPages:3,activeCount:0,...extra});
const model=(name='model-a',extra={})=>({model:name,provider:'baze',returnedModels:[name+'-reported'],experiments:3,total:10,succeeded:5,errors:2,queued:1,running:1,cancelled:1,blocked:0,
  attack:{successes:0,failures:2,indeterminate:2,unjudged:1,judged:2,asr:0},latencyMsMean:1250,latencyReportedCount:4,
  tokens:{inputMean:20,outputMean:null,totalMean:30,inputReportedCount:4,outputReportedCount:0,totalReportedCount:3},
  scenarioCount:2,scenarioVersionCount:3,promptVersions:['email-baseline-v1'],inputModes:['scenario-only-v1','attachment-v1'],evaluatorVersions:['exact-trim-v1'],inferenceSettingsCount:1,
  firstRunAt:'2026-11-10T05:00:00Z',lastRunAt:'2026-11-11T05:00:00Z',quality:null,...extra});
const metrics=(purpose='attack',vector='all',rows=[model()])=>({purpose,vector,defense:'off',generatedAt:'2026-11-11T05:01:00Z',models:rows});
function app(fetcher=()=>{throw Error('Unexpected request');}){
  const requests=[],timers=new Map(),downloads=[],toasts=[],performanceOpens=[],elements=new Map();let nextTimer=0;
  function element(selector){if(!elements.has(selector))elements.set(selector,{innerHTML:'',listeners:{},disabled:false,addEventListener(name,fn){this.listeners[name]=fn;},scrollIntoView(){this.scrolled=true;}});return elements.get(selector);}
  const context=vm.createContext({categoryTools,seeds:structuredClone({...seeds}),structuredClone,console,crypto:webcrypto,AbortController,
    setTimeout:(callback,delay)=>{const id=++nextTimer;timers.set(id,{callback,delay});return id;},clearTimeout:id=>timers.delete(id),
    fetch:(url,options)=>{requests.push({url,options});return fetcher(url,options);},
    localStorage:{getItem:()=>null,setItem(){}},
    document:{addEventListener(){},querySelector:selector=>elements.get(selector)||null,querySelectorAll:()=>[]},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#results'},downloads,toasts,performanceOpens});
  vm.runInContext(source+`\nshell=()=>{};renderRuntime=()=>{};toast=message=>toasts.push(message);download=(name,content,type)=>downloads.push({name,content,type});openPerformanceRun=async(mode,id)=>performanceOpens.push({mode,id});
    globalThis.api={state,pages,actions,bindPage,databaseJobRows,refreshJobs,clearJobFilter,showModelHistory,refreshResultMetrics,changeResultPurpose,liveResultRows,liveResultsContent,resultBars,resultModelTable,resultModelOptions,resultCsvCell,exportCSV,openHistoryPerformance};`,context);
  Object.assign(context.api.state,{dbChecked:true,dbConfigured:true,dbReady:true,route:'results'});
  return {...context.api,requests,timers,downloads,toasts,performanceOpens,element,location:context.location};
}

 test('history is capped at five rows and uses server totals and clamped page metadata',async()=>{
  const a=app(()=>response(history(3,{experiments:[job('last')]})));
  a.state.jobs=Array.from({length:8},(_,i)=>job(String(i)));
  assert.equal((a.databaseJobRows().match(/data-action="db-detail"/g)||[]).length,5);
  await a.refreshJobs({page:99});
  assert.equal(a.requests[0].url,'/api/experiments?page=99&pageSize=5');
  assert.equal(a.state.jobPage,3);assert.equal(a.state.jobPages,3);assert.equal(a.state.jobTotal,11);
  const html=a.databaseJobRows();assert.match(html,/11–11개/);assert.match(html,/3 \/ 3/);
  assert.match(html,/data-page="4" disabled/);assert.doesNotMatch(html,/data-page="2" disabled/);
  assert.equal(a.requests[0].options.method,'GET');
 });

test('late history responses cannot replace the newer selected page',async()=>{
  const pending=[];const a=app(()=>new Promise(resolve=>pending.push(resolve)));
  const first=a.refreshJobs({page:1}),second=a.refreshJobs({page:2});
  pending[1](response(history(2)));await second;
  pending[0](response(history(1)));await first;
  assert.equal(a.state.jobPage,2);assert.equal(a.state.jobs[0].id,'page-2');assert.equal(a.state.jobLoading,false);
});

test('global active experiments keep polling even when the displayed page is completed',async()=>{
  let count=0;const a=app(url=>response(url.startsWith('/api/results/models')?metrics():history(2,{activeCount:count++===0?1:0})));
  await a.refreshJobs({page:2});assert.equal(a.state.jobs[0].status,'completed');
  const scheduled=[...a.timers.values()];assert.equal(scheduled.length,1);assert.equal(scheduled[0].delay,5000);
  scheduled[0].callback();await new Promise(resolve=>setImmediate(resolve));
  assert.ok(a.requests.some(r=>r.url==='/api/experiments?page=2&pageSize=5'));
  assert.ok(a.requests.some(r=>r.url.startsWith('/api/results/models?')));
  assert.ok(a.requests.every(r=>r.options.method==='GET'));
  assert.ok(a.requests.every(r=>!r.url.includes('/summarize')));
});

test('history API failures and legacy unpaginated responses stay explicit',async()=>{
  const a=app(()=>response({experiments:Array.from({length:7},(_,i)=>job(String(i)))}));
  await a.refreshJobs();assert.match(a.state.jobError,/페이지 정보/);assert.equal(a.state.jobs.length,0);
  assert.match(a.databaseJobRows(),/role="alert"/);assert.equal(a.timers.size,0);
  const b=app(()=>response({error:{message:'연결을 확인하세요.'}},503));await b.refreshJobs();
  assert.match(b.databaseJobRows(),/연결을 확인하세요/);
});

test('model and experiment names are escaped and dates use the Korean display zone',()=>{
  const a=app();a.state.jobs=[job('x',{name:'<img src=x onerror=alert(1)>',models:['<script>bad</script>']})];
  a.state.resultReport.data=metrics('attack','all',[model('<img src=x>',{returnedModels:['<svg onload=bad>']})]);
  const html=a.pages.results();assert.match(html,/웹에서 진행한 실험/);assert.match(html,/&lt;img src=x/);
  assert.doesNotMatch(html,/<img|<script|<svg onload=bad/);assert.match(html,/2026\. 11\. 11\. 14:00/);
  assert.doesNotMatch(html,/9월 30일|61\.22|53\.06|기존 실험 ASR|원본 실험 기록/);
});

test('live result filters request only their chosen purpose and path and ignore late responses',async()=>{
  const pending=[];const a=app(()=>new Promise(resolve=>pending.push(resolve)));
  a.state.resultReport.vector='direct';const old=a.refreshResultMetrics();
  a.changeResultPurpose('performance');const fresh=a.refreshResultMetrics();
  assert.equal(a.requests[0].url,'/api/results/models?purpose=attack&vector=direct');
  assert.equal(a.requests[1].url,'/api/results/models?purpose=performance&vector=all');
  const perf=model('quality-model',{attack:null,quality:{gradedCount:5,meanRougeL:0.72}});
  pending[1](response(metrics('performance','all',[perf])));await fresh;
  pending[0](response(metrics('attack','direct')));await old;
  assert.equal(a.state.resultReport.data.purpose,'performance');assert.equal(a.state.resultReport.data.models[0].model,'quality-model');
  assert.match(a.liveResultsContent(),/ROUGE-L/);assert.doesNotMatch(a.liveResultsContent(),/모델별 공격 성공률/);
  assert.ok(a.requests.every(r=>r.options.method==='GET'));
});

test('changing the visible model filters actual aggregate rows without using historical models',()=>{
  const a=app();a.state.resultReport.data=metrics('attack','all',[model('one'),model('two')]);
  a.state.resultReport.model='two';assert.deepEqual(clone(a.liveResultRows()).map(x=>x.model),['two']);
  assert.match(a.resultModelOptions(),/value="two" selected/);assert.doesNotMatch(a.resultModelOptions(),/gemini-3.5-flash-lite/);
  a.changeResultPurpose('normal');assert.equal(a.state.resultReport.model,'all');assert.equal(a.state.resultReport.data,null);assert.equal(a.state.resultReport.vector,'all');
});

test('zero ASR remains a measured zero while unavailable measurements remain dashes',()=>{
  const a=app();a.state.resultReport.data=metrics();
  const html=a.liveResultsContent();assert.match(html,/0\.0%/);assert.match(html,/실패 2/);assert.match(html,/미확정 2/);assert.match(html,/미판정 1/);assert.match(html,/오류 2/);
  assert.match(html,/1\.25초/);assert.match(html,/20\.0 \/ —/);assert.match(html,/출력 0건/);
  const absent=model('missing',{attack:{successes:0,failures:0,judged:0,asr:null,indeterminate:0,unjudged:5},latencyMsMean:null,latencyReportedCount:0});
  const bars=a.resultBars([absent]);assert.match(bars,/<span>—<\/span>/);assert.match(bars,/산출 전/);assert.doesNotMatch(bars,/0\.0%|NaN|undefined/);
});

test('empty or failed live results do not fall back to old sample results',async()=>{
  const a=app(()=>response({error:{message:'현재 지표 조회 실패'}},503));
  a.state.resultReport.data=metrics();await a.refreshResultMetrics();
  const failed=a.liveResultsContent();assert.match(failed,/현재 지표 조회 실패/);assert.match(failed,/role="alert"/);
  assert.doesNotMatch(failed,/model-a|61\.22|9월 30일/);
  a.exportCSV();assert.equal(a.downloads.length,0);
  a.state.resultReport.error='';a.state.resultReport.data=metrics('attack','all',[]);
  assert.match(a.liveResultsContent(),/실험 기록이 없어요/);
  a.state.dbReady=false;assert.match(a.pages.results(),/데이터 연결이 필요해요/);assert.doesNotMatch(a.pages.results(),/9월 30일/);
});

test('CSV exports only displayed live models, preserves missing fields, and neutralizes formulas',()=>{
  const a=app();a.state.resultReport.data=metrics('attack','all',[model('=IMPORTXML("x")'),model('other')]);
  a.state.resultReport.model='=IMPORTXML("x")';a.exportCSV();assert.equal(a.downloads.length,1);
  const file=a.downloads[0];assert.equal(file.name,'fincode-model-results-attack.csv');assert.match(file.content,/requested_model/);
  assert.match(file.content,/'=IMPORTXML\(""x""\)/);assert.doesNotMatch(file.content,/"other"|2026-09-30/);
  assert.equal(file.content.split('\r\n').length,2);assert.match(file.content,/""/);
  for(const value of ['=1+1','+SUM(A1)','-1+2','@SUM(A1)','\t=1','  =1'])assert.ok(a.resultCsvCell(value).startsWith('"\''),value);
  assert.equal(a.resultCsvCell('plain,"text"'),'"plain,""text"""');
});

test('model history uses chosen model and experiment filters then can return to all history',async()=>{
  const a=app(()=>response(history()));a.state.resultReport.data=metrics('attack','indirect',[model('a & b')]);a.state.resultReport.vector='indirect';
  await a.showModelHistory('a & b','baze');
  const url=new URL(a.requests[0].url,'https://example.test');
  assert.equal(url.searchParams.get('model'),'a & b');assert.equal(url.searchParams.get('purpose'),'attack');assert.equal(url.searchParams.get('vector'),'indirect');
  await a.clearJobFilter();assert.equal(a.state.jobFilter,null);assert.equal(a.requests.at(-1).url,'/api/experiments?page=1&pageSize=5');
});

test('performance history opens its existing single or comparison result screen',async()=>{
  const a=app();a.state.jobs=[job('single',{purpose:'performance',models:['one']}),job('comparison',{purpose:'performance',models:['one','two']})];
  await a.actions['db-detail']({dataset:{id:'single'}});assert.equal(a.location.hash,'single-evaluation');
  await a.actions['db-detail']({dataset:{id:'comparison'}});assert.equal(a.location.hash,'comparison');
  assert.deepEqual(clone(a.performanceOpens),[{mode:'single',id:'single'},{mode:'comparison',id:'comparison'}]);assert.equal(a.requests.length,0);
});

test('switching history model clears previous rows even when the request fails on the same page',async()=>{
  let fail;const a=app(()=>new Promise(resolve=>fail=resolve));
  a.state.jobs=[job('old',{models:['old-model']})];a.state.jobPage=1;
  a.state.resultReport.data=metrics('attack','all',[model('new-model')]);
  const request=a.showModelHistory('new-model','baze');
  assert.equal(a.state.jobs.length,0);assert.doesNotMatch(a.databaseJobRows(),/old-model/);
  fail(response({error:{message:'필터 조회 실패'}},503));await request;
  assert.match(a.databaseJobRows(),/필터 조회 실패/);assert.doesNotMatch(a.databaseJobRows(),/old-model/);
});

test('model options deduplicate model IDs while metrics retain distinct provider cohorts',()=>{
  const a=app();a.state.resultReport.data=metrics('attack','all',[model('same',{provider:'baze'}),model('same',{provider:'other-provider'})]);
  assert.equal((a.resultModelOptions().match(/value="same"/g)||[]).length,1);
  a.state.resultReport.model='same';assert.equal(a.liveResultRows().length,2);
  assert.match(a.resultModelTable(a.liveResultRows()),/other-provider/);
});

test('bound model and vector controls filter live results without submitting experiments',async()=>{
  const a=app(url=>{const query=new URL(url,'https://example.test').searchParams;return response(url.startsWith('/api/results/models')?metrics(query.get('purpose'),query.get('vector'),[model('one'),model('two')]):history());});
  const modelSelect=a.element('#live-result-model'),vectorSelect=a.element('#live-result-vector');a.element('#live-results-content');a.element('#db-job-list');
  a.bindPage();await new Promise(resolve=>setImmediate(resolve));
  const before=a.requests.length;modelSelect.listeners.change({target:{value:'two'}});
  assert.equal(a.state.resultReport.model,'two');assert.equal(a.requests.length,before);
  vectorSelect.listeners.change({target:{value:'indirect'}});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(a.state.resultReport.vector,'indirect');assert.equal(a.state.resultReport.model,'all');
  assert.ok(a.requests.some(r=>r.url==='/api/results/models?purpose=attack&vector=indirect'));
  assert.ok(a.requests.every(r=>r.options.method==='GET'));
});

test('normal response metrics do not claim attack or quality scores',()=>{
  const a=app();
  Object.assign(a.state.resultReport,{purpose:'normal',data:metrics('normal','all',[model('service',{attack:null,quality:null})])});
  const html=a.liveResultsContent();assert.match(html,/모델별 평균 응답시간/);assert.match(html,/공격 성공률 집계 대상 아님/);
  assert.doesNotMatch(html,/모델별 공격 성공률|ROUGE-L F1/);
});

test('results expose only three evaluation types and cannot select the retired summary type',()=>{
  const a=app();a.state.resultReport.data=metrics();
  const html=a.pages.results();
  assert.deepEqual([...html.matchAll(/data-action="results-purpose" data-purpose="([^"]+)"/g)].map(match=>match[1]),['attack','normal','performance']);
  assert.doesNotMatch(html,/이메일 요약|data-purpose="summary"/);
  const before=clone(a.state.resultReport);a.changeResultPurpose('summary');
  assert.deepEqual(clone(a.state.resultReport),before);assert.equal(a.requests.length,0);
});

test('legacy summary rows in a paginated response fail explicitly without exposing their contents',async()=>{
  const a=app(()=>response(history(1,{experiments:[job('legacy-summary',{name:'LEGACY_SUMMARY_CONTENT',purpose:'summary'})]})));
  await a.refreshJobs();
  assert.ok(a.state.jobError);assert.equal(a.state.jobs.length,0);
  assert.match(a.databaseJobRows(),/role="alert"/);assert.doesNotMatch(a.databaseJobRows(),/LEGACY_SUMMARY_CONTENT/);
  assert.equal(a.timers.size,0);
});

test('attack path is available only for attack results and model selection remains available for all three types',()=>{
  const a=app();
  for(const purpose of ['attack','normal','performance']){
    a.state.resultReport.purpose=purpose;a.state.resultReport.data=metrics(purpose);
    const html=a.pages.results(),path=html.match(/<select\b[^>]*id="live-result-vector"[^>]*>/)?.[0],model=html.match(/<select\b[^>]*id="live-result-model"[^>]*>/)?.[0];
    assert.ok(path);assert.ok(model);assert.equal(/\bdisabled\b/.test(path),purpose!=='attack');
    assert.doesNotMatch(model,/\bdisabled\b/);
    assert.match(html,/<label\b[^>]*for="live-result-vector"/);assert.match(html,/<label\b[^>]*for="live-result-model"/);
  }
});
