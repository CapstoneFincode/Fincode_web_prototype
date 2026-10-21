import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as categoryTools from '../public/categories.js';
import * as seeds from '../public/data.js';
const {categorySeeds,normalizeCategoryData,categoryGroups,selectionPlan}=categoryTools;
const row=(id,extra={})=>({...seeds.attackSeeds[0],id,dbId:'db-'+id,...extra});
const backup=()=>({schemaVersion:1,normal:structuredClone(seeds.normalSeeds),attacks:structuredClone(seeds.attackSeeds),plans:[],notes:[],categories:structuredClone(categorySeeds)});

test('category expansion includes every eligible scenario and explicitly counts exclusions',()=>{
  const attacks=[row('a'),row('b'),row('disabled',{enabled:false}),row('file',{channel:'attachment'}),row('other',{categoryId:'special-token'})];
  const plan=selectionPlan(attacks,['instruction-override'],3);
  assert.deepEqual(plan.runnable.map(a=>a.id),['a','b']);assert.equal(plan.calls,6);assert.equal(plan.disabled,1);assert.equal(plan.unsupported,1);assert.equal(plan.error,'');
  const groups=categoryGroups(categorySeeds,attacks);assert.equal(groups.find(c=>c.id==='system-prompt-leak').total,0);
});
test('over-limit categories are never truncated and repeat boundaries are explicit',()=>{
  const attacks=Array.from({length:11},(_,i)=>row('a'+i));const p=selectionPlan(attacks,['instruction-override'],1);
  assert.equal(p.runnable.length,11);assert.match(p.error,/최대 10건/);assert.match(selectionPlan(attacks.slice(0,1),['instruction-override'],11).error,/1~10회/);
  assert.equal(selectionPlan(attacks.slice(0,10),['instruction-override'],10).calls,100);
});
test('catalog preserves empty custom categories and legacy records use uncategorized',()=>{
  const legacy=row('legacy');delete legacy.categoryId;
  const catalog=[...categorySeeds,{id:'custom',name:'다국어 우회'}];const normalized=normalizeCategoryData(catalog,[legacy]);
  assert.equal(normalized.attacks[0].categoryId,'uncategorized');assert.equal(normalized.categories.at(-1).name,'다국어 우회');
  assert.deepEqual(normalizeCategoryData(normalized.categories,normalized.attacks),normalized);
});
test('catalog rejects unknown references, duplicates, format controls, and normalization overflow',()=>{
  assert.throws(()=>normalizeCategoryData(categorySeeds,[row('x',{categoryId:'missing'})]));
  assert.throws(()=>normalizeCategoryData([...categorySeeds,{id:'custom',name:'  지시 덮어쓰기  '}],[]));
  assert.throws(()=>normalizeCategoryData([...categorySeeds,{id:'custom',name:'ﬃ'.repeat(30)}],[]));
  assert.throws(()=>normalizeCategoryData([...categorySeeds,{id:'custom',name:'A\u200bB'}],[]));
  assert.throws(()=>normalizeCategoryData([{id:'custom',name:'미분류'}],[]));
  assert.throws(()=>normalizeCategoryData(Array.from({length:1000},(_,i)=>({id:'cat'+i,name:'Category '+i})),[]));
});
const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/gm,'')
  .replace(/^/,'const { historicalRuns, attackSeeds, normalSeeds, historicalTypes }=seeds; const { categorySeeds, categoryNameKey, categoryFor, normalizeCategoryData, categoryGroups, selectionPlan }=categoryTools;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');
