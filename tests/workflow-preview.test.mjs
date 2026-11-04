import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync,mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';
import {webcrypto} from 'node:crypto';
import * as seeds from '../public/data.js';
import * as categoryTools from '../public/categories.js';
import * as workflowModule from '../preview/attacktest/workflow-preview.js';

const systemPrompt=JSON.parse(readFileSync(new URL('../config/email-baseline.json',import.meta.url),'utf8')).systemPrompt;
const scenario=(id,extra={})=>({...seeds.attackSeeds[0],id,name:id,payload:'저장된 공격문 '+id,expectedOutput:'SAVED_MARKER',evaluationMode:'exact',...extra});
const input=()=>({id:'preview-1',name:'선택한 구성',model:'화면 확인용 모델',email:structuredClone(seeds.normalSeeds[0]),categories:structuredClone(categoryTools.categorySeeds),systemPrompt,scenarios:[scenario('direct'),scenario('indirect',{vector:'indirect',channel:'email'})]});
test('workflow preview uses saved payload and criterion, with direct and indirect messages each once',()=>{
  const config=input(),detail=workflowModule.createWorkflowPreview(config);
  assert.equal(detail.exampleOnly,true);assert.equal(detail.runs.length,2);
  const [direct,indirect]=detail.runs;
  assert.equal(direct.input_snapshot.messages.length,3);assert.equal(direct.input_snapshot.messages[2].content,config.scenarios[0].payload);
  assert.equal(indirect.input_snapshot.messages.length,2);assert.ok(indirect.input_snapshot.messages[1].content.endsWith(config.scenarios[1].payload));
  assert.equal(direct.input_snapshot.successCriterion.expectedOutput,'SAVED_MARKER');assert.equal(direct.evaluations[0].boolean_value,true);
  assert.equal(direct.repeat_index,1);assert.equal(indirect.repeat_index,1);assert.deepEqual(direct.calls,[]);assert.equal(direct.service_total_tokens,null);assert.equal(direct.backend_latency_ms,null);
  config.scenarios[0].payload='나중에 수정';config.email.body='나중에 수정';
  assert.equal(direct.input_snapshot.attackScenario.payload,'저장된 공격문 direct');assert.notEqual(direct.input_snapshot.email.body,config.email.body);
});
test('manual rules remain unjudged; sample response does not invent human review',()=>{
  const config=input();config.scenarios=[scenario('manual',{evaluationMode:'manual',expectedOutput:'IGNORED'}),scenario('leak',{evaluationMode:'system_prompt_leak'})];
  const detail=workflowModule.createWorkflowPreview(config);assert.deepEqual(detail.runs[0].evaluations,[]);assert.equal(detail.runs[1].input_snapshot.successCriterion.type,'system_prompt_leak');
  assert.equal(detail.runs[0].input_snapshot.successCriterion.expectedOutput,undefined);
});
const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8').replace(/^import[^\n]+\n/gm,'')
  .replace('export function validateImport','function validateImport').replace(/route\(\);\s*refreshConnection\(\);\s*$/,'')
  .replace("await import('./workflow-preview.js')",'workflowModule');
