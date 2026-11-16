import test from 'node:test';
import assert from 'node:assert/strict';
import {createComparisonState,comparisonRecordKey,comparisonSelections,validateComparison,duplicateSelectionWarning,renderComparisonResult,renderComparisonContent} from '../public/result-comparison.js';

const row=(experimentId,model,defense)=>({experimentId,model,defense,name:experimentId,createdAt:'2026-11-16T00:00:00Z',status:'completed',runCount:100});
const fixture=[row('baseline','model-a','off'),row('filter-a','model-a','filter'),row('separate-a','model-a','separate'),row('filter-b','model-b','filter')];
const normal=()=>{const state=createComparisonState('normal');Object.assign(state,{ready:true,loaded:true,model:'model-a',records:fixture,normalRecords:{off:comparisonRecordKey(fixture[0]),filter:comparisonRecordKey(fixture[1])}});return state;};
const response=({records=fixture,page=1,totalPages=1,total=records.length}={})=>({records,page,pageSize:50,totalPages,total});
const result=()=>({kind:'normal',comparable:true,commonCases:5,commonRuns:5,groups:[{experimentId:'baseline',name:'기준',model:'model-a',defense:'off',selectedRuns:8,matchedRuns:5,excludedRuns:3,metrics:{meanRougeL:0,normalRejectionRate:0,meanFactCoverage:null,latencyMsMean:null,totalTokensMean:null,gradedCount:5}},{experimentId:'filter-a',name:'필터',model:'model-a',defense:'filter',selectedRuns:10,matchedRuns:5,excludedRuns:5,metrics:{meanRougeL:.5,normalRejectionRate:20,meanFactCoverage:null,latencyMsMean:null,totalTokensMean:null,gradedCount:5}}],warnings:[]});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fakeRoot(){const handlers={};return {innerHTML:'',matches:()=>true,contains:()=>true,querySelector:()=>null,addEventListener:(type,fn)=>{handlers[type]=fn;},removeEventListener:(type,fn)=>{if(handlers[type]===fn)delete handlers[type];},change(dataset,value,checked){handlers.change?.({target:{id:'',dataset,value,checked}});},click(action,extra={}){const target={dataset:{rcAction:action,...extra},disabled:false};target.closest=()=>target;handlers.click?.({target});},handlers};}
let instance=0;
const fresh=()=>import(`../public/result-comparison.js?test=${++instance}`);

