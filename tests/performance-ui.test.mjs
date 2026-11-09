import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as seeds from '../public/data.js';
import * as categoryTools from '../public/categories.js';

// Execute the application with a small DOM adapter. No request can escape this harness.
const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/gm,'')
  .replace(/^/,'const { historicalRuns, attackSeeds, normalSeeds, historicalTypes } = seeds; const { categorySeeds, categoryNameKey, categoryFor, normalizeCategoryData, categoryGroups, categoryScopes, categoryScope, categoryLayout, selectionPlan, attackVectors, vectorCategoryKey, vectorSelectionPlan } = categoryTools;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');
const storedKey='fincode-lab-v1-performance-v1';
const id=n=>'00000000-0000-4000-8000-'+String(n).padStart(12,'0');
const runId=id(1001),otherRunId=id(1002);
const plain=value=>JSON.parse(JSON.stringify(value));
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};

function element(extra={}){
  const attributes={},classes=new Set(),listeners={};
  return {hidden:false,innerHTML:'',outerHTML:'',textContent:'',value:'',attributes,listeners,
    focus(){this.focused=true;},setAttribute(name,value){attributes[name]=String(value);},
    getAttribute(name){return attributes[name]??null;},
    classList:{add(name){classes.add(name);},remove(name){classes.delete(name);},contains(name){return classes.has(name);},
      toggle(name,force){const add=force??!classes.has(name);if(add)classes.add(name);else classes.delete(name);return add;}},
    addEventListener(name,handler){listeners[name]=handler;},...extra};
}

function app({fetch:handler,storage=new Map()}={}){
  const elements=Object.fromEntries(['#comparison-summary','#single-evaluation-summary','#performance-dataset',
    '#performance-history','#performance-results','#performance-cases','#single-evaluation-model'].map(key=>[key,element()]));
  const calls=[],queryLists={},timers=new Map(),location={hash:'#overview'};
  let timerSequence=0;
  const context=vm.createContext({seeds:structuredClone({...seeds}),categoryTools,structuredClone,console,
    crypto:webcrypto,AbortController,location,
    setTimeout(fn,delay){const key=++timerSequence;timers.set(key,{fn,delay});return key;},
    clearTimeout(key){timers.delete(key);},
    fetch(path,options){calls.push({path,options});if(!handler)throw new Error('Unexpected mocked request: '+path);return handler(path,options,calls);},
    localStorage:{getItem:key=>storage.get(key)??null,setItem(key,value){storage.set(key,String(value));}},
    document:{addEventListener(){},querySelector:selector=>elements[selector]??null,
      querySelectorAll:selector=>queryLists[selector]??[],getElementById:name=>elements['#'+name]??null},
    window:{addEventListener(){},scrollTo(){}}});
  vm.runInContext(source+`\nshell=()=>{};toast=()=>{};
    globalThis.api={state,route,performanceView,performanceModels,performanceValidation,performanceSummary,
      selectedPerformanceEmails,performanceDatasetPanel,performanceHistory,performanceModelTable,
      performanceCaseCard,performanceResults,performancePage,performanceNumber,performancePercent,
      evaluationDefensePanel,updateSingleEvaluationDraft,updateComparisonDraft,addComparisonModel,
      removeComparisonModel,bindPerformanceSettings,loadPerformanceData,submitPerformance,
      openPerformanceRun,refreshPerformanceRun,loadPerformanceCases,cancelPerformance,
      mountPerformance,stopPerformancePolling,schedulePerformancePoll};`,context);
  return {api:context.api,elements,calls,queryLists,timers,location,storage};
}