function app({workflow=true}={}){
  const elements={'#db-experiment-form':{reportValidity:()=>true,values:{name:'선택 결과',model:'sample-model',emailId:seeds.normalSeeds[0].id}},'#db-submit-error':{},'[data-action="workflow-preview"]':{}};
  for(const selector of ['#db-kind','#db-model','#db-normal-repeat'])elements[selector]={listeners:{},addEventListener(type,fn){this.listeners[type]=fn;}};
  elements['#db-experiment-form'].addEventListener=()=>{};
  const fetched=[],toasts=[],shown=[];
  const context=vm.createContext({seeds,categoryTools,workflowModule,structuredClone,crypto:webcrypto,console,setTimeout,clearTimeout,AbortController,shown,toasts,
    FormData:class{constructor(f){this.values=f.values}get(k){return this.values[k]}},
    localStorage:{getItem:()=>null},location:{hash:'#experiments'},window:{addEventListener(){},scrollTo(){}},
    document:{querySelector:s=>s.includes('name="fincode-preview"')?{}:s.includes('name="fincode-workflow-preview"')?(workflow?{}:null):elements[s]||null,querySelectorAll:()=>[],addEventListener(){}},
    fetch:async path=>{fetched.push(path);assert.equal(path,'./baseline.json');return {ok:true,json:async()=>({systemPrompt})}}});
  const setup='const {historicalRuns,attackSeeds,normalSeeds,historicalTypes}=seeds;const {categorySeeds,categoryNameKey,categoryFor,normalizeCategoryData,categoryGroups,categoryScopes,categoryScope,categoryLayout,selectionPlan,attackVectors,vectorCategoryKey,vectorSelectionPlan}=categoryTools;';
  vm.runInContext(setup+source+'\nshell=()=>{};toast=msg=>toasts.push(msg);showWorkflowReport=id=>shown.push(id);globalThis.api={state,pages,bindPage,workflowReports,previewSelectedExperiment,workflowHistoryPanel};',context);
  return {...context.api,elements,fetched,toasts,shown};
}
test('workflow preview reuses the existing experiment form without a prompt or verdict input',()=>{
  const a=app(),html=a.pages.experiments();
  assert.match(html,/id="db-experiment-form"/);assert.match(html,/기준 정상 이메일/);assert.match(html,/workflow-preview/);assert.match(html,/선택 시나리오 결과 미리보기/);
  assert.doesNotMatch(html,/id="attack-input"|id="response"|name="evidence"|name="evaluationMode"/);
  assert.equal(a.state.dbReady,false);assert.equal(a.state.dbConfigured,false);
  assert.doesNotMatch(app({workflow:false}).pages.experiments(),/data-action="workflow-preview"/);
});
test('selected registration-ordered scenarios feed history and result detail without API calls',async()=>{
  const a=app();a.state.attacks=[scenario('first'),scenario('second'),scenario('disabled',{enabled:false})];a.state.vectorIds=['direct'];a.state.categoryIds=['direct:instruction-override'];a.state.categoryCounts={'direct:instruction-override':2};
  await a.previewSelectedExperiment();assert.deepEqual(a.fetched,['./baseline.json']);assert.equal(a.workflowReports.length,1);
  const detail=a.workflowReports[0];assert.deepEqual(detail.runs.map(r=>r.input_snapshot.attackScenario.key),['first','second']);assert.equal(detail.runs.length,2);assert.equal(a.shown[0],detail.experiment.id);
  assert.match(a.workflowHistoryPanel(),/선택 결과/);assert.match(a.workflowHistoryPanel(),/workflow-detail/);assert.equal(a.state.jobs.length,0);assert.equal(a.state.jobSubmitting,false);
});
test('normal selection or invalid category count cannot generate attack preview or spend tokens',async()=>{
  const a=app();a.state.dbKind='normal';await a.previewSelectedExperiment();assert.equal(a.fetched.length,0);a.state.dbKind='attack';a.state.vectorIds=[];await a.previewSelectedExperiment();assert.equal(a.fetched.length,0);assert.equal(a.workflowReports.length,0);assert.ok(a.elements['#db-submit-error'].textContent);
});
test('existing form handlers bind in workflow mode and preserve normal-only repeat rules',()=>{
  const a=app();a.state.route='experiments';assert.doesNotThrow(()=>a.bindPage());
  assert.equal(a.elements['#db-normal-repeat'].disabled,true);assert.equal(a.elements['[data-action="workflow-preview"]'].disabled,false);
  a.elements['#db-kind'].listeners.change({target:{value:'normal'}});
  assert.equal(a.elements['#db-normal-repeat'].disabled,false);assert.equal(a.elements['[data-action="workflow-preview"]'].disabled,true);
  a.elements['#db-kind'].listeners.change({target:{value:'attack'}});assert.equal(a.elements['[data-action="workflow-preview"]'].disabled,false);
});
test('packaged home is the original dashboard and the manual judge is a secondary page',()=>{
  const dir=mkdtempSync(join(tmpdir(),'fincode-workflow-'));
  try{
    execFileSync('python',['scripts/package-attacktest-preview.py','--output',dir],{cwd:new URL('..',import.meta.url),encoding:'utf8'});
    const html=readFileSync(join(dir,'index.html'),'utf8');assert.match(html,/fincode-workflow-preview/);assert.match(html,/src="\.\/app.js"/);assert.doesNotMatch(html,/id="response"|id="test-form"/);
    assert.equal(readFileSync(join(dir,'dashboard.html'),'utf8'),html);assert.match(readFileSync(join(dir,'judge.html'),'utf8'),/개발자용 응답 판정기 검증/);assert.ok(readFileSync(join(dir,'workflow-preview.js'),'utf8'));
  }finally{rmSync(dir,{recursive:true,force:true});}
});