test('normal comparison selects one model OFF and valid ON records only',()=>{
  const state=normal();assert.equal(validateComparison(state),'');assert.deepEqual(comparisonSelections(state),[{experimentId:'baseline',model:'model-a',defense:'off'},{experimentId:'filter-a',model:'model-a',defense:'filter'}]);
  state.normalRecords.filter=comparisonRecordKey(fixture[3]);assert.match(validateComparison(state),/기록을 선택/);
  state.normalModes=[];assert.match(validateComparison(state),/한 개 이상/);
});
test('attack comparison permits independent OFF/ON, paths, same models and exact duplicates',()=>{
  const state=createComparisonState('attack');Object.assign(state,{ready:true,records:fixture,slots:[{model:'model-a',defense:'off',vector:'direct',record:comparisonRecordKey(fixture[0])},{model:'model-a',defense:'filter',vector:'indirect',record:comparisonRecordKey(fixture[1])}]});
  assert.equal(validateComparison(state),'');assert.deepEqual(comparisonSelections(state).map(item=>[item.model,item.defense,item.vector]),[['model-a','off','direct'],['model-a','filter','indirect']]);assert.equal(duplicateSelectionWarning(state),'');
  state.slots[1]={...state.slots[0]};assert.equal(validateComparison(state),'');assert.match(duplicateSelectionWarning(state),/중복 선택/);
  state.slots=Array(6).fill(state.slots[0]);assert.match(validateComparison(state),/2개에서 5개/);state.slots=state.slots.slice(0,2);state.slots[0]={...state.slots[0],vector:'invalid'};assert.match(validateComparison(state),/공격 경로/);
});
test('comparison is unavailable during every catalog load, including pagination',()=>{const state=normal();state.loading=true;assert.match(validateComparison(state),/불러오고/);assert.match(renderComparisonContent(state),/data-rc-action="compare"[^>]+disabled/);});
test('missing metrics stay missing; scales and zero-baseline deltas are explicit',()=>{
  const html=renderComparisonResult(result());assert.match(html,/50\.0%/);assert.match(html,/기준 대비 \+50\.0%p/);assert.match(html,/20\.0%/);assert.doesNotMatch(html,/NaN|Infinity|undefined/);assert.match(html,/측정된 기록이 없어요/);
  assert.ok(html.indexOf('공통 5 / 선택 8 / 제외 3건')<html.indexOf('rc-chart-grid'));assert.match(html,/규칙 기반 보조 지표/);
});
test('no common data has no zero charts, and persisted record labels are escaped',()=>{
  const empty=renderComparisonResult({...result(),comparable:false,warnings:['<script>bad()</script>']});assert.match(empty,/공통 기록이 없어요/);assert.doesNotMatch(empty,/rc-bar-track|<script>/);assert.match(empty,/&lt;script&gt;/);
  const state=normal();state.records=[...state.records,row('<img src=x onerror=bad()>','model-a','filter')];assert.doesNotMatch(renderComparisonContent(state),/<img src=x/);
});
test('unconnected state has no enabled load-more and performs no request',async()=>{
  const module=await fresh(),root=fakeRoot();let calls=0;const cleanup=module.mountResultComparison(root,{kind:'normal',ready:false,api:async()=>{calls++;}});await flush();assert.equal(calls,0);assert.doesNotMatch(root.innerHTML,/data-rc-action="more"/);assert.match(root.innerHTML,/DB 연결 후/);cleanup();assert.deepEqual(root.handlers,{});
});
test('changing defense during delayed metadata load does not strand loading state',async()=>{
  const module=await fresh(),root=fakeRoot(),pending=deferred();const cleanup=module.mountResultComparison(root,{kind:'attack',ready:true,api:()=>pending.promise});root.change({rcDefense:'0'},'separate');pending.resolve(response());await flush();assert.match(root.innerHTML,/value="separate" selected/);assert.doesNotMatch(root.innerHTML,/aria-busy="true"/);assert.match(root.innerHTML,/data-rc-action="reload"\s*>/);cleanup();
});
test('load-more preserves a selected record and blocks comparison until metadata finishes',async()=>{
  const module=await fresh(),root=fakeRoot(),more=deferred(),calls=[];const additional=row('chosen-earlier','model-a','filter');const api=async(path,method,body)=>{calls.push({path,method,body});if(method==='POST')return result();if(path.includes('page=2'))return more.promise;return response({records:[...fixture,additional],totalPages:2,total:6});};
  const cleanup=module.mountResultComparison(root,{kind:'normal',ready:true,api});await flush();root.change({rcRecord:'filter'},comparisonRecordKey(additional));root.click('more');root.click('compare');assert.equal(calls.filter(call=>call.method==='POST').length,0);more.resolve(response({records:[row('older','model-a','filter')],page:2,totalPages:2,total:6}));await flush();root.click('compare');await flush();const posted=calls.find(call=>call.method==='POST');assert.equal(posted.body.selections[1].experimentId,'chosen-earlier');assert.equal(posted.path,'/api/results/compare');assert.equal(posted.body.vector,'all');cleanup();
});
test('a late comparison response cannot repaint a changed selection or unmounted page',async()=>{
  const module=await fresh(),root=fakeRoot(),pending=deferred();const cleanup=module.mountResultComparison(root,{kind:'normal',ready:true,api:async(path,method)=>method==='POST'?pending.promise:response()});await flush();root.click('compare');root.change({rcMode:'separate'},'',true);const changed=root.innerHTML;pending.resolve(result());await flush();assert.equal(root.innerHTML,changed);assert.doesNotMatch(root.innerHTML,/COMPARISON REPORT/);cleanup();assert.deepEqual(root.handlers,{});
  const module2=await fresh(),root2=fakeRoot(),catalog=deferred();const close=module2.mountResultComparison(root2,{kind:'normal',ready:true,api:()=>catalog.promise});close();const old=root2.innerHTML;catalog.resolve(response());await flush();assert.equal(root2.innerHTML,old);
});
const attackMetrics=(asr=0)=>({total:10,completed:8,blocked:1,errors:1,pending:0,cancelled:0,successes:asr===null?0:asr===0?0:1,failures:asr===null?0:asr===0?8:7,indeterminate:0,unjudged:asr===null?8:0,judged:asr===null?0:8,asr,latencyMsMean:150,latencyCount:8,inputTokensMean:100,outputTokensMean:20,totalTokensMean:120,tokensCount:8});
const attackResult=()=>({kind:'attack',analysisMode:'independent',comparable:true,commonCases:null,commonRuns:null,groups:[{index:0,experimentId:'a',name:'공격A',model:'same-model',defense:'off',vector:'direct',sourceRuns:40,selectedRuns:10,excludedRuns:30,scenarioCount:3,matchedRuns:null,metrics:attackMetrics(0)},{index:1,experimentId:'b',name:'공격B',model:'same-model',defense:'filter',vector:'indirect',sourceRuns:30,selectedRuns:15,excludedRuns:15,scenarioCount:4,matchedRuns:null,metrics:attackMetrics(null)}],categories:[{id:null,name:'미분류',groups:[{index:0,runCount:10,scenarioCount:3,metrics:attackMetrics(0)},{index:1,runCount:0,scenarioCount:0,metrics:null}]},{id:'null',name:'<img src=x>',groups:[{index:0,runCount:0,scenarioCount:0,metrics:null},{index:1,runCount:15,scenarioCount:4,metrics:attackMetrics(null)}]}],warnings:[]});
test('independent attack results distinguish conditions, no data, zero ASR, and unjudged categories',()=>{
  const html=renderComparisonResult(attackResult());
  assert.match(html,/대상 1 · same-model · 방어 미적용 · 직접 인젝션/);assert.match(html,/대상 2 · same-model · 입력 필터링 · 간접 인젝션/);
  assert.doesNotMatch(html,/공통 실행|공통 공격 시나리오|공통 표본/);assert.match(html,/0\.0%/);assert.match(html,/기록 없음/);assert.match(html,/확정 판정 없음/);assert.match(html,/경로 필터 제외 30건/);assert.match(html,/시나리오 4개/);assert.match(html,/조건과 표본이 다를/);assert.doesNotMatch(html,/<img src=x>/);
  const other=renderComparisonResult(attackResult(),{categoryIndex:'1'});assert.match(other,/value="1" selected/);assert.match(other,/&lt;img src=x&gt; · 공격 성공률/);assert.match(html,/value="0" selected>미분류/);
});
test('normal screen rename keeps common-cohort behavior and attack cards have no shared controls',()=>{
  assert.match(renderComparisonContent(normal()),/<h1>단일 모델 성능 평가 간 비교<\/h1>/);assert.match(renderComparisonResult(result()),/조건별 공통 실행/);
  const state=createComparisonState('attack');const html=renderComparisonContent(state);assert.doesNotMatch(html,/공통 방어 조건|rc-attack-mode|rc-attack-vector/);assert.match(html,/비교 대상 추가/);assert.equal((html.match(/data-rc-defense=/g)||[]).length,2);assert.equal((html.match(/data-rc-vector=/g)||[]).length,2);
});
test('independent slot changes send per-selection paths and category switching never calls the API',async()=>{
  const module=await fresh(),root=fakeRoot(),calls=[];
  const cleanup=module.mountResultComparison(root,{kind:'attack',ready:true,api:async(path,method,body)=>{calls.push({path,method,body});return method==='POST'?attackResult():response();}});await flush();
  root.change({rcModel:'1'},'model-a');root.change({rcDefense:'1'},'filter');root.change({rcVector:'0'},'direct');root.change({rcVector:'1'},'indirect');root.click('compare');await flush();
  const posted=calls.find(call=>call.method==='POST');assert.equal(Object.hasOwn(posted.body,'vector'),false);assert.deepEqual(posted.body.selections.map(item=>[item.model,item.defense,item.vector]),[['model-a','off','direct'],['model-a','filter','indirect']]);
  const count=calls.length;root.change({rcCategory:'true'},'1');assert.equal(calls.length,count);assert.match(root.innerHTML,/value="1" selected/);root.click('category',{rcIndex:'0'});assert.equal(calls.length,count);assert.match(root.innerHTML,/value="0" selected>미분류/);cleanup();
});
test('changing one attack path during a pending comparison prevents stale results',async()=>{
  const module=await fresh(),root=fakeRoot(),pending=deferred();const cleanup=module.mountResultComparison(root,{kind:'attack',ready:true,api:async(path,method)=>method==='POST'?pending.promise:response()});await flush();root.click('compare');root.change({rcVector:'0'},'indirect');const html=root.innerHTML;pending.resolve(attackResult());await flush();assert.equal(root.innerHTML,html);assert.doesNotMatch(html,/OVERALL COMPARISON/);assert.match(html,/value="indirect" selected/);cleanup();
});
