import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as categoryTools from '../public/categories.js';
import * as seeds from '../public/data.js';
const {categorySeeds,normalizeCategoryData,categoryGroups,selectionPlan,registrationOrder}=categoryTools;
const row=(id,extra={})=>({...seeds.attackSeeds[0],id,dbId:'db-'+id,...extra});
const backup=()=>({schemaVersion:1,normal:structuredClone(seeds.normalSeeds),attacks:structuredClone(seeds.attackSeeds),plans:[],notes:[],categories:structuredClone(categorySeeds)});

test('category counts select distinct eligible scenarios and explicitly count exclusions',()=>{
  const attacks=[row('a'),row('b'),row('disabled',{enabled:false}),row('file',{channel:'attachment'}),row('other',{categoryId:'special-token'})];
  const plan=selectionPlan(attacks,['instruction-override'],{'instruction-override':2});
  assert.deepEqual(plan.runnable.map(a=>a.id),['a','b']);assert.equal(plan.calls,2);assert.equal(plan.disabled,1);assert.equal(plan.unsupported,1);assert.equal(plan.error,'');
  assert.deepEqual(selectionPlan(attacks,['instruction-override']).runnable.map(a=>a.id),['a']);
  assert.equal(categoryGroups(categorySeeds,attacks).find(c=>c.id==='system-prompt-leak').total,0);
});
test('counts reject zero, blank, fractional, excess, and nonnumeric values; total cap does not truncate',()=>{
  const attacks=Array.from({length:101},(_,i)=>row('a'+i));
  for(const count of [0,'', ' ',1.5,-1,'no',null,102])assert.ok(selectionPlan(attacks,['instruction-override'],{'instruction-override':count}).error,String(count));
  const tooMany=selectionPlan(attacks,['instruction-override'],{'instruction-override':101});
  assert.equal(tooMany.runnable.length,101);assert.match(tooMany.error,/최대 100개/);
  assert.equal(selectionPlan(attacks,['instruction-override'],{'instruction-override':'100'}).calls,100);
  assert.equal(selectionPlan(attacks,['instruction-override'],{'instruction-override':100}).error,'');
  const combined=selectionPlan([...attacks,row('other',{categoryId:'special-token'})],['instruction-override','special-token'],{'instruction-override':100,'special-token':1});
  assert.match(combined.error,/총 101건/);
});
test('registration order keeps legacy array order first, sorts timestamps stably, and removes duplicate IDs',()=>{
  const attacks=[row('new',{registeredAt:'2026-10-21T10:00:00Z'}),row('legacy-b'),row('old',{registeredAt:'2026-10-20T10:00:00Z'}),row('legacy-a'),row('tie',{registeredAt:'2026-10-20T10:00:00Z'})];
  assert.deepEqual(registrationOrder(attacks).map(a=>a.id),['legacy-b','legacy-a','old','tie','new']);
  assert.deepEqual(attacks.map(a=>a.id),['new','legacy-b','old','legacy-a','tie']);
  const plan=selectionPlan([...attacks,attacks[1]],['instruction-override','instruction-override'],{'instruction-override':4});
  assert.deepEqual(plan.runnable.map(a=>a.id),['legacy-b','legacy-a','old','tie']);assert.equal(plan.calls,4);
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
function app({saved=null,preview=false,elements={},queryLists={},fetcher=()=>{throw Error('Unexpected network');}}={}){
  const writes=[],toasts=[];
  const context=vm.createContext({toasts,seeds,categoryTools,structuredClone,crypto:webcrypto,console,setTimeout,clearTimeout,AbortController,
    FormData:class {constructor(form){this.values=form.values||{}} get(key){return this.values[key]??null;} [Symbol.iterator](){return Object.entries(this.values)[Symbol.iterator]();}},
    fetch:fetcher,localStorage:{getItem:()=>saved?JSON.stringify(saved):null,setItem:(key,value)=>writes.push({key,value:JSON.parse(value)})},
    document:{querySelector:s=>s.startsWith('meta[')?preview?{}:null:elements[s]||null,querySelectorAll:s=>queryLists[s]||[],getElementById:id=>elements['#'+id]||null,addEventListener(){}},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#experiments'}});
  vm.runInContext(source+`\nshell=()=>{};renderRuntime=()=>{};toast=(msg)=>toasts.push(msg);refreshJobs=async()=>{};showDatabaseExperiment=async()=>{};
  globalThis.api={state,pages,validateImport,filteredData,adoptDatabase,persist,refreshConnection,submitDatabaseExperiment,recordForm,savePlan,categorySelectionSummary,bindCategoryPicker,bindPage,actions};`,context);
  // Use the context that contains both stubs and state.
  return {api:context.api,writes,toasts};
}
test('category filter combines with route filter/search and sorts without dropping categories',()=>{
  const {api:a}=app();a.state.tab='attacks';a.state.categoryFilter='instruction-override';
  assert.equal(a.filteredData().length,2);a.state.filter='direct';assert.equal(a.filteredData().length,1);
  a.state.query='없는 단어';assert.equal(a.filteredData().length,0);
  a.state.query='';a.state.filter='all';a.state.dataSort='name';const names=Array.from(a.filteredData(),a=>a.name);assert.deepEqual(names,[...names].sort((a,b)=>a.localeCompare(b,'ko')));
  assert.deepEqual(Array.from(a.state.attacks,x=>x.id),seeds.attackSeeds.map(x=>x.id));assert.match(a.pages.data(),/카테고리 추가/);assert.match(a.pages.experiments(),/name="categoryIds"/);assert.doesNotMatch(a.pages.experiments(),/name="attack-choice"|name="scenarioIds"/);
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
test('DB submission sends exact first-registered IDs across categories, fixed repeat 1, and blocks invalid totals',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Category run',kind:'attack',model:'test-model',emailId:'email-id'}};
  const elements={'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':{textContent:''}};
  const {api:a}=app({elements,fetcher:async(url,options)=>{calls.push({url,payload:JSON.parse(options.body)});return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:['instruction-override','special-token'],categoryCounts:{'instruction-override':2,'special-token':1},attacks:[row('new',{registeredAt:'2026-10-21T10:00:00Z'}),row('legacy'),row('old',{registeredAt:'2026-10-20T10:00:00Z'}),row('file',{channel:'attachment'}),row('disabled',{enabled:false}),row('c',{categoryId:'special-token'})]});
  await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/experiments');assert.deepEqual(calls[0].payload.scenarioIds,['db-legacy','db-old','db-c']);assert.equal(calls[0].payload.repeat,1);
  a.state.attacks=Array.from({length:101},(_,i)=>row(String(i)));a.state.categoryIds=['instruction-override'];a.state.categoryCounts={'instruction-override':101};await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.match(elements['#db-submit-error'].textContent,/최대 100개/);
  a.state.categoryCounts['instruction-override']=0;await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.match(elements['#db-submit-error'].textContent,/정수/);
});
test('normal experiment ignores invalid attack counts and sends configured repeat with default 1',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Normal run',kind:'normal',model:'test-model',emailId:'email-id',normalRepeat:'1'}};
  const elements={'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':{textContent:''}};
  const {api:a}=app({elements,fetcher:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:['instruction-override'],categoryCounts:{'instruction-override':0},attacks:[]});await a.submitDatabaseExperiment();assert.deepEqual(calls[0].scenarioIds,[]);assert.equal(calls[0].kind,'normal');assert.equal(calls[0].repeat,1);
  assert.equal(a.state.dbNormalRepeat,1);form.values.normalRepeat='10';a.state.dbNormalRepeat='10';await a.submitDatabaseExperiment();assert.equal(calls[1].repeat,10);assert.deepEqual(calls[1].scenarioIds,[]);assert.match(a.categorySelectionSummary(true),/<strong>10<small>회/);
});
test('invalid normal repeat values block submission explicitly while attack payload remains repeat 1',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Repeat validation',kind:'normal',model:'test-model',emailId:'email-id',normalRepeat:''}};
  const error={textContent:''};const {api:a}=app({elements:{'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':error},fetcher:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:['instruction-override'],categoryCounts:{'instruction-override':1},attacks:[row('a')]});
  for(const value of ['', ' ', '0', '1.5', '11']){form.values.normalRepeat=value;a.state.dbNormalRepeat=value;await a.submitDatabaseExperiment();assert.match(error.textContent,/1~10 사이의 정수/);assert.match(a.categorySelectionSummary(true),/<strong>—<small>회/);}
  assert.equal(calls.length,0);form.values.kind='attack';await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.equal(calls[0].repeat,1);assert.deepEqual(calls[0].scenarioIds,['db-a']);
});
test('kind toggles preserve normal repeat, category counts and existing name/model/email fields',()=>{
  const input=value=>({value,listeners:{},addEventListener(name,handler){this.listeners[name]=handler;}});
  const wrapper=()=>({hidden:false,classList:{toggle(name,value){this.owner.hidden=value;}}});
  const kind=input('attack'),repeat=input('1'),amount=input('2');amount.dataset={category:'instruction-override'};
  const name=input('Keep this experiment'),model=input('chosen-model'),email=input('selected-email');
  const category=wrapper(),normal=wrapper(),policy=wrapper();for(const el of [category,normal,policy])el.classList.owner=el;
  const summary={innerHTML:''},form={};const elements={'#db-experiment-form':form,'#db-kind':kind,'#db-normal-repeat':repeat,'#db-category-section':category,'#db-normal-repeat-field':normal,'#db-attack-policy':policy,'#db-selection-summary':summary,'#db-submit-error':{textContent:''},'#db-name':name,'#db-model':model,'#db-email':email};
  const {api:a}=app({elements,queryLists:{'input[name="category-count"]':[amount]}});Object.assign(a.state,{route:'experiments',dbConfigured:true,dbReady:true,categoryIds:['instruction-override'],categoryCounts:{'instruction-override':'2'}});a.bindPage();
  assert.equal(repeat.disabled,true);assert.equal(normal.hidden,true);assert.equal(amount.disabled,false);
  kind.value='normal';kind.listeners.change({target:kind});assert.equal(repeat.disabled,false);assert.equal(repeat.required,true);assert.equal(amount.disabled,true);assert.equal(amount.required,false);assert.equal(category.hidden,true);assert.equal(policy.hidden,true);
  repeat.value='4';repeat.listeners.input({target:repeat});assert.equal(a.state.dbNormalRepeat,'4');assert.match(summary.innerHTML,/<strong>4<small>회/);
  kind.value='attack';kind.listeners.change({target:kind});assert.equal(repeat.disabled,true);assert.equal(normal.hidden,true);assert.equal(amount.disabled,false);assert.equal(amount.required,true);assert.equal(amount.value,'2');assert.match(summary.innerHTML,/선택된 공격 2건/);
  kind.value='normal';kind.listeners.change({target:kind});assert.equal(repeat.value,'4');assert.match(summary.innerHTML,/<strong>4<small>회/);assert.equal(a.state.categoryCounts['instruction-override'],'2');
  assert.equal(elements['#db-experiment-form'],form);assert.equal(name.value,'Keep this experiment');assert.equal(model.value,'chosen-model');assert.equal(email.value,'selected-email');
});
test('saved plan freezes counts, category labels, first-registered scenario IDs and contents',()=>{
  const {api:a,writes}=app({elements:{'#plan-title':{value:'Frozen category plan'}}});a.state.categoryIds=['instruction-override'];a.state.categoryCounts['instruction-override']=2;a.savePlan();
  const p=a.state.plans[0];assert.equal(p.scenarios.length,2);assert.equal(p.repeat,1);assert.equal(p.selectionOrder,'registered-first-v1');assert.equal(p.categories[0].name,'지시 덮어쓰기');
  assert.deepEqual(Array.from(p.categorySelections[0].scenarioIds),['ATK-S01','ATK-S03']);assert.equal(p.categorySelections[0].count,2);
  a.state.attacks[0].name='Changed';a.state.categoryCounts['instruction-override']=1;assert.notEqual(p.scenarios[0].name,'Changed');assert.equal(p.categorySelections[0].count,2);
  const d=writes.at(-1).value;const next=app({saved:d});assert.equal(next.api.state.plans[0].categorySelections[0].count,2);assert.doesNotThrow(()=>a.validateImport(d));
});
test('attack count inputs remain separate from the hidden normal-only repeat field',()=>{
  const {api:a}=app();let html=a.pages.experiments();assert.doesNotMatch(html,/(?:id|name)="(?:db-)?repeat"/);assert.match(html,/name="category-count"/);assert.match(html,/<\/label>\s*<div class="category-amount">/);
  Object.assign(a.state,{dbConfigured:true,dbReady:true});html=a.pages.experiments();assert.doesNotMatch(html,/(?:id|name)="(?:db-)?repeat"/);assert.match(html,/시나리오당 1회 호출/);assert.match(html,/<div id="db-normal-repeat-field" class="field hidden">/);assert.match(html,/id="db-normal-repeat"[^>]+value="1" disabled/);
});
test('DOM count input updates selection, supports prototype-like category IDs, and is disabled for normal mode',()=>{
  const listeners={};const amount={value:'2',dataset:{category:'__proto__'},disabled:false,required:true,addEventListener:(name,fn)=>listeners[name]=fn};
  const summary={innerHTML:''};const {api:a}=app({elements:{'#plan-summary':summary},queryLists:{'input[name="category-count"]':[amount]}});
  a.state.categories.push({id:'__proto__',name:'Custom'});a.state.categoryIds=['__proto__'];a.state.attacks=[row('a',{categoryId:'__proto__'}),row('b',{categoryId:'__proto__'})];a.bindCategoryPicker();listeners.input();
  assert.equal(Object.hasOwn(a.state.categoryCounts,'__proto__'),true);assert.equal(a.state.categoryCounts['__proto__'],'2');assert.match(summary.innerHTML,/선택된 공격 2건/);
  Object.assign(a.state,{dbConfigured:true,dbKind:'normal'});a.bindCategoryPicker();assert.equal(amount.disabled,true);assert.equal(amount.required,false);
});
test('local edits preserve original registration metadata while new records and copies receive a timestamp',async()=>{
  let submit;const form={values:{name:'Edited attack',categoryId:'instruction-override',vector:'direct',channel:'input',technique:'test',goal:'test',payload:'test',criterion:'test',expectedOutput:''},addEventListener:(event,callback)=>{submit=callback;}};
  const modal={open:false,innerHTML:'',showModal(){this.open=true;},close(){this.open=false;}};
  const {api:a}=app({elements:{'#modal':modal,'#record-form':form,'#form-error':{textContent:''}}});Object.assign(a.state,{tab:'attacks',dbChecked:true,attacks:[row('dated',{provenance:'user',registeredAt:'2026-10-20T10:00:00Z'}),row('legacy',{provenance:'user'}),row('sample')]});
  for(const id of ['dated','legacy']){a.recordForm(id);await submit({preventDefault(){},target:form});}
  assert.equal(a.state.attacks[0].registeredAt,'2026-10-20T10:00:00Z');assert.equal(a.state.attacks[1].registeredAt,undefined);assert.deepEqual(Array.from(a.state.attacks,r=>r.id),['dated','legacy','sample']);
  a.recordForm('sample');await submit({preventDefault(){},target:form});assert.equal(a.state.attacks.length,4);assert.ok(Number.isFinite(Date.parse(a.state.attacks[3].registeredAt)));assert.equal(a.state.attacks[2].registeredAt,undefined);
  a.recordForm();await submit({preventDefault(){},target:form});assert.ok(Number.isFinite(Date.parse(a.state.attacks[4].registeredAt)));
});
test('old repeated plans stay readable and malformed category selections or timestamps are rejected',()=>{
  const {api:a,writes}=app({elements:{'#plan-title':{value:'Snapshot'}}});a.state.categoryIds=['instruction-override'];a.savePlan();const valid=writes.at(-1).value;
  for(const mutate of [p=>delete p.selectionOrder,p=>p.categorySelections[0].count='<img src=x onerror=alert(1)>',p=>p.categorySelections[0].scenarioIds=['missing'],p=>p.categorySelections.push(p.categorySelections[0]),p=>p.categorySelections[0].count=2,p=>p.categorySelections[0].id='special-token']){
    const d=structuredClone(valid);mutate(d.plans[0]);assert.throws(()=>a.validateImport(d));
  }
  const legacy=structuredClone(valid);delete legacy.plans[0].selectionOrder;delete legacy.plans[0].categorySelections;legacy.plans[0].repeat=3;assert.doesNotThrow(()=>a.validateImport(legacy));assert.equal(app({saved:legacy}).api.state.plans[0].repeat,3);
  const xss=structuredClone(legacy);xss.plans[0].categorySelections=[{name:'x',count:'<img src=x onerror=alert(1)>'}];assert.throws(()=>a.validateImport(xss));
  for(const timestamp of ['invalid','2026-10-21',null,'2026-10-21T10:00:00']){const d=backup();d.attacks[0].registeredAt=timestamp;assert.throws(()=>a.validateImport(d));}
});
