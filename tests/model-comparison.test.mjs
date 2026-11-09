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
  vm.runInContext(source+'\nshell=()=>{shellCalls++;};toast=message=>toasts.push(message);globalThis.api={state,pages,evaluationNavigation,toggleEvaluationMenu,comparisonPage,comparisonSummary,updateComparisonDraft,route,bindPage,actions,summaryRequest,gtCount};',context);
  return {api:context.api,context,elements,calls,location,toasts,queryLists};
}
const plain=value=>JSON.parse(JSON.stringify(value));
const openingTag=(html,name,id)=>(html.match(new RegExp('<'+name+'\\b[^>]*>','g'))||[]).find(tag=>tag.includes('id="'+id+'"'));
const linkFor=(html,hash)=>(html.match(/<a\b[^>]*>/g)||[]).find(tag=>tag.includes('href="'+hash+'"'));

test('email and comparison deep links open their shared group and mark only the current child',()=>{
  const {api:a,location}=app();
  for(const route of ['email','comparison']){
    a.state.evaluationMenuOpen=false;location.hash='#'+route;a.route();
    assert.equal(a.state.route,route);assert.equal(a.state.evaluationMenuOpen,true);
    const html=a.evaluationNavigation();
    assert.match(html,/모델 별 성능 평가/);
    assert.match(openingTag(html,'button','evaluation-menu-toggle'),/aria-expanded="true"/);
    assert.match(openingTag(html,'button','evaluation-menu-toggle'),/class="[^"]*\bactive\b/);
    assert.match(linkFor(html,'#'+route),/aria-current="page"/);
    assert.doesNotMatch(linkFor(html,'#'+(route==='email'?'comparison':'email')),/aria-current="page"/);
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
  a.updateComparisonDraft('modelA','model-a');
  a.updateComparisonDraft('modelB','model-b');
  a.updateComparisonDraft('defense','separate');
  assert.deepEqual(plain(a.state.comparisonDraft),{modelA:'model-a',modelB:'model-b',defense:'separate'});
  assert.equal(a.state.liveModel,'summary-model');
  assert.equal(a.state.model,'attack-plan-model');
  assert.equal(a.state.defense,'both');
  assert.deepEqual(plain(a.summaryRequest()),request);
  assert.equal(a.summaryRequest().defense,'off');
  location.hash='#email';a.route();location.hash='#comparison';a.route();
  assert.deepEqual(plain(a.state.comparisonDraft),{modelA:'model-a',modelB:'model-b',defense:'separate'});
  assert.equal(calls.length,0);
});

test('unknown draft models, defense choices, and fields cannot alter settings or trigger requests',()=>{
  const {api:a,calls}=app();
  a.state.models=[{id:'model-a'},{id:'model-b'}];
  a.updateComparisonDraft('modelA','model-a');
  const before=plain(a.state.comparisonDraft);
  for(const [field,value] of [['modelA','unavailable-model'],['modelB','unavailable-model'],
      ['defense','invented-defense'],['liveModel','model-b'],['__proto__','model-b']]){
    a.updateComparisonDraft(field,value);
    assert.deepEqual(plain(a.state.comparisonDraft),before);
  }
  assert.equal(calls.length,0);
});

test('comparison configuration is labeled as preparation and cannot execute the deferred comparison',()=>{
  const {api:a,calls}=app();
  a.state.models=[{id:'model-a'},{id:'model-b'}];a.state.connection='ready';
  a.updateComparisonDraft('modelA','model-a');a.updateComparisonDraft('modelB','model-b');
  for(const defense of ['off','filter','separate','both']){
    a.updateComparisonDraft('defense',defense);
    const html=a.pages.comparison();
    assert.match(html,/<h1>모델 별 비교<\/h1>/);
    assert.match(html,/준비|구현 예정/);
    assert.match(openingTag(html,'button','comparison-run'),/\bdisabled\b/);
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
  a.updateComparisonDraft('modelA',unsafe);a.updateComparisonDraft('modelB','second&model');
  for(const html of [a.pages.comparison(),a.comparisonSummary()]){
    assert.doesNotMatch(html,/<img|onerror="/);
    assert.match(html,/&lt;img/);assert.match(html,/second&amp;model/);
  }
});

test('comparison counts distinguish pending candidates and samples from reviewed normal labels',()=>{
  const {api:a}=app();
  a.state.dbReady=true;
  a.state.normal=[...structuredClone(seeds.normalSeeds),
    {...seeds.normalSeeds[0],id:'pending-a',provenance:'user',reviewStatus:'pending'},
    {...seeds.normalSeeds[1],id:'pending-b',provenance:'user',reviewStatus:'pending'}];
  let html=a.pages.comparison();
  assert.match(html,/<dt>정상 라벨 검토 완료<\/dt><dd>0건<\/dd>/);
  assert.match(html,/<dt>검토 대기<\/dt><dd>2건<\/dd>/);
  assert.match(html,/<dt>기존 합성 예시<\/dt><dd>5건<\/dd>/);
  assert.match(html,/<strong>7<\/strong><span>건 · DB 정상 이메일/);
  a.state.normal.find(row=>row.id==='pending-a').reviewStatus='reviewed';
  html=a.pages.comparison();
  assert.match(html,/<dt>정상 라벨 검토 완료<\/dt><dd>1건<\/dd>/);
  assert.match(html,/<dt>검토 대기<\/dt><dd>1건<\/dd>/);
  assert.match(html,/정상 라벨 검토와 기준 요약 검수는 별개/);
});

test('bound model and defense controls update only the draft summary without submitting requests',()=>{
  const {api:a,queryLists,elements,calls}=app();
  const modelA=element({value:'model-a',dataset:{comparisonField:'modelA'}});
  const modelB=element({value:'model-b',dataset:{comparisonField:'modelB'}});
  const defense=element({value:'filter',checked:true});
  queryLists['[data-comparison-field]']=[modelA,modelB];
  queryLists['input[name="comparison-defense"]']=[defense];
  Object.assign(a.state,{route:'comparison',connection:'ready',models:[{id:'model-a'},{id:'model-b'}]});
  a.bindPage();
  modelA.listeners.change({target:modelA});modelB.listeners.change({target:modelB});
  defense.listeners.change({target:defense});
  assert.deepEqual(plain(a.state.comparisonDraft),{modelA:'model-a',modelB:'model-b',defense:'filter'});
  assert.match(elements['#comparison-summary'].innerHTML,/입력 탐지 · 구현 예정/);
  defense.checked=false;defense.value='both';defense.listeners.change({target:defense});
  assert.equal(a.state.comparisonDraft.defense,'filter');
  assert.equal(calls.length,0);
});