function dataset(){
  return {id:'ground_truth_set/v1',name:'합성 정상 이메일',total:100,available:100,reviewedLabels:100,reviewedReferences:0,
    items:Array.from({length:100},(_,i)=>({id:id(i+1),sourceId:'GT-SYN-'+String(i+1).padStart(3,'0'),
      caseKey:'case-'+(i+1),title:'메일 '+(i+1),reviewStatus:'reviewed',referenceReviewStatus:'draft',
      dbVersion:1,groundTruthId:id(i+201)}))};
}
function summary(overrides={}){
  return {id:runId,name:'평가 기록',status:'completed',datasetId:'ground_truth_set/v1',createdAt:'2026-11-09T13:00:00Z',
    models:['model-a'],defense:'off',total:5,finished:5,succeeded:5,failed:0,cancelled:0,running:0,queued:0,
    preliminary:true,commonCompletedEmails:5,modelResults:[{model:'model-a',total:5,finished:5,succeeded:5,
      gradedCount:5,failed:0,cancelled:0,meanRougeL:0.75,meanFactCoverage:0.5,confirmedFacts:5,totalFacts:10,
      needsReviewFacts:5,latencyMsMean:100,latencyMsMedian:90,
      tokens:{inputMean:100,outputMean:20,totalMean:120,reportedCount:5},
      common:{count:5,meanRougeL:0.75,latencyMsMean:100,totalTokensMean:120}}],...overrides};
}
function caseRow(overrides={}){
  return {id:id(2001),emailId:id(1),sourceId:'GT-SYN-001',title:'회의 일정',requestedModel:'model-a',returnedModel:'model-a',
    status:'succeeded',body:'월요일 오전 회의입니다.',reference:'월요일 오전 회의',summary:'월요일 오전 회의 안내',
    referenceReviewStatus:'draft',latencyMs:100,usage:{inputTokens:100,outputTokens:20,totalTokens:120},
    warnings:[],finishReason:'stop',error:null,
    quality:{score:0.75,version:'rouge-l-v1',rougeL:{precision:0.8,recall:0.7,f1:0.75},
      factCoverage:0.5,confirmedFacts:1,totalFacts:2,needsReviewFacts:1,
      facts:[{fact:'월요일 오전',status:'confirmed',evidence:'월요일 오전',reason:'문구 확인'}]},...overrides};
}
function ready(a,mode='single'){
  Object.assign(a.state,{route:mode==='single'?'single-evaluation':'comparison',dbReady:true,connection:'ready',
    models:['model-a','model-b','model-c','model-d','model-e','model-f'].map(id=>({id}))});
  a.state.performance.dataset=dataset();a.state.performance.loaded=true;
  a.state.singleEvaluationDraft.model='model-a';a.state.comparisonDraft.models=['model-a','model-b'];
}
const runButton=(html,mode)=>(html.match(/<button\b[^>]*>/g)||[]).find(tag=>tag.includes('id="'+(mode==='single'?'single-evaluation':'comparison')+'-run"'));
const pollTimers=h=>[...h.timers.entries()].filter(([,value])=>value.delay===2500);
const casesResponse=(rows=[],total=rows.length)=>response({cases:rows,total,offset:0,limit:20});

test('only the 100 server dataset records are selected, with deterministic category-spread 5/10 subsets',()=>{
  const h=app(),a=h.api;ready(a);
  a.state.normal.push({id:'local-only',sourceId:'GT-SYN-101',reviewStatus:'reviewed'});
  a.state.performance.dataset.items.push({id:id(999),sourceId:'UI-SAMPLE-001',reviewStatus:'sample'});
  a.state.performance.dataset.items.reverse();
  const all=plain(a.selectedPerformanceEmails('single'));
  assert.equal(all.length,100);assert.equal(new Set(all.map(row=>row.id)).size,100);
  assert.deepEqual(all.map(row=>row.id),Array.from({length:100},(_,i)=>id(i+1)));
  a.performanceView('single').size=5;
  assert.deepEqual(plain(a.selectedPerformanceEmails('single')).map(row=>row.sourceId),['GT-SYN-001','GT-SYN-021','GT-SYN-041','GT-SYN-061','GT-SYN-081']);
  a.performanceView('single').size=10;
  assert.deepEqual(plain(a.selectedPerformanceEmails('single')).map(row=>row.sourceId),['GT-SYN-001','GT-SYN-021','GT-SYN-041','GT-SYN-061','GT-SYN-081','GT-SYN-002','GT-SYN-022','GT-SYN-042','GT-SYN-062','GT-SYN-082']);
  assert.equal(h.calls.length,0);
});

