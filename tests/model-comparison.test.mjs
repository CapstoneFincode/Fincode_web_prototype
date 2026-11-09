import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as seeds from '../public/data.js';
import * as categoryTools from '../public/categories.js';

const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/gm,'')
  .replace(/^/,'const { historicalRuns, attackSeeds, normalSeeds, historicalTypes } = seeds; const { categorySeeds, categoryNameKey, categoryFor, normalizeCategoryData, categoryGroups, categoryScopes, categoryScope, categoryLayout, selectionPlan, attackVectors, vectorCategoryKey, vectorSelectionPlan } = categoryTools;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');

function element(extra={}){
  const attributes={},classes=new Set(),listeners={};
  return {hidden:false,innerHTML:'',textContent:'',value:'',attributes,listeners,
    focus(){this.focused=true;},
    setAttribute(name,value){attributes[name]=String(value);},
    getAttribute(name){return attributes[name]??null;},
    classList:{add(name){classes.add(name);},remove(name){classes.delete(name);},
      contains(name){return classes.has(name);},
      toggle(name,force){const add=force??!classes.has(name);if(add)classes.add(name);else classes.delete(name);return add;}},
    addEventListener(name,handler){listeners[name]=handler;},...extra};
}
function app(){
  const elements={
    '#evaluation-menu-toggle':element(),
    '#evaluation-subnav':element({hidden:true}),
    '#comparison-summary':element(),
    '#single-evaluation-summary':element(),
    '#db-name':element({value:'작성 중인 공격 실험 이름'}),
  };
  const calls=[],toasts=[],queryLists={},location={hash:'#overview'};
  const context=vm.createContext({seeds:structuredClone({...seeds}),categoryTools,structuredClone,
    console,crypto:webcrypto,setTimeout,clearTimeout,AbortController,location,
    fetch:(...args)=>{calls.push(args);throw Error('Configuration must not call an API');},
    localStorage:{getItem:()=>null,setItem(){}},
    document:{addEventListener(){},querySelector:selector=>elements[selector]??null,
      querySelectorAll:selector=>queryLists[selector]??[],getElementById:id=>elements['#'+id]??null},
    window:{addEventListener(){},scrollTo(){}},toasts,shellCalls:0});
  vm.runInContext(source+'\nshell=()=>{shellCalls++;};toast=message=>toasts.push(message);globalThis.api={state,pages,evaluationNavigation,toggleEvaluationMenu,comparisonPage,comparisonSummary,updateComparisonDraft,addComparisonModel,removeComparisonModel,singleEvaluationSummary,updateSingleEvaluationDraft,route,bindPage,actions,summaryRequest,gtCount};',context);
  return {api:context.api,context,elements,calls,location,toasts,queryLists};
}
const plain=value=>JSON.parse(JSON.stringify(value));
const openingTag=(html,name,id)=>(html.match(new RegExp('<'+name+'\\b[^>]*>','g'))||[]).find(tag=>tag.includes('id="'+id+'"'));
const linkFor=(html,hash)=>(html.match(/<a\b[^>]*>/g)||[]).find(tag=>tag.includes('href="'+hash+'"'));

test('all evaluation deep links open their shared group and mark only the current child',()=>{
  const {api:a,location}=app();
  for(const route of ['email','single-evaluation','comparison']){
    a.state.evaluationMenuOpen=false;location.hash='#'+route;a.route();
    assert.equal(a.state.route,route);assert.equal(a.state.evaluationMenuOpen,true);
    const html=a.evaluationNavigation();
    assert.match(html,/<span>성능 평가<\/span>/);
    assert.doesNotMatch(html,/모델 별 성능 평가/);
    assert.match(openingTag(html,'button','evaluation-menu-toggle'),/aria-expanded="true"/);
    assert.match(openingTag(html,'button','evaluation-menu-toggle'),/class="[^"]*\bactive\b/);
    assert.match(linkFor(html,'#'+route),/aria-current="page"/);
    for(const other of ['email','single-evaluation','comparison'].filter(child=>child!==route)){
      assert.doesNotMatch(linkFor(html,'#'+other),/aria-current="page"/);
    }
  }
  location.hash='#missing';a.route();assert.equal(a.state.route,'overview');
});