function app({saved=null,preview=false,elements={},fetcher=()=>{throw Error('Unexpected network');}}={}){
  const writes=[],toasts=[];
  const context=vm.createContext({toasts,seeds,categoryTools,structuredClone,crypto:webcrypto,console,setTimeout,clearTimeout,AbortController,
    FormData:class {constructor(form){this.values=form.values||{}} get(key){return this.values[key]??null;}},
    fetch:fetcher,localStorage:{getItem:()=>saved?JSON.stringify(saved):null,setItem:(key,value)=>writes.push({key,value:JSON.parse(value)})},
    document:{querySelector:s=>s.startsWith('meta[')?preview?{}:null:elements[s]||null,querySelectorAll:()=>[],addEventListener(){}},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#experiments'}});
  vm.runInContext(source+`\nshell=()=>{};renderRuntime=()=>{};toast=(msg)=>toasts.push(msg);refreshJobs=async()=>{};showDatabaseExperiment=async()=>{};
  globalThis.api={state,pages,validateImport,filteredData,adoptDatabase,persist,refreshConnection,submitDatabaseExperiment,recordForm,savePlan,categorySelectionSummary,actions};`,context);
  // Use the context that contains both stubs and state.
  return {api:context.api,writes,toasts};
}
test('category filter combines with route filter/search and sorts without dropping categories',()=>{
  const {api:a}=app();a.state.tab='attacks';a.state.categoryFilter='instruction-override';
  assert.equal(a.filteredData().length,2);a.state.filter='direct';assert.equal(a.filteredData().length,1);
  a.state.query='없는 단어';assert.equal(a.filteredData().length,0);
  a.state.query='';a.state.filter='all';a.state.dataSort='name';const names=Array.from(a.filteredData(),a=>a.name);assert.deepEqual(names,[...names].sort((a,b)=>a.localeCompare(b,'ko')));
  assert.match(a.pages.data(),/카테고리 추가/);assert.match(a.pages.experiments(),/name="categoryIds"/);assert.doesNotMatch(a.pages.experiments(),/name="attack-choice"|name="scenarioIds"/);
});
test('custom category catalog and assignment survive export-shaped persistence/reload',()=>{
  const d=backup();d.categories.push({id:'custom-empty',name:'아직 비어 있는 분류'},{id:'custom-filled',name:'다국어 우회'});d.attacks[0].categoryId='custom-filled';
  const first=app({saved:d});first.api.persist();const saved=first.writes.at(-1).value;const next=app({saved});
  assert.equal(next.api.state.categories.length,9);assert.equal(next.api.state.attacks[0].categoryId,'custom-filled');
  assert.equal(next.api.state.categories.find(c=>c.id==='custom-empty').name,'아직 비어 있는 분류');
});
test('older backends cannot silently discard category changes',()=>{
  const {api:a,toasts}=app();a.state.dbConfigured=true;a.state.dbReady=true;a.adoptDatabase({normal:[],attacks:[row('legacy')]});a.state.tab='attacks';
  assert.equal(a.state.dbCategorySupport,false);a.recordForm();assert.match(toasts.at(-1),/백엔드로 업데이트/);
  assert.match(a.pages.data(),/카테고리 저장 기능이 아직 적용되지/);
});
test('preview mode never requests health/models and uses isolated browser storage',async()=>{
  let calls=0;const {api:a,writes}=app({preview:true,fetcher:()=>{calls++;throw Error('network');}});await a.refreshConnection();a.persist();
  assert.equal(calls,0);assert.equal(a.state.dbChecked,true);assert.equal(a.state.dbReady,false);assert.equal(writes.at(-1).key,'fincode-ui-categories-preview-v1');assert.match(a.pages.experiments(),/실제 모델을 호출하지/);
});
test('DB submission resolves selected category to exact eligible scenario IDs',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Category run',kind:'attack',model:'test-model',emailId:'email-id',repeat:'2'}};
  const elements={'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':{textContent:''}};
  const {api:a}=app({elements,fetcher:async(url,options)=>{calls.push({url,payload:JSON.parse(options.body)});return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:['instruction-override'],attacks:[row('a'),row('b'),row('file',{channel:'attachment'}),row('disabled',{enabled:false}),row('c',{categoryId:'special-token'})]});
  await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/experiments');assert.deepEqual(calls[0].payload.scenarioIds,['db-a','db-b']);assert.equal(calls[0].payload.repeat,2);
  a.state.attacks=Array.from({length:11},(_,i)=>row(String(i)));await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.match(elements['#db-submit-error'].textContent,/최대 10건/);
});
test('normal experiment ignores attack category selection',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Normal run',kind:'normal',model:'test-model',emailId:'email-id',repeat:'2'}};
  const elements={'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':{textContent:''}};
  const {api:a}=app({elements,fetcher:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:[],attacks:[]});await a.submitDatabaseExperiment();assert.deepEqual(calls[0].scenarioIds,[]);assert.equal(calls[0].kind,'normal');
});
test('saved plan freezes chosen catalog labels and all scenario members',()=>{
  const elements={'#repeat':{checkValidity:()=>true},'#plan-title':{value:'Frozen category plan'}};
  const {api:a}=app({elements});a.state.categoryIds=['instruction-override'];a.state.repeat=2;a.savePlan();
  assert.equal(a.state.plans[0].scenarios.length,2);assert.equal(a.state.plans[0].categories[0].name,'지시 덮어쓰기');
  a.state.attacks[0].name='Changed';assert.notEqual(a.state.plans[0].scenarios[0].name,'Changed');
});