test('run stays disabled for unavailable datasets, incomplete review, and invalid model selections',async()=>{
  const h=app(),a=h.api;ready(a,'comparison');
  const invalid=[
    ()=>{a.state.dbReady=false;},()=>{a.state.connection='error';},()=>{a.state.performance.dataset=null;},
    ()=>{a.state.comparisonDraft.models=['model-a'];},()=>{a.state.comparisonDraft.models=['model-a',''];},
    ()=>{a.state.comparisonDraft.models=['model-a','unknown'];},()=>{a.state.comparisonDraft.models=['model-a','model-a'];},
    ()=>{a.state.comparisonDraft.models=['model-a','model-b','model-c','model-d','model-e','model-f'];},
    ()=>{a.state.performance.dataset.items.pop();},()=>{a.state.performance.dataset.items[0].reviewStatus='pending';},
    ()=>{a.state.performance.dataset.items[0].groundTruthId=null;},
    ()=>{a.state.performance.dataset.items[0].referenceReviewStatus='rejected';},
    ()=>{a.state.performance.dataset.items[0].referenceReviewStatus='sample';},
    ()=>{delete a.state.performance.dataset.items[0].referenceReviewStatus;},
  ];
  for(const invalidate of invalid){
    ready(a,'comparison');invalidate();
    assert.ok(a.performanceValidation('comparison'));
    assert.match(runButton(a.performanceSummary('comparison'),'comparison'),/\bdisabled\b/);
    await a.submitPerformance('comparison');assert.equal(h.calls.length,0);
  }
  ready(a,'comparison');assert.equal(a.performanceValidation('comparison'),'');
  assert.doesNotMatch(runButton(a.performanceSummary('comparison'),'comparison'),/\bdisabled\b/);
  a.state.singleEvaluationDraft.model='';assert.match(runButton(a.performanceSummary('single'),'single'),/\bdisabled\b/);
});

test('configuration is inert and defense OFF is the only available execution condition',()=>{
  const h=app(),a=h.api;ready(a,'comparison');
  for(const defense of ['filter','separate','both']){
    a.updateSingleEvaluationDraft('defense',defense);a.updateComparisonDraft('defense',defense);
  }
  assert.equal(a.state.singleEvaluationDraft.defense,'off');assert.equal(a.state.comparisonDraft.defense,'off');
  const html=a.evaluationDefensePanel('comparison-defense');
  for(const input of html.match(/<input\b[^>]*>/g)){
    if(input.includes('value="off"'))assert.match(input,/\bchecked\b/);
    else assert.match(input,/\bdisabled\b/);
  }
  a.updateComparisonDraft(0,'model-c');a.addComparisonModel();a.updateComparisonDraft(2,'model-d');
  a.performancePage('comparison');a.performancePage('single');
  assert.equal(h.calls.length,0);
});