test('collapsing the sidebar changes only disclosure state without rerendering or losing form input',()=>{
  const {api:a,context,elements}=app();
  a.state.evaluationMenuOpen=false;
  a.toggleEvaluationMenu();
  assert.equal(a.state.evaluationMenuOpen,true);
  assert.equal(elements['#evaluation-subnav'].hidden,false);
  assert.equal(elements['#evaluation-menu-toggle'].getAttribute('aria-expanded'),'true');
  a.toggleEvaluationMenu();
  assert.equal(a.state.evaluationMenuOpen,false);
  assert.equal(elements['#evaluation-subnav'].hidden,true);
  assert.equal(elements['#evaluation-menu-toggle'].getAttribute('aria-expanded'),'false');
  assert.equal(context.shellCalls,0);
  assert.equal(elements['#db-name'].value,'작성 중인 공격 실험 이름');
});

test('comparison choices remain drafts, preserve both existing execution settings, and never call a model',()=>{
  const {api:a,calls,location}=app();
  Object.assign(a.state,{models:[{id:'model-a'},{id:'model-b'},{id:'summary-model'}],
    liveModel:'summary-model',model:'attack-plan-model',defense:'both',emailTitle:'기존 제목',email:'기존 본문'});
  const request=plain(a.summaryRequest());
  a.updateComparisonDraft(0,'model-a');
  a.updateComparisonDraft(1,'model-b');
  a.updateComparisonDraft('defense','separate');
  assert.deepEqual(plain(a.state.comparisonDraft),{models:['model-a','model-b'],defense:'off'});
  assert.equal(a.state.liveModel,'summary-model');
  assert.equal(a.state.model,'attack-plan-model');
  assert.equal(a.state.defense,'both');
  assert.deepEqual(plain(a.summaryRequest()),request);
  assert.equal(a.summaryRequest().defense,'off');
  location.hash='#email';a.route();location.hash='#comparison';a.route();
  assert.deepEqual(plain(a.state.comparisonDraft),{models:['model-a','model-b'],defense:'off'});
  assert.equal(calls.length,0);
});

test('unknown draft models, defense choices, and fields cannot alter settings or trigger requests',()=>{
  const {api:a,calls}=app();
  a.state.models=[{id:'model-a'},{id:'model-b'}];
  a.updateComparisonDraft(0,'model-a');
  const before=plain(a.state.comparisonDraft);
  for(const [field,value] of [[0,'unavailable-model'],[1,'unavailable-model'],[-1,'model-a'],[2,'model-a'],[0.5,'model-a'],['0','model-a'],
      ['defense','invented-defense'],['defense','filter'],['defense','separate'],['defense','both'],['liveModel','model-b'],['__proto__','model-b']]){
    a.updateComparisonDraft(field,value);
    assert.deepEqual(plain(a.state.comparisonDraft),before);
  }
  assert.equal(calls.length,0);
});

test('comparison requires the evaluation dataset and keeps unimplemented defenses disabled',()=>{
  const {api:a,calls}=app();
  a.state.models=[{id:'model-a'},{id:'model-b'}];a.state.connection='ready';
  a.updateComparisonDraft(0,'model-a');a.updateComparisonDraft(1,'model-b');
  for(const defense of ['off','filter','separate','both']){
    a.updateComparisonDraft('defense',defense);
    const html=a.pages.comparison();
    assert.match(html,/<h1>모델 별 비교<\/h1>/);
    assert.equal(a.state.comparisonDraft.defense,'off');
    const run=openingTag(html,'button','comparison-run');
    assert.match(run,/\bdisabled\b/);
    assert.match(run,/data-action="performance-run"/);
    assert.match(run,/data-mode="comparison"/);
    assert.doesNotMatch(html,/비교 실행 · 준비 중/);
    for(const option of ['filter','separate','both']){
      const input=(html.match(/<input\b[^>]*>/g)||[]).find(tag=>tag.includes('name="comparison-defense"')&&tag.includes('value="'+option+'"'));
      assert.match(input,/\bdisabled\b/);
      assert.doesNotMatch(input,/\bchecked\b/);
    }
    assert.doesNotMatch(html,/undefined|NaN/);
  }
  assert.equal(calls.length,0);
});

test('summarizer retains its original controls without exposing comparison defense settings',()=>{
  const {api:a}=app();
  const html=a.pages.email();
  assert.match(html,/<h1>이메일 요약기<\/h1>/);
  assert.match(html,/id="live-model"/);assert.match(html,/id="email-body"/);
  assert.match(html,/data-action="live-summary"/);assert.match(html,/data-action="demo-summary"/);
  assert.doesNotMatch(html,/email-defense|class="field-label">방어 설정/);
  assert.equal(a.summaryRequest().defense,'off');
});

