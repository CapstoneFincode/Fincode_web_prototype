import test from 'node:test';
import assert from 'node:assert/strict';
import {createComparisonState,comparisonRecordKey,comparisonSelections,validateComparison,renderComparisonResult,renderComparisonContent} from '../public/result-comparison.js';

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
test('attack comparison requires 2–5 distinct models with one recorded ON condition',()=>{
  const state=createComparisonState('attack');Object.assign(state,{ready:true,records:fixture,slots:[{model:'model-a',record:comparisonRecordKey(fixture[1])},{model:'model-b',record:comparisonRecordKey(fixture[3])}]});assert.equal(validateComparison(state),'');
  state.slots[1].model='model-a';assert.match(validateComparison(state),/서로 다른/);state.slots=Array(6).fill({model:'model-a'});assert.match(validateComparison(state),/2개에서 5개/);state.attackMode='off';state.slots=Array(2).fill({});assert.match(validateComparison(state),/공통으로 적용/);
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
  const module=await fresh(),root=fakeRoot(),pending=deferred();const cleanup=module.mountResultComparison(root,{kind:'attack',ready:true,api:()=>pending.promise});root.change({rcField:'attackMode'},'separate');pending.resolve(response());await flush();assert.match(root.innerHTML,/value="separate" selected/);assert.doesNotMatch(root.innerHTML,/aria-busy="true"/);assert.match(root.innerHTML,/data-rc-action="reload"\s*>/);cleanup();
});
test('load-more preserves a selected record and blocks comparison until metadata finishes',async()=>{
  const module=await fresh(),root=fakeRoot(),more=deferred(),calls=[];const additional=row('chosen-earlier','model-a','filter');const api=async(path,method,body)=>{calls.push({path,method,body});if(method==='POST')return result();if(path.includes('page=2'))return more.promise;return response({records:[...fixture,additional],totalPages:2,total:6});};
  const cleanup=module.mountResultComparison(root,{kind:'normal',ready:true,api});await flush();root.change({rcRecord:'filter'},comparisonRecordKey(additional));root.click('more');root.click('compare');assert.equal(calls.filter(call=>call.method==='POST').length,0);more.resolve(response({records:[row('older','model-a','filter')],page:2,totalPages:2,total:6}));await flush();root.click('compare');await flush();const posted=calls.find(call=>call.method==='POST');assert.equal(posted.body.selections[1].experimentId,'chosen-earlier');assert.equal(posted.path,'/api/results/compare');assert.equal(posted.body.vector,'all');cleanup();
});
test('a late comparison response cannot repaint a changed selection or unmounted page',async()=>{
  const module=await fresh(),root=fakeRoot(),pending=deferred();const cleanup=module.mountResultComparison(root,{kind:'normal',ready:true,api:async(path,method)=>method==='POST'?pending.promise:response()});await flush();root.click('compare');root.change({rcMode:'separate'},'',true);const changed=root.innerHTML;pending.resolve(result());await flush();assert.equal(root.innerHTML,changed);assert.doesNotMatch(root.innerHTML,/COMPARISON REPORT/);cleanup();assert.deepEqual(root.handlers,{});
  const module2=await fresh(),root2=fakeRoot(),catalog=deferred();const close=module2.mountResultComparison(root2,{kind:'normal',ready:true,api:()=>catalog.promise});close();const old=root2.innerHTML;catalog.resolve(response());await flush();assert.equal(root2.innerHTML,old);
});