test('explicit single evaluation posts one fixed ID-only payload and prevents double submission',async()=>{
  const accepted=deferred();
  const h=app({fetch:(path,options)=>{
    if(path==='/api/performance'&&options.method==='POST')return accepted.promise;
    if(path==='/api/performance/'+runId)return response(summary());
    if(path.includes('/cases?'))return casesResponse([caseRow()],5);
    throw new Error('Unexpected request: '+path);
  }}),a=h.api;ready(a);a.performanceView('single').size=5;a.performanceView('single').name='  첫 평가  ';
  const request=a.submitPerformance('single');
  assert.equal(a.performanceView('single').submitting,true);
  assert.match(runButton(a.performanceSummary('single'),'single'),/\bdisabled\b/);
  await a.submitPerformance('single');assert.equal(h.calls.length,1);
  const payload=JSON.parse(h.calls[0].options.body);
  assert.deepEqual(Object.keys(payload).sort(),['datasetId','defense','emailIds','idempotencyKey','models','name']);
  assert.deepEqual(payload.emailIds,[1,21,41,61,81].map(id));assert.deepEqual(payload.models,['model-a']);
  assert.equal(payload.name,'첫 평가');assert.equal(payload.defense,'off');assert.equal(payload.datasetId,'ground_truth_set/v1');
  assert.match(payload.idempotencyKey,/^[a-f0-9-]{36}$/i);
  assert.equal(h.calls[0].options.credentials,'same-origin');
  accepted.resolve(response({id:runId}));await request;
  assert.deepEqual(h.calls.map(call=>[call.options.method,call.path]),[
    ['POST','/api/performance'],['GET','/api/performance/'+runId],['GET','/api/performance/'+runId+'/cases?offset=0&limit=20']]);
  assert.equal(a.performanceView('single').pendingSubmission,null);
  assert.equal(a.performanceView('single').selectedId,runId);
  assert.equal(a.performanceView('single').summary.succeeded,5);
  assert.equal(a.performanceView('single').cases[0].body,caseRow().body);
  assert.equal(a.performanceView('single').submitting,false);
  assert.equal(h.timers.size,0);
});

test('comparison uses one evaluation request for up to five models and shows 500 expected calls',async()=>{
  const h=app({fetch:(path,options)=>{
    if(options.method==='POST')return response({id:runId});
    if(path.includes('/cases?'))return casesResponse();
    return response(summary({models:['model-a','model-b','model-c','model-d','model-e'],total:500,finished:500}));
  }}),a=h.api;ready(a,'comparison');
  a.state.comparisonDraft.models=['model-a','model-b','model-c','model-d','model-e'];
  assert.match(a.performanceSummary('comparison'),/500회/);
  await a.submitPerformance('comparison');
  const posts=h.calls.filter(call=>call.options.method==='POST');assert.equal(posts.length,1);
  const payload=JSON.parse(posts[0].options.body);assert.equal(payload.emailIds.length,100);assert.equal(payload.models.length,5);
  assert.equal(payload.defense,'off');assert.equal(a.performanceView('single').selectedId,'');
});

for(const failure of ['network','server'])test(failure+' uncertainty preserves an immutable request and retries only after explicit action',async()=>{
  let postCount=0;
  const h=app({fetch:(path,options)=>{
    if(options.method==='POST'){
      if(++postCount===1)return failure==='network'?Promise.reject(new Error('연결 끊김')):response({error:{message:'잠시 후 확인',code:'UNAVAILABLE'}},503);
      return response({id:runId});
    }
    return path.includes('/cases?')?casesResponse():response(summary());
  }}),a=h.api;ready(a);a.performanceView('single').size=5;
  await a.submitPerformance('single');const firstBody=h.calls[0].options.body,pending=plain(a.performanceView('single').pendingSubmission);
  assert.ok(pending);assert.equal(a.performanceView('single').submitting,false);
  assert.match(a.performanceView('single').error,/자동으로 다시 실행하지 않았어요/);
  assert.match(a.performanceSummary('single'),/동일 요청 상태 확인/);
  a.updateSingleEvaluationDraft('model','model-b');assert.equal(a.state.singleEvaluationDraft.model,'model-a');
  const size=element({dataset:{performanceSize:'single'}}),name=element({dataset:{performanceName:'single'}});
  h.queryLists['[data-performance-size]']=[size];h.queryLists['[data-performance-name]']=[name];a.bindPerformanceSettings();
  size.listeners.change({target:{value:'100'}});name.listeners.input({target:{value:'changed'}});
  assert.equal(a.performanceView('single').size,5);assert.equal(a.performanceView('single').name,'');
  await flush();assert.equal(postCount,1);assert.equal(h.timers.size,0);
  assert.deepEqual(JSON.parse(h.storage.get(storedKey)).single.pendingSubmission,pending);
  await a.submitPerformance('single');
  assert.equal(postCount,2);assert.equal(h.calls.filter(call=>call.options.method==='POST')[1].options.body,firstBody);
  assert.equal(a.performanceView('single').pendingSubmission,null);
});

