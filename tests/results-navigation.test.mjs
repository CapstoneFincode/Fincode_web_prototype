import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as seeds from '../public/data.js';
import * as categoryTools from '../public/categories.js';
import {renderResultComparison} from '../public/result-comparison.js';

const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/gm,'')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');
function setup(fetcher=()=>{throw Error('Unexpected history or model request');}){
  const mounted=[],disposed=[],elements=new Map();
  const element=selector=>{
    if(!elements.has(selector))elements.set(selector,{innerHTML:'',hidden:false,attributes:{},setAttribute(k,v){this.attributes[k]=v;},querySelector:()=>null,querySelectorAll:()=>[]});
    return elements.get(selector);
  };
  ['#app','#main','#results-menu-toggle','#results-subnav'].forEach(element);
  const context=vm.createContext({seeds,categoryTools,renderResultComparison,structuredClone,console,crypto:webcrypto,AbortController,setTimeout,clearTimeout,
    mountResultComparison:(root,options)=>{mounted.push(options);return ()=>disposed.push(options.kind);},
    fetch:fetcher,
    localStorage:{getItem:()=>null,setItem(){}},
    document:{addEventListener(){},querySelector:s=>elements.get(s)||null,querySelectorAll:()=>[]},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#normal-result-comparison'}});
  vm.runInContext(`const {historicalRuns,attackSeeds,normalSeeds,historicalTypes}=seeds;const {categorySeeds,categoryNameKey,categoryFor,normalizeCategoryData,categoryGroups,categoryScopes,categoryScope,categoryLayout,selectionPlan,attackVectors,vectorCategoryKey,vectorSelectionPlan}=categoryTools;\n`+source+`
    globalThis.api={state,route,shell,bindPage,resultsNavigation,toggleResultsMenu,pages,openComparisonRecord};`,context);
  return {api:context.api,context,mounted,disposed,element};
}

test('comparison deep links expand the three result children and retain the existing history route',()=>{
  const {api,context,element,mounted}=setup();
  for(const [route,kind,label] of [['normal-result-comparison','normal','단일 모델 성능 평가 간 비교'],['attack-result-comparison','attack','공격 실험 결과 성능 비교']]){
    context.location.hash='#'+route;api.route();
    assert.equal(api.state.route,route);assert.equal(api.state.resultsMenuOpen,true);
    const html=element('#app').innerHTML;
    assert.match(html,/aria-controls="results-subnav"/);
    assert.match(html,new RegExp(`href="#${route}" class="active" aria-current="page"`));
    assert.match(html,new RegExp(label));assert.match(html,/href="#results"/);
    assert.equal(mounted.at(-1).kind,kind);
  }
  assert.ok(api.pages.results);assert.doesNotMatch(api.pages.results(),/results-defense-placeholder/);
});

test('record details ignore superseded clicks and responses after leaving a comparison page',async()=>{
  const pending=[],opened=[];
  const {api,context}=setup(()=>new Promise(resolve=>pending.push(resolve)));
  context.opened=opened;
  vm.runInContext('openPerformanceRun=async(mode,id)=>opened.push({mode,id});',context);
  api.state.route='normal-result-comparison';
  const ids=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222'];
  const respond=(index,body)=>pending[index]({ok:true,status:200,json:async()=>body});
  const summary=id=>({id,models:['model-a'],modelResults:[],total:1,finished:1});
  const first=api.openComparisonRecord({experimentId:ids[0],kind:'normal'});
  const second=api.openComparisonRecord({experimentId:ids[1],kind:'normal'});
  respond(0,summary(ids[0]));await first;assert.equal(opened.length,0);
  respond(1,summary(ids[1]));await second;assert.equal(opened[0].id,ids[1]);
  api.state.route='attack-result-comparison';
  const attack=api.openComparisonRecord({experimentId:ids[0],kind:'attack'});
  api.state.route='normal-result-comparison';api.shell();
  // This response would require a modal if a stale request were incorrectly rendered.
  respond(2,{experiment:{purpose:'attack'},runs:[]});await attack;
  assert.equal(opened.length,1);
});

test('new result routes mount only the comparison controller and dispose it on rerender',()=>{
  const {api,mounted,disposed,element}=setup();
  Object.assign(api.state,{route:'normal-result-comparison',dbReady:true});
  api.shell();assert.equal(mounted.length,1);assert.equal(mounted[0].ready,true);
  api.state.route='attack-result-comparison';api.shell();
  assert.deepEqual(disposed,['normal']);assert.equal(mounted.length,2);
  api.toggleResultsMenu();assert.equal(element('#results-menu-toggle').attributes['aria-expanded'],'true');
  api.toggleResultsMenu();assert.equal(element('#results-subnav').hidden,true);
});
