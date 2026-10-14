import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as seeds from '../public/data.js';

const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/, 'const { historicalRuns, attackSeeds, normalSeeds, historicalTypes } = seeds;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/, '');
function app(saved=null,fetcher=()=>{throw new Error('Unexpected API request');}){
  const listeners={};
  const context=vm.createContext({seeds:structuredClone({...seeds}),structuredClone,console,crypto:webcrypto,setTimeout,clearTimeout,AbortController,fetch:fetcher,
    localStorage:{getItem:()=>saved?JSON.stringify(saved):null,setItem(){}},
    document:{addEventListener:(name,handler)=>listeners[name]=handler,querySelector:()=>null,querySelectorAll:()=>[]},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#overview'},listeners});
  vm.runInContext(source+'\nrenderRuntime=()=>{};shell=()=>{};toast=()=>{};globalThis.api={state,pages,gtCount,validateImport,filteredResults,historyTable,chart,esc,refreshConnection,runSummary,cancelSummary,summaryRequest,canSummarize,showSummary,formatTokens};',context);
  return context.api;
}
function backup(){return {schemaVersion:1,normal:structuredClone(seeds.normalSeeds),attacks:structuredClone(seeds.attackSeeds),plans:[],notes:[]};}

test('six recorded experiments preserve source arithmetic and the disputed original',()=>{
  assert.equal(seeds.historicalRuns.length,6);
  for(const r of seeds.historicalRuns)assert.equal(Number((100*r.successes/r.attempts).toFixed(2)),r.asr);
  const disputed=seeds.historicalRuns.find(r=>r.id==='hist-deepseek-extended');
  assert.equal(disputed.asr,61.22);
  assert.match(disputed.reviewNote,/53\.06/);
});
test('seed examples do not count as reviewed Ground Truth',()=>{
  const a=app();assert.equal(a.gtCount(),0);
  a.state.normal.push({...seeds.normalSeeds[0],id:'user-reviewed',provenance:'user',reviewStatus:'reviewed'});
  assert.equal(a.gtCount(),1);
  a.state.normal.push({...seeds.normalSeeds[0],id:'user-pending',provenance:'user',reviewStatus:'pending'});
  assert.equal(a.gtCount(),1);
});
test('all six screens render without keys or invented token/latency results',()=>{
  const a=app();
  for(const [name,page] of Object.entries(a.pages)){
    const html=page();assert.ok(html.length>1000,name);assert.match(html,/<h1>/,name);assert.doesNotMatch(html,/undefined|NaN/,name);
  }
  assert.match(a.pages.email(),/실제 API 응답 후 측정값/);
  assert.match(a.pages.experiments(),/미실행/);
});

const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const liveResult=()=>({mode:'live',summary:'실제 요약',model:'gateway-model',requestedModel:'gateway-model',provider:'baze',requestId:'test-request',createdAt:'2026-10-14T04:00:00Z',promptVersion:'email-baseline-v1',defense:'off',usage:{inputTokens:21,outputTokens:9,totalTokens:30,reasoningTokens:null},latencyMs:1515,finishReason:'stop',warnings:[]});
function enable(a){Object.assign(a.state,{connection:'ready',configured:true,models:[{id:'gateway-model',ownedBy:'test'}],liveModel:'gateway-model',emailTitle:'회의 안내',email:'내일 오후 3시 연구실에서 만나요.'});}

test('a missing server key prevents model lookup and real summary requests',async()=>{
  const calls=[];const a=app(null,async(url)=>{calls.push(url);return response({configured:false});});
  await a.refreshConnection();
  assert.deepEqual(calls,['/api/health']);assert.equal(a.state.connection,'missing');assert.equal(a.canSummarize(),false);
  a.state.email='본문';await a.runSummary();assert.equal(calls.length,1);assert.match(a.pages.email(),/BAZE_API_KEY/);
});
test('live models come only from the server and stay independent of historical plan selection',async()=>{
  const calls=[];const a=app(null,async(url)=>{calls.push(url);return response(url.endsWith('health')?{configured:true}:{models:[{id:'real-model',ownedBy:'baze'}]});});
  const historicalModel=a.state.model;await a.refreshConnection();
  assert.deepEqual(calls,['/api/health','/api/models']);assert.equal(a.state.liveModel,'real-model');assert.equal(a.state.model,historicalModel);
  a.state.model='plan-model';assert.equal(a.state.liveModel,'real-model');assert.equal(a.state.connection,'ready');
  assert.match(a.pages.email(),/<option value="real-model" selected>/);
});
test('real summary submits one snapshot with baseline defense, prevents duplicate calls, and escapes output',async()=>{
  let resolve;const calls=[];const a=app(null,(url,options)=>{calls.push({url,options});return new Promise(r=>resolve=r);});enable(a);a.state.defense='both';
  const pending=a.runSummary();await a.runSummary();assert.equal(calls.length,1);assert.equal(a.state.running,true);
  assert.match(a.pages.email(),/<fieldset class="email-controls" disabled>/);
  assert.deepEqual(JSON.parse(calls[0].options.body),{model:'gateway-model',email:{subject:'회의 안내',body:'내일 오후 3시 연구실에서 만나요.'},defense:'off',attachments:[]});
  assert.equal(calls[0].options.headers.Authorization,undefined);
  resolve(response({...liveResult(),summary:'<img src=x onerror=alert(1)>',usage:{inputTokens:21,outputTokens:null,totalTokens:null,reasoningTokens:null}}));await pending;
  assert.equal(a.state.running,false);assert.equal(a.state.summary.mode,'live');assert.equal(a.state.lastLiveResult.input.email.body,'내일 오후 3시 연구실에서 만나요.');
  assert.match(a.pages.email(),/1\.51초/);assert.match(a.pages.email(),/출력 토큰<strong>—<\/strong>/);assert.doesNotMatch(a.pages.email(),/<img src=x/);
  a.state.email='새 입력';assert.equal(a.state.lastLiveResult.input.email.body,'내일 오후 3시 연구실에서 만나요.');
});
test('failed requests preserve reported consumption without presenting a successful summary or retrying',async()=>{
  let calls=0;const a=app(null,async()=>{calls++;return response({error:{code:'EMPTY_RESPONSE',message:'요약 본문이 없어요.'},usage:{inputTokens:20,outputTokens:5,totalTokens:25,reasoningTokens:5},latencyMs:2100},502);});enable(a);
  await a.runSummary();assert.equal(calls,1);assert.equal(a.state.summary,null);assert.equal(a.state.running,false);assert.equal(a.state.emailError.code,'EMPTY_RESPONSE');
  assert.match(a.pages.email(),/실패한 요청의 측정값/);assert.match(a.pages.email(),/전체 토큰<strong>25<\/strong>/);assert.match(a.pages.email(),/2\.10초/);
  assert.equal(a.state.lastLiveResult.mode,'error');assert.equal(a.state.lastLiveResult.usage.totalTokens,25);
});
test('cancellation ignores a late response and allows a subsequent request to finish independently',async()=>{
  const pendingFetches=[];const a=app(null,()=>new Promise(resolve=>pendingFetches.push(resolve)));enable(a);
  const old=a.runSummary();a.cancelSummary();assert.equal(a.state.running,false);assert.equal(a.state.emailError.code,'CANCELLED');
  const current=a.runSummary();pendingFetches[0](response({...liveResult(),summary:'취소한 이전 응답'}));await old;
  assert.equal(a.state.running,true);assert.equal(a.state.summary,null);
  pendingFetches[1](response({...liveResult(),summary:'현재 응답'}));await current;assert.equal(a.state.summary.summary,'현재 응답');
});
test('unexpected input mutation cannot display an obsolete response as current',async()=>{
  let resolve;const a=app(null,()=>new Promise(r=>resolve=r));enable(a);const pending=a.runSummary();a.state.email='다른 원문';resolve(response(liveResult()));await pending;
  assert.equal(a.state.summary,null);assert.equal(a.state.emailError.code,'STALE_INPUT');
});
test('attachments block real requests and saved reference summaries have no invented metrics',async()=>{
  let calls=0;const a=app(null,async()=>{calls++;return response(liveResult());});enable(a);a.state.files=[{name:'document.pdf',size:123}];
  await a.runSummary();assert.equal(calls,0);assert.match(a.state.emailError.message,/첨부파일/);
  a.state.files=[];a.state.email=seeds.normalSeeds[0].body;a.state.emailTitle=seeds.normalSeeds[0].title;a.showSummary();
  assert.equal(a.state.summary.mode,'reference');assert.equal(calls,0);assert.match(a.pages.email(),/저장된 기준 요약/);assert.match(a.pages.email(),/입력 토큰<strong>—<\/strong>/);
  assert.equal(a.formatTokens(null),'—');assert.equal(a.formatTokens(0),'0');
});
test('stored content is escaped on list and detail-oriented result renders',()=>{
  const a=app();a.state.normal.push({...seeds.normalSeeds[0],id:'x',title:'<img src=x onerror=alert(1)>',body:'<script>alert(1)</script>'});
  const html=a.pages.data();assert.doesNotMatch(html,/<img src=x/);assert.match(html,/&lt;img src=x/);
  a.state.notes.push({id:'n',title:'<svg onload=alert(1)>',body:'<script>alert(1)</script>',date:'2026-10-12'});
  assert.doesNotMatch(a.pages.settings(),/<script>/);
});
test('imports reject invalid schemas, invalid attack paths, fake seed review states and missing fields',()=>{
  const a=app();assert.doesNotThrow(()=>a.validateImport(backup()));
  const malformed=backup();malformed.normal[0].reference=null;assert.throws(()=>a.validateImport(malformed));
  const wrong=backup();wrong.attacks[0].channel='email';assert.throws(()=>a.validateImport(wrong));
  const fake=backup();fake.normal[0].reviewStatus='reviewed';assert.throws(()=>a.validateImport(fake));
  const duplicate=backup();duplicate.normal.push(duplicate.normal[0]);assert.throws(()=>a.validateImport(duplicate));
  assert.throws(()=>a.validateImport({schemaVersion:2}));
});
test('saved plans must be unexecuted and have bounded integer repetition counts',()=>{
  const a=app();const d=backup();const p={id:'p',title:'Plan',model:'unverified-label',defense:'off',repeat:3,scenarios:[d.attacks[0]],status:'not_run',createdAt:'2026-10-12'};d.plans=[p];assert.doesNotThrow(()=>a.validateImport(d));
  p.status='succeeded';assert.throws(()=>a.validateImport(d));p.status='not_run';p.repeat=10000;assert.throws(()=>a.validateImport(d));
});
test('restoring a custom-only dataset selects an existing record and malformed storage falls back safely',()=>{
  const d=backup();d.normal=[{...d.normal[0],id:'custom-only',provenance:'user',reviewStatus:'pending'}];
  assert.equal(app(d).state.selectedSample,'custom-only');
  assert.equal(app({schemaVersion:1,normal:'bad',attacks:[]}).state.normal.length,5);
  const empty=backup();empty.normal=[];assert.equal(app(empty).state.selectedSample,'');
});
test('result filters export the same subset that is displayed',()=>{
  const a=app();a.state.resultGroup='5';assert.equal(a.filteredResults().length,2);
  a.state.resultModel='gemini-3.5-flash-lite';assert.equal(a.filteredResults().length,1);
  assert.equal(a.filteredResults()[0].asr,63.27);
});