test('pending comparison requests and their idempotency key survive page reload without automatic POST',async()=>{
  const h=app({fetch:()=>Promise.reject(new Error('접수 결과 불명'))}),a=h.api;ready(a,'comparison');
  a.performanceView('comparison').size=10;await a.submitPerformance('comparison');
  const saved=JSON.parse(h.calls[0].options.body);
  const restored=app({storage:h.storage,fetch:(path,options)=>{
    if(options.method==='POST')return response({id:runId});
    if(path==='/api/performance/dataset')return response({dataset:dataset()});
    if(path==='/api/performance')return response({evaluations:[]});
    return path.includes('/cases?')?casesResponse():response(summary({models:saved.models}));
  }}),b=restored.api;
  Object.assign(b.state,{route:'comparison',dbReady:true,connection:'ready',models:[{id:'model-a'},{id:'model-b'}]});
  assert.deepEqual(plain(b.state.comparisonDraft.models),saved.models);
  assert.equal(b.performanceView('comparison').size,10);
  assert.equal(b.performanceView('comparison').pendingSubmission.idempotencyKey,saved.idempotencyKey);
  await b.loadPerformanceData();assert.equal(restored.calls.filter(call=>call.options.method==='POST').length,0);
  b.addComparisonModel();b.removeComparisonModel(2);b.updateComparisonDraft(0,'model-b');
  assert.deepEqual(plain(b.state.comparisonDraft.models),saved.models);
  await b.submitPerformance('comparison');
  assert.equal(restored.calls.find(call=>call.options.method==='POST').options.body,h.calls[0].options.body);
});

test('a definitive validation response clears the pending request and unlocks corrections',async()=>{
  const h=app({fetch:()=>response({error:{code:'INVALID_INPUT',message:'데이터를 다시 확인해 주세요.'}},400)}),a=h.api;ready(a);
  await a.submitPerformance('single');
  assert.equal(a.performanceView('single').pendingSubmission,null);
  assert.equal(JSON.parse(h.storage.get(storedKey)).single.pendingSubmission,null);
  assert.equal(a.performanceView('single').error,'데이터를 다시 확인해 주세요.');
  a.updateSingleEvaluationDraft('model','model-b');assert.equal(a.state.singleEvaluationDraft.model,'model-b');
  assert.equal(h.calls.length,1);
});

test('missing evaluation APIs show server errors and never substitute local data or results',async()=>{
  const h=app({fetch:()=>response({error:{code:'NOT_FOUND',message:'평가 API를 찾을 수 없어요. <retry>'}},404)}),a=h.api;
  Object.assign(a.state,{route:'single-evaluation',dbReady:true,connection:'ready',models:[{id:'model-a'}]});
  a.state.singleEvaluationDraft.model='model-a';
  await a.loadPerformanceData();
  assert.equal(a.state.performance.dataset,null);assert.equal(a.state.performance.loaded,false);
  assert.deepEqual(plain(a.state.performance.history),[]);assert.deepEqual(plain(a.performanceView('single').cases),[]);
  assert.match(a.performanceDatasetPanel('single'),/role="alert"[^>]*>평가 API를 찾을 수 없어요\. &lt;retry&gt;/);
  assert.match(a.performanceHistory('single'),/&lt;retry&gt;/);
  assert.match(runButton(a.performanceSummary('single'),'single'),/\bdisabled\b/);
  await a.loadPerformanceData();assert.equal(h.calls.length,2);
  assert.ok(h.calls.every(call=>call.options.method==='GET'));
});