test('provider model labels are escaped in comparison inputs and selected summary',()=>{
  const {api:a}=app();
  const unsafe='<img src=x onerror=alert(1)>';
  a.state.models=[{id:unsafe},{id:'second&model'}];a.state.connection='ready';
  a.updateComparisonDraft(0,unsafe);a.updateComparisonDraft(1,'second&model');
  for(const html of [a.pages.comparison(),a.comparisonSummary()]){
    assert.doesNotMatch(html,/<img|onerror="/);
    assert.match(html,/&lt;img/);assert.match(html,/second&amp;model/);
  }
});

test('normal email examples and label approvals alone do not enable comparison without the evaluation dataset',()=>{
  const {api:a}=app();
  Object.assign(a.state,{dbReady:true,connection:'ready',models:[{id:'model-a'},{id:'model-b'}]});
  a.state.normal=[...structuredClone(seeds.normalSeeds),
    ...Array.from({length:100},(_,index)=>({...seeds.normalSeeds[0],id:'reviewed-'+index,provenance:'user',reviewStatus:'reviewed'}))];
  a.updateComparisonDraft(0,'model-a');a.updateComparisonDraft(1,'model-b');
  const html=a.pages.comparison();
  assert.match(openingTag(html,'button','comparison-run'),/\bdisabled\b/);
  assert.doesNotMatch(html,/<strong>105<\/strong>/);
});

test('bound model controls update only independent OFF settings without submitting requests',()=>{
  const {api:a,queryLists,elements,calls}=app();
  const modelA=element({value:'model-a',dataset:{comparisonIndex:'0'}});
  const modelB=element({value:'model-b',dataset:{comparisonIndex:'1'}});
  const defense=element({value:'filter',checked:true});
  queryLists['[data-comparison-index]']=[modelA,modelB];
  queryLists['input[name="comparison-defense"]']=[defense];
  Object.assign(a.state,{route:'comparison',connection:'ready',models:[{id:'model-a'},{id:'model-b'}]});
  a.bindPage();
  modelA.listeners.change({target:modelA});modelB.listeners.change({target:modelB});
  defense.listeners.change?.({target:defense});
  assert.deepEqual(plain(a.state.comparisonDraft),{models:['model-a','model-b'],defense:'off'});
  assert.match(elements['#comparison-summary'].innerHTML,/OFF/);
  defense.checked=false;defense.value='both';defense.listeners.change?.({target:defense});
  assert.equal(a.state.comparisonDraft.defense,'off');
  assert.equal(calls.length,0);
});

test('comparison grows from two to five, removes only extra slots, and preserves remaining choices',()=>{
  const {api:a,calls}=app();
  a.state.models=['a','b','c','d','e'].map(id=>({id}));a.state.connection='ready';
  assert.deepEqual(plain(a.state.comparisonDraft),{models:['',''],defense:'off'});
  a.updateComparisonDraft(0,'a');a.updateComparisonDraft(1,'b');
  a.updateComparisonDraft('defense','both');
  for(let index=2;index<5;index++){
    a.addComparisonModel();a.updateComparisonDraft(index,a.state.models[index].id);
  }
  a.addComparisonModel();
  assert.deepEqual(plain(a.state.comparisonDraft),{models:['a','b','c','d','e'],defense:'off'});
  const html=a.pages.comparison();
  assert.equal((html.match(/data-comparison-index=/g)||[]).length,5);
  assert.match((html.match(/<button\b[^>]*>/g)||[]).find(tag=>tag.includes('data-action="comparison-add-model"')),/\bdisabled\b/);
  for(const invalid of [0,1,-1,5,2.5,'2',null])a.removeComparisonModel(invalid);
  assert.deepEqual(plain(a.state.comparisonDraft.models),['a','b','c','d','e']);
  a.removeComparisonModel(3);
  assert.deepEqual(plain(a.state.comparisonDraft.models),['a','b','c','e']);
  a.addComparisonModel();
  assert.deepEqual(plain(a.state.comparisonDraft.models),['a','b','c','e','']);
  a.removeComparisonModel(4);a.removeComparisonModel(3);a.removeComparisonModel(2);a.removeComparisonModel(2);
  assert.deepEqual(plain(a.state.comparisonDraft),{models:['a','b'],defense:'off'});
  assert.equal(calls.length,0);
});

test('single evaluation preserves independent selections and cannot run without the evaluation dataset',()=>{
  const {api:a,calls,location}=app();
  Object.assign(a.state,{models:[{id:'single'},{id:'comparison'}],liveModel:'summarizer',model:'attack',defense:'both',connection:'ready'});
  const request=plain(a.summaryRequest());
  a.updateComparisonDraft(0,'comparison');a.updateComparisonDraft('defense','separate');
  const comparison=plain(a.state.comparisonDraft);
  a.updateSingleEvaluationDraft('model','single');a.updateSingleEvaluationDraft('defense','filter');
  assert.deepEqual(plain(a.state.singleEvaluationDraft),{model:'single',defense:'off'});
  for(const [field,value] of [['model','unknown'],['defense','unknown'],['liveModel','single'],['__proto__','single']]){
    a.updateSingleEvaluationDraft(field,value);
    assert.deepEqual(plain(a.state.singleEvaluationDraft),{model:'single',defense:'off'});
  }
  location.hash='#comparison';a.route();location.hash='#single-evaluation';a.route();
  assert.deepEqual(plain(a.state.singleEvaluationDraft),{model:'single',defense:'off'});
  assert.deepEqual(plain(a.state.comparisonDraft),comparison);
  assert.deepEqual(plain(a.summaryRequest()),request);
  assert.equal(a.state.model,'attack');assert.equal(a.state.defense,'both');
  const html=a.pages['single-evaluation']();
  assert.match(html,/<h1>단일 모델 성능 평가<\/h1>/);
  assert.equal((html.match(/data-single-evaluation-field="model"/g)||[]).length,1);
  const run=openingTag(html,'button','single-evaluation-run');
  assert.match(run,/\bdisabled\b/);
  assert.match(run,/data-action="performance-run"/);
  assert.match(run,/data-mode="single"/);
  assert.doesNotMatch(html,/data-action="(?:live-summary|db-run|comparison-add-model)"|undefined|NaN/);
  a.updateSingleEvaluationDraft('model','');
  assert.equal(a.state.singleEvaluationDraft.model,'');
  assert.equal(calls.length,0);
});

test('single evaluation model changes preserve comparison and ignore unavailable defense choices',()=>{
  const {api:a,queryLists,elements,calls}=app();
  const model=element({value:'model-a',dataset:{singleEvaluationField:'model'}});
  const defense=element({value:'both',checked:true});
  queryLists['[data-single-evaluation-field]']=[model];
  queryLists['input[name="single-evaluation-defense"]']=[defense];
  Object.assign(a.state,{route:'single-evaluation',connection:'ready',models:[{id:'model-a'}]});
  a.bindPage();model.listeners.change({target:model});defense.listeners.change?.({target:defense});
  assert.deepEqual(plain(a.state.singleEvaluationDraft),{model:'model-a',defense:'off'});
  assert.deepEqual(plain(a.state.comparisonDraft),{models:['',''],defense:'off'});
  assert.match(elements['#single-evaluation-summary'].innerHTML,/model-a/);
  defense.checked=false;defense.value='filter';defense.listeners.change?.({target:defense});
  assert.equal(a.state.singleEvaluationDraft.defense,'off');
  assert.equal(calls.length,0);
});

test('single evaluation escapes model labels and does not treat local examples as an evaluation dataset',()=>{
  const {api:a}=app();
  const model='<img src=x onerror=alert(1)>';
  a.state.models=[{id:model}];a.updateSingleEvaluationDraft('model',model);
  a.state.normal=[{...seeds.normalSeeds[0],id:'pending',provenance:'user',reviewStatus:'pending'}];
  const html=a.pages['single-evaluation']();
  assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
  assert.match(openingTag(html,'button','single-evaluation-run'),/\bdisabled\b/);
  assert.doesNotMatch(html,/평가 실행 · 준비 중/);
});

test('add and remove actions refresh model controls without rerendering the page',()=>{
  const {api:a,elements,queryLists,calls,context}=app();
  const addedModel=element({value:'new-model',dataset:{comparisonIndex:'2'}});
  elements['#comparison-model-fields']=element();
  elements['#comparison-model-2']=addedModel;
  queryLists['[data-comparison-index]']=[addedModel];
  Object.assign(a.state,{route:'comparison',connection:'ready',models:[{id:'new-model'}]});
  a.actions['comparison-add-model']();
  assert.equal((elements['#comparison-model-fields'].innerHTML.match(/data-comparison-index=/g)||[]).length,3);
  addedModel.listeners.change({target:addedModel});
  assert.equal(a.state.comparisonDraft.models[2],'new-model');
  assert.equal(addedModel.focused,true);
  a.actions['comparison-remove-model']({dataset:{index:'2'}});
  assert.equal((elements['#comparison-model-fields'].innerHTML.match(/data-comparison-index=/g)||[]).length,2);
  assert.equal(context.shellCalls,0);
  assert.equal(calls.length,0);
});