test('reload reopens the persisted evaluation from DB history without submitting a new evaluation',async()=>{
  const storage=new Map([[storedKey,JSON.stringify({single:{selectedId:runId,pendingSubmission:null}})]]);
  const h=app({storage,fetch:path=>{
    if(path==='/api/performance/dataset')return response({dataset:dataset()});
    if(path==='/api/performance')return response({evaluations:[summary({id:otherRunId,name:'다른 최신 기록'}),summary()]});
    if(path.includes('/cases?'))return casesResponse([caseRow()],5);
    assert.equal(path,'/api/performance/'+runId);return response(summary());
  }}),a=h.api;Object.assign(a.state,{route:'single-evaluation',dbReady:true});
  await a.loadPerformanceData();
  assert.equal(a.performanceView('single').selectedId,runId);assert.equal(a.performanceView('single').summary.id,runId);
  assert.equal(a.performanceView('single').cases.length,1);assert.ok(h.calls.every(call=>call.options.method==='GET'));
});

test('late summary and case responses cannot overwrite a newly selected evaluation',async()=>{
  const oldSummary=deferred(),oldCases=deferred();
  const h=app({fetch:path=>{
    if(path==='/api/performance/'+runId)return oldSummary.promise;
    if(path.startsWith('/api/performance/'+runId+'/cases?'))return oldCases.promise;
    if(path==='/api/performance/'+otherRunId)return response(summary({id:otherRunId,name:'새 평가'}));
    if(path.startsWith('/api/performance/'+otherRunId+'/cases?'))return casesResponse([caseRow({title:'새 결과'})]);
    throw new Error('Unexpected request');
  }}),a=h.api;ready(a);a.performanceView('single').selectedId=runId;
  const summaryRequest=a.refreshPerformanceRun('single'),caseRequest=a.loadPerformanceCases('single');
  await a.openPerformanceRun('single',otherRunId);
  oldSummary.resolve(response(summary({name:'오래된 평가'})));oldCases.resolve(casesResponse([caseRow({title:'오래된 결과'})]));
  await Promise.all([summaryRequest,caseRequest]);
  assert.equal(a.performanceView('single').summary.id,otherRunId);
  assert.equal(a.performanceView('single').cases[0].title,'새 결과');
  assert.equal(a.performanceView('single').loading,false);assert.equal(a.performanceView('single').casesLoading,false);
  assert.doesNotMatch(h.elements['#performance-cases'].outerHTML,/오래된 결과/);
});

test('polling runs only for active evaluations on evaluation routes and stops when leaving',async()=>{
  const inflight=deferred();
  const h=app({fetch:()=>inflight.promise}),a=h.api;ready(a);
  Object.assign(a.performanceView('single'),{selectedId:runId,summary:summary({status:'running',finished:1,running:1,queued:3})});
  a.schedulePerformancePoll('single');assert.equal(pollTimers(h).length,1);assert.equal(h.calls.length,0);
  a.schedulePerformancePoll('single');assert.equal(pollTimers(h).length,1);
  const [timerKey,timer]=pollTimers(h)[0];h.timers.delete(timerKey);timer.fn();
  assert.equal(h.calls.length,1);
  h.location.hash='#email';a.route();
  const before=h.elements['#single-evaluation-summary'].innerHTML;
  inflight.resolve(response(summary({status:'running',finished:2,running:1,queued:2})));await flush();
  assert.equal(a.state.route,'email');assert.equal(pollTimers(h).length,0);assert.equal(h.timers.size,0);
  assert.equal(h.elements['#single-evaluation-summary'].innerHTML,before);
  a.schedulePerformancePoll('single');assert.equal(pollTimers(h).length,0);
  a.state.route='single-evaluation';a.performanceView('single').summary=summary();a.schedulePerformancePoll('single');
  assert.equal(pollTimers(h).length,0);
});

test('cancel targets queued work, keeps in-flight status, and supersedes an older progress request',async()=>{
  const oldProgress=deferred(),cancel=deferred();let progressCalls=0;
  const h=app({fetch:(path,options)=>{
    if(path.endsWith('/cancel'))return cancel.promise;
    if(path.includes('/cases?'))return casesResponse();
    if(path==='/api/performance/'+runId){
      if(++progressCalls===1)return oldProgress.promise;
      return response(summary({status:'running',total:5,finished:4,succeeded:0,cancelled:4,running:1,queued:0}));
    }
    throw new Error('Unexpected request: '+path);
  }}),a=h.api;ready(a);
  Object.assign(a.performanceView('single'),{selectedId:runId,summary:summary({status:'running',total:5,finished:0,succeeded:0,running:1,queued:4})});
  const progress=a.refreshPerformanceRun('single'),cancelling=a.cancelPerformance('single');
  await a.cancelPerformance('single');assert.equal(h.calls.filter(call=>call.options.method==='POST').length,1);
  const request=h.calls.find(call=>call.options.method==='POST');
  assert.equal(request.path,'/api/performance/'+runId+'/cancel');assert.deepEqual(JSON.parse(request.options.body),{});
  assert.equal(a.performanceView('single').summary.running,1);assert.equal(a.performanceView('single').cancelling,true);
  assert.match(a.performanceResults('single'),/이미 전송된 요청은 완료 후 결과를 저장해요/);
  cancel.resolve(response({cancelled:4}));await cancelling;
  oldProgress.resolve(response(summary({status:'running',finished:0,succeeded:0,running:1,queued:4})));await progress;
  assert.equal(a.performanceView('single').summary.status,'running');assert.equal(a.performanceView('single').summary.running,1);
  assert.equal(a.performanceView('single').summary.queued,0);assert.equal(a.performanceView('single').summary.cancelled,4);
  assert.equal(a.performanceView('single').cancelling,false);assert.equal(pollTimers(h).length,1);
  a.stopPerformancePolling();
});

test('missing measurements remain unknown and common-sample absence never appears as zero quality',()=>{
  const h=app(),a=h.api;ready(a,'comparison');
  const noScore={model:'model-a',total:5,finished:1,succeeded:0,gradedCount:0,failed:1,cancelled:0,
    meanRougeL:null,meanFactCoverage:null,confirmedFacts:0,totalFacts:0,needsReviewFacts:0,
    latencyMsMean:null,latencyMsMedian:null,tokens:{inputMean:null,outputMean:null,totalMean:null,reportedCount:0},
    common:{count:0,meanRougeL:null,latencyMsMean:null,totalTokensMean:null}};
  const data=summary({models:['model-a','model-b'],commonCompletedEmails:0,modelResults:[noScore,{...noScore,model:'model-b'}]});
  const html=a.performanceModelTable(data);
  assert.match(html,/공통 이메일 0건/);assert.match(html,/<td>—<\/td>/);
  assert.match(html,/채점 완료 0건/);
  assert.match(html,/— \/ — ms/);assert.match(html,/— \/ — \/ —/);
  assert.doesNotMatch(html,/NaN|undefined|0\.0%/);
  const failed=a.performanceCaseCard(caseRow({status:'failed',quality:null,usage:null,latencyMs:null,error:{message:'모델 호출 실패'}}));
  assert.match(failed,/실행 오류는 0점으로 처리하지 않아요/);assert.doesNotMatch(failed,/0\.0%/);
  assert.equal(a.performanceNumber(null),'—');assert.equal(a.performancePercent(null),'—');
  assert.equal(a.performanceNumber(0),'0');assert.equal(a.performancePercent(0),'0.0%');
});

test('server-provided text is escaped in results, facts, history, errors, and preliminary labels are visible',()=>{
  const h=app(),a=h.api;ready(a);
  const hostile='<img src=x onerror="alert(1)"> & \'quoted\'';
  const row=caseRow({sourceId:hostile,title:hostile,requestedModel:hostile,returnedModel:hostile+'2',
    body:hostile,reference:hostile,summary:hostile,warnings:[hostile],error:{code:hostile,message:hostile},
    quality:{score:null,version:hostile,factCoverage:null,facts:[{fact:hostile,status:'review_needed',reason:hostile,evidence:{excerpt:hostile}}]}});
  const result=summary({name:hostile,createdAt:hostile,preliminary:true,stopReason:hostile,
    modelResults:[{model:hostile,tokens:{},common:{}}]});
  Object.assign(a.performanceView('single'),{selectedId:runId,summary:result,cases:[row],casesTotal:1,error:hostile});
  a.state.performance.history=[result];a.state.performance.datasetError=hostile;a.performanceView('single').name=hostile;
  const parts=[a.performanceResults('single'),a.performanceHistory('single'),a.performanceDatasetPanel('single'),a.performanceSummary('single')];
  for(const html of parts){assert.doesNotMatch(html,/<img\b|<script\b/);assert.match(html,/&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);}
  assert.match(parts[0],/초안 기준 예비 평가/);assert.match(parts[0],/미확인은 오답이 아니며 모델 순위 지표로 사용하지 않아요/);
  assert.match(parts[0],/<p class="performance-warning">&lt;img/);
  assert.match(parts[0],/이메일 원문/);assert.match(parts[0],/기준 요약/);assert.match(parts[0],/모델 요약/);assert.match(parts[0],/핵심 정보 확인 근거/);
});

test('progress updates preserve the focused controls and refresh the current detail page once on completion',async()=>{
  let progressCalls=0;
  const h=app({fetch:path=>{
    if(path.includes('/cases?')){assert.match(path,/offset=20&limit=20$/);return casesResponse([caseRow({title:'마지막 결과'})],100);}
    return response(++progressCalls===1?summary({status:'running',total:100,finished:50,running:1,queued:49}):summary({total:100,finished:100}));
  }}),a=h.api;ready(a);
  h.elements['#performance-result-summary']=element();
  const name=element({value:'새 평가 이름 입력 중',focused:true});h.elements['#performance-name']=name;
  const openDetails=element({dataset:{performanceCase:id(2001)},open:true});
  const replacedDetails=element({dataset:{performanceCase:id(2001)},open:false});
  h.queryLists['#performance-cases details[open]']=[openDetails];
  h.queryLists['#performance-cases details[data-performance-case]']=[replacedDetails];
  Object.assign(a.performanceView('single'),{selectedId:runId,offset:20,cases:[caseRow()],casesTotal:100,
    summary:summary({status:'running',total:100,finished:0,running:1,queued:99})});
  await a.refreshPerformanceRun('single');
  assert.equal(h.calls.filter(call=>call.path.includes('/cases?')).length,0);
  assert.equal(h.elements['#performance-results'].outerHTML,'');assert.equal(h.elements['#performance-cases'].outerHTML,'');
  assert.equal(h.elements['#performance-name'],name);assert.equal(name.value,'새 평가 이름 입력 중');assert.equal(name.focused,true);
  await a.refreshPerformanceRun('single');
  assert.equal(h.calls.filter(call=>call.path.includes('/cases?')).length,1);
  assert.equal(a.performanceView('single').offset,20);assert.equal(a.performanceView('single').cases[0].title,'마지막 결과');
  assert.equal(replacedDetails.open,true);assert.equal(pollTimers(h).length,0);
  await a.refreshPerformanceRun('single');assert.equal(h.calls.filter(call=>call.path.includes('/cases?')).length,1);
});

test('malformed progress and detail responses expose errors without manufacturing scores',async()=>{
  const h=app({fetch:path=>path.includes('/cases?')?response({cases:'invalid',total:0}):response({id:runId,status:'completed'})}),a=h.api;
  ready(a);a.performanceView('single').selectedId=runId;
  await a.refreshPerformanceRun('single');assert.equal(a.performanceView('single').summary,null);
  assert.match(a.performanceView('single').error,/결과 형식/);assert.match(a.performanceResults('single'),/role="alert"/);
  await a.loadPerformanceCases('single');assert.deepEqual(plain(a.performanceView('single').cases),[]);
  assert.match(a.performanceView('single').caseError,/상세 결과 형식/);
  assert.equal(pollTimers(h).length,0);
});
