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
  .replace(/^/,'const { historicalRuns, attackSeeds, normalSeeds, historicalTypes }=seeds; const { categorySeeds, categoryNameKey, categoryFor, normalizeCategoryData, categoryGroups, selectionPlan, attackVectors, vectorCategoryKey, vectorSelectionPlan }=categoryTools;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');
function app({saved=null,preview=false,cryptoApi=webcrypto,elements={},queryLists={},fetcher=()=>{throw Error('Unexpected network');}}={}){
  const writes=[],toasts=[];
  const context=vm.createContext({toasts,seeds,categoryTools,structuredClone,crypto:cryptoApi,console,setTimeout,clearTimeout,AbortController,
    FormData:class {constructor(form){this.values=form.values||{}} get(key){return this.values[key]??null;} [Symbol.iterator](){return Object.entries(this.values)[Symbol.iterator]();}},
    fetch:fetcher,localStorage:{getItem:()=>saved?JSON.stringify(saved):null,setItem:(key,value)=>writes.push({key,value:JSON.parse(value)})},
    document:{querySelector:s=>s.startsWith('meta[')?preview?{}:null:elements[s]||null,querySelectorAll:s=>queryLists[s]||[],getElementById:id=>elements['#'+id]||null,addEventListener(){}},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#experiments'}});
  vm.runInContext(source+`\nshell=()=>{};renderRuntime=()=>{};toast=(msg)=>toasts.push(msg);refreshJobs=async()=>{};showDatabaseExperiment=async()=>{};
  state.vectorIds=['direct'];globalThis.api={state,bindVectorPicker,currentAttackPlan,pages,validateImport,filteredData,adoptDatabase,persist,refreshConnection,submitDatabaseExperiment,recordForm,savePlan,categorySelectionSummary,bindCategoryPicker,bindPage,actions};`,context);
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
test('static preview toggles normal repeat and attack settings without API calls or fake DB state',async()=>{
  const input=value=>({value,listeners:{},addEventListener(name,handler){this.listeners[name]=handler;}});
  const wrapper=()=>{const el={hidden:false};el.classList={toggle(name,value){el.hidden=value;}};return el;};
  const kind=input('attack'),repeat=input('1'),amount=input('2');amount.dataset={category:'direct:instruction-override'};
  const summary={innerHTML:''},category=wrapper(),attackConfig=wrapper(),normalConfig=wrapper(),planActions=wrapper(),normalActions=wrapper();
  const elements={'#db-kind':kind,'#db-normal-repeat':repeat,'#db-normal-repeat-field':wrapper(),'#db-category-section':category,'#preview-attack-config':attackConfig,'#preview-normal-config':normalConfig,'#preview-plan-actions':planActions,'#preview-normal-actions':normalActions,'#plan-summary':summary,'#plan-title':input('Preview attack plan'),'#experiment-model':input('model'),'#experiment-defense':input('off'),'#preview-normal-email':input('email')};
  let calls=0;const {api:a,writes,toasts}=app({preview:true,elements,queryLists:{'input[name="category-count"]':[amount]},fetcher:()=>{calls++;throw Error('Unexpected network');}});
  await a.refreshConnection();Object.assign(a.state,{route:'experiments',categoryIds:['direct:instruction-override'],categoryCounts:{'direct:instruction-override':'2'}});a.state.attacks[2].vector='direct';a.state.attacks[2].channel='input';a.bindPage();
  assert.match(a.pages.experiments(),/실험 종류 · UI 검토용/);assert.match(a.pages.experiments(),/name="normalRepeat"/);assert.doesNotMatch(a.pages.experiments(),/data-action="db-run"/);assert.equal(normalConfig.hidden,true);assert.equal(amount.disabled,false);
  kind.value='normal';kind.listeners.change({target:kind});assert.equal(normalConfig.hidden,false);assert.equal(attackConfig.hidden,true);assert.equal(category.hidden,true);assert.equal(planActions.hidden,true);assert.equal(normalActions.hidden,false);assert.equal(repeat.disabled,false);assert.equal(amount.disabled,true);
  repeat.value='7';repeat.listeners.input({target:repeat});assert.match(summary.innerHTML,/<strong>7<small>회/);assert.match(a.pages.experiments(),/disabled>실제 실행은 API·DB 연결 후 지원/);
  repeat.value='0';repeat.listeners.input({target:repeat});assert.match(summary.innerHTML,/1~10 사이의 정수/);assert.match(summary.innerHTML,/<strong>—<small>회/);
  repeat.value='7';repeat.listeners.input({target:repeat});await a.submitDatabaseExperiment();a.savePlan();assert.equal(a.state.plans.length,0);assert.match(toasts.at(-1),/설정만 확인/);
  kind.value='attack';kind.listeners.change({target:kind});assert.equal(normalConfig.hidden,true);assert.equal(planActions.hidden,false);assert.equal(amount.disabled,false);assert.equal(amount.value,'2');assert.equal(a.state.dbNormalRepeat,'7');a.savePlan();assert.equal(a.state.plans[0].repeat,1);assert.equal(a.state.plans[0].scenarios.length,2);assert.equal(writes.at(-1).key,'fincode-ui-categories-preview-v1');
  assert.equal(calls,0);assert.equal(a.state.dbReady,false);assert.equal(a.state.dbConfigured,false);assert.equal(a.state.connection,'idle');
  assert.doesNotMatch(app().api.pages.experiments(),/실험 종류 · UI 검토용|name="normalRepeat"/);
});
test('DB submission sends exact first-registered IDs across categories, fixed repeat 1, and blocks invalid totals',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Category run',kind:'attack',model:'test-model',emailId:'email-id'}};
  const elements={'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':{textContent:''}};
  const {api:a}=app({elements,fetcher:async(url,options)=>{calls.push({url,payload:JSON.parse(options.body)});return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:['direct:instruction-override','direct:special-token'],categoryCounts:{'direct:instruction-override':2,'direct:special-token':1},attacks:[row('new',{registeredAt:'2026-10-21T10:00:00Z'}),row('legacy'),row('old',{registeredAt:'2026-10-20T10:00:00Z'}),row('file',{channel:'attachment'}),row('disabled',{enabled:false}),row('c',{categoryId:'special-token'})]});
  await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/experiments');assert.deepEqual(calls[0].payload.scenarioIds,['db-legacy','db-old','db-c']);assert.equal(calls[0].payload.repeat,1);
  a.state.attacks=Array.from({length:101},(_,i)=>row(String(i)));a.state.categoryIds=['direct:instruction-override'];a.state.categoryCounts={'direct:instruction-override':101};await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.match(elements['#db-submit-error'].textContent,/최대 100개/);
  a.state.categoryCounts['direct:instruction-override']=0;await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.match(elements['#db-submit-error'].textContent,/정수/);
});
test('normal experiment ignores invalid attack counts and sends configured repeat with default 1',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Normal run',kind:'normal',model:'test-model',emailId:'email-id',normalRepeat:'1'}};
  const elements={'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':{textContent:''}};
  const {api:a}=app({elements,fetcher:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:['direct:instruction-override'],categoryCounts:{'direct:instruction-override':0},attacks:[]});await a.submitDatabaseExperiment();assert.deepEqual(calls[0].scenarioIds,[]);assert.equal(calls[0].kind,'normal');assert.equal(calls[0].repeat,1);
  assert.equal(a.state.dbNormalRepeat,1);form.values.normalRepeat='10';a.state.dbNormalRepeat='10';await a.submitDatabaseExperiment();assert.equal(calls[1].repeat,10);assert.deepEqual(calls[1].scenarioIds,[]);assert.match(a.categorySelectionSummary(true),/<strong>10<small>회/);
});
test('invalid normal repeat values block submission explicitly while attack payload remains repeat 1',async()=>{
  const calls=[];const form={reportValidity:()=>true,dataset:{},values:{name:'Repeat validation',kind:'normal',model:'test-model',emailId:'email-id',normalRepeat:''}};
  const error={textContent:''};const {api:a}=app({elements:{'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':error},fetcher:async(url,options)=>{calls.push(JSON.parse(options.body));return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',categoryIds:['direct:instruction-override'],categoryCounts:{'direct:instruction-override':1},attacks:[row('a')]});
  for(const value of ['', ' ', '0', '1.5', '11']){form.values.normalRepeat=value;a.state.dbNormalRepeat=value;await a.submitDatabaseExperiment();assert.match(error.textContent,/1~10 사이의 정수/);assert.match(a.categorySelectionSummary(true),/<strong>—<small>회/);}
  assert.equal(calls.length,0);form.values.kind='attack';await a.submitDatabaseExperiment();assert.equal(calls.length,1);assert.equal(calls[0].repeat,1);assert.deepEqual(calls[0].scenarioIds,['db-a']);
});
test('kind toggles preserve normal repeat, category counts and existing name/model/email fields',()=>{
  const input=value=>({value,listeners:{},addEventListener(name,handler){this.listeners[name]=handler;}});
  const wrapper=()=>({hidden:false,classList:{toggle(name,value){this.owner.hidden=value;}}});
  const kind=input('attack'),repeat=input('1'),amount=input('2');amount.dataset={category:'direct:instruction-override'};
  const name=input('Keep this experiment'),model=input('chosen-model'),email=input('selected-email');
  const category=wrapper(),normal=wrapper(),policy=wrapper();for(const el of [category,normal,policy])el.classList.owner=el;
  const summary={innerHTML:''},form={};const elements={'#db-experiment-form':form,'#db-kind':kind,'#db-normal-repeat':repeat,'#db-category-section':category,'#db-normal-repeat-field':normal,'#db-attack-policy':policy,'#db-selection-summary':summary,'#db-submit-error':{textContent:''},'#db-name':name,'#db-model':model,'#db-email':email};
  const {api:a}=app({elements,queryLists:{'input[name="category-count"]':[amount]}});Object.assign(a.state,{route:'experiments',dbConfigured:true,dbReady:true,categoryIds:['direct:instruction-override'],categoryCounts:{'direct:instruction-override':'2'}});a.state.attacks[2].vector='direct';a.state.attacks[2].channel='input';a.bindPage();
  assert.equal(repeat.disabled,true);assert.equal(normal.hidden,true);assert.equal(amount.disabled,false);
  kind.value='normal';kind.listeners.change({target:kind});assert.equal(repeat.disabled,false);assert.equal(repeat.required,true);assert.equal(amount.disabled,true);assert.equal(amount.required,false);assert.equal(category.hidden,true);assert.equal(policy.hidden,true);
  repeat.value='4';repeat.listeners.input({target:repeat});assert.equal(a.state.dbNormalRepeat,'4');assert.match(summary.innerHTML,/<strong>4<small>회/);
  kind.value='attack';kind.listeners.change({target:kind});assert.equal(repeat.disabled,true);assert.equal(normal.hidden,true);assert.equal(amount.disabled,false);assert.equal(amount.required,true);assert.equal(amount.value,'2');assert.match(summary.innerHTML,/선택된 공격 2건/);
  kind.value='normal';kind.listeners.change({target:kind});assert.equal(repeat.value,'4');assert.match(summary.innerHTML,/<strong>4<small>회/);assert.equal(a.state.categoryCounts['direct:instruction-override'],'2');
  assert.equal(elements['#db-experiment-form'],form);assert.equal(name.value,'Keep this experiment');assert.equal(model.value,'chosen-model');assert.equal(email.value,'selected-email');
});
test('saved plan freezes counts, category labels, first-registered scenario IDs and contents',()=>{
  const {api:a,writes}=app({elements:{'#plan-title':{value:'Frozen category plan'}}});a.state.categoryIds=['direct:instruction-override'];a.state.categoryCounts['direct:instruction-override']=2;a.state.attacks[2].vector='direct';a.state.attacks[2].channel='input';a.savePlan();
  const p=a.state.plans[0];assert.equal(p.scenarios.length,2);assert.equal(p.repeat,1);assert.equal(p.selectionOrder,'vector-registered-first-v1');assert.equal(p.categories[0].name,'지시 덮어쓰기');
  assert.deepEqual(Array.from(p.categorySelections[0].scenarioIds),['ATK-S01','ATK-S03']);assert.equal(p.categorySelections[0].count,2);
  a.state.attacks[0].name='Changed';a.state.categoryCounts['direct:instruction-override']=1;assert.notEqual(p.scenarios[0].name,'Changed');assert.equal(p.categorySelections[0].count,2);
  const d=writes.at(-1).value;const next=app({saved:d});assert.equal(next.api.state.plans[0].categorySelections[0].count,2);assert.doesNotThrow(()=>a.validateImport(d));
});
test('attack count inputs remain separate from the hidden normal-only repeat field',()=>{
  const {api:a}=app();let html=a.pages.experiments();assert.doesNotMatch(html,/(?:id|name)="(?:db-)?repeat"/);assert.match(html,/name="category-count"/);assert.match(html,/<\/label>\s*<div class="category-amount">/);
  Object.assign(a.state,{dbConfigured:true,dbReady:true});html=a.pages.experiments();assert.doesNotMatch(html,/(?:id|name)="(?:db-)?repeat"/);assert.match(html,/시나리오당 1회 호출/);assert.match(html,/<div id="db-normal-repeat-field" class="field hidden">/);assert.match(html,/id="db-normal-repeat"[^>]+value="1" disabled/);
});
test('DOM count input updates selection, supports prototype-like category IDs, and is disabled for normal mode',()=>{
  const listeners={};const amount={value:'2',dataset:{category:'direct:__proto__'},disabled:false,required:true,addEventListener:(name,fn)=>listeners[name]=fn};
  const summary={innerHTML:''};const {api:a}=app({elements:{'#plan-summary':summary},queryLists:{'input[name="category-count"]':[amount]}});
  a.state.categories.push({id:'__proto__',name:'Custom'});a.state.categoryIds=['direct:__proto__'];a.state.attacks=[row('a',{categoryId:'__proto__'}),row('b',{categoryId:'__proto__'})];a.bindCategoryPicker();listeners.input();
  assert.equal(Object.hasOwn(a.state.categoryCounts,'direct:__proto__'),true);assert.equal(a.state.categoryCounts['direct:__proto__'],'2');assert.match(summary.innerHTML,/선택된 공격 2건/);
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
  const {api:a,writes}=app({elements:{'#plan-title':{value:'Snapshot'}}});a.state.categoryIds=['direct:instruction-override'];a.savePlan();const valid=writes.at(-1).value;
  for(const mutate of [p=>delete p.selectionOrder,p=>p.categorySelections[0].count='<img src=x onerror=alert(1)>',p=>p.categorySelections[0].scenarioIds=['missing'],p=>p.categorySelections.push(p.categorySelections[0]),p=>p.categorySelections[0].count=2,p=>p.categorySelections[0].id='special-token']){
    const d=structuredClone(valid);mutate(d.plans[0]);assert.throws(()=>a.validateImport(d));
  }
  const legacy=structuredClone(valid);delete legacy.plans[0].selectionOrder;delete legacy.plans[0].categorySelections;legacy.plans[0].repeat=3;assert.doesNotThrow(()=>a.validateImport(legacy));assert.equal(app({saved:legacy}).api.state.plans[0].repeat,3);
  const xss=structuredClone(legacy);xss.plans[0].categorySelections=[{name:'x',count:'<img src=x onerror=alert(1)>'}];assert.throws(()=>a.validateImport(xss));
  for(const timestamp of ['invalid','2026-10-21',null,'2026-10-21T10:00:00']){const d=backup();d.attacks[0].registeredAt=timestamp;assert.throws(()=>a.validateImport(d));}
});

test('vector selection uses independent counts and registration order within the same category',()=>{
  const rows=[row('d-new',{registeredAt:'2026-11-04T02:00:00Z'}),row('i-new',{vector:'indirect',channel:'email',registeredAt:'2026-11-04T03:00:00Z'}),row('d-old',{registeredAt:'2026-11-03T02:00:00Z'}),row('i-old',{vector:'indirect',channel:'email',registeredAt:'2026-11-03T03:00:00Z'}),row('disabled',{enabled:false}),row('file',{vector:'indirect',channel:'attachment'}),row('unknown',{vector:'unknown'})];
  const keys=['direct:instruction-override','indirect:instruction-override'];
  const p=categoryTools.vectorSelectionPlan([...rows,rows[0]],['direct','indirect','direct'],keys,{'direct:instruction-override':2,'indirect:instruction-override':1});
  assert.equal(p.error,'');assert.deepEqual(p.runnable.map(a=>a.id),['d-old','d-new','i-old']);assert.equal(p.calls,3);assert.equal(p.disabled,1);assert.equal(p.unsupported,1);
  assert.deepEqual(p.byCategory.map(c=>[c.vector,c.id,c.count,c.available]),[['direct','instruction-override',2,2],['indirect','instruction-override',1,2]]);
  assert.deepEqual(rows.map(a=>a.id),['d-new','i-new','d-old','i-old','disabled','file','unknown']);
});
test('vector plan ignores inactive errors, rejects missing selections and applies one combined limit',()=>{
  const rows=[row('d'),row('i',{vector:'indirect',channel:'email'})],keys=['direct:instruction-override','indirect:instruction-override'],counts={'direct:instruction-override':1,'indirect:instruction-override':0};
  assert.equal(categoryTools.vectorSelectionPlan(rows,['direct'],keys,counts).error,'');assert.match(categoryTools.vectorSelectionPlan(rows,['direct','indirect'],keys,counts).error,/정수/);
  assert.match(categoryTools.vectorSelectionPlan(rows,[],keys,counts).error,/직접 공격 또는 간접 공격/);
  assert.ok(categoryTools.vectorSelectionPlan(rows,['indirect'],['direct:instruction-override'],counts).error);
  const many=[...Array.from({length:60},(_,i)=>row('d'+i)),...Array.from({length:60},(_,i)=>row('i'+i,{vector:'indirect',channel:'email'}))];
  assert.match(categoryTools.vectorSelectionPlan(many,['direct','indirect'],keys,{'direct:instruction-override':60,'indirect:instruction-override':41}).error,/총 101건/);
  assert.equal(categoryTools.vectorSelectionPlan(many,['direct','indirect'],keys,{'direct:instruction-override':60,'indirect:instruction-override':40}).calls,100);
});
test('DB attack request matches the active vector/category selections and never sends inactive scenarios',async()=>{
  const calls=[],form={reportValidity:()=>true,dataset:{},values:{name:'Both paths',kind:'attack',model:'test-model',emailId:'email'}};
  const error={textContent:''};const {api:a}=app({elements:{'#db-experiment-form':form,'[data-action="db-run"]':{disabled:false},'#db-submit-error':error},fetcher:async(url,opt)=>{calls.push(JSON.parse(opt.body));return {ok:true,status:202,json:async()=>({id:'experiment'})};}});
  Object.assign(a.state,{dbReady:true,connection:'ready',vectorIds:['direct','indirect'],categoryIds:['direct:instruction-override','indirect:instruction-override'],categoryCounts:{'direct:instruction-override':1,'indirect:instruction-override':2},attacks:[row('d'),row('i-new',{vector:'indirect',channel:'email',registeredAt:'2026-11-04T02:00:00Z'}),row('i-old',{vector:'indirect',channel:'email',registeredAt:'2026-11-03T02:00:00Z'})]});
  await a.submitDatabaseExperiment();assert.deepEqual(calls[0].scenarioIds,['db-d','db-i-old','db-i-new']);assert.equal(calls[0].repeat,1);assert.equal(calls[0].defense,'off');
  a.state.vectorIds=['direct'];a.state.categoryCounts['indirect:instruction-override']='';await a.submitDatabaseExperiment();assert.deepEqual(calls[1].scenarioIds,['db-d']);
  a.state.vectorIds=['direct','indirect'];await a.submitDatabaseExperiment();assert.equal(calls.length,2);assert.match(error.textContent,/정수/);
});
test('path checkbox switches preserve independent category counts and update visible groups and totals',()=>{
  const make=v=>({value:v,checked:true,listeners:{},addEventListener(k,cb){this.listeners[k]=cb;},closest(){return {classList:{toggle(){}}};}});
  const direct=make('direct'),indirect=make('indirect'),grid={innerHTML:''},summary={innerHTML:''};
  const {api:a}=app({elements:{'#category-grid':grid,'#plan-summary':summary},queryLists:{'input[name="attack-vector"]':[direct,indirect]}});
  Object.assign(a.state,{vectorIds:['direct','indirect'],categoryIds:['direct:instruction-override','indirect:instruction-override'],categoryCounts:{'direct:instruction-override':1,'indirect:instruction-override':1}});a.bindVectorPicker();
  indirect.checked=false;indirect.listeners.change();assert.deepEqual(Array.from(a.state.vectorIds),['direct']);assert.match(grid.innerHTML,/직접 공격 카테고리/);assert.doesNotMatch(grid.innerHTML,/간접 공격 카테고리/);assert.match(summary.innerHTML,/선택 안 함/);assert.equal(a.currentAttackPlan().calls,1);
  indirect.checked=true;indirect.listeners.change();assert.equal(a.currentAttackPlan().calls,2);assert.equal(a.state.categoryCounts['indirect:instruction-override'],1);assert.match(grid.innerHTML,/간접 공격 카테고리/);assert.match(summary.innerHTML,/직접 공격 1회, 간접 공격 1회/);
  direct.checked=false;direct.listeners.change();indirect.checked=false;indirect.listeners.change();assert.match(summary.innerHTML,/직접 공격 또는 간접 공격을 선택/);
});
test('new snapshots roundtrip the same category in both paths; legacy category plans still import',()=>{
  const {api:a,writes}=app({elements:{'#plan-title':{value:'Both paths snapshot'}}});Object.assign(a.state,{vectorIds:['direct','indirect'],categoryIds:['direct:instruction-override','indirect:instruction-override'],categoryCounts:{'direct:instruction-override':1,'indirect:instruction-override':1}});a.savePlan();
  const data=writes.at(-1).value,plan=data.plans[0];assert.equal(plan.categorySelections.length,2);assert.deepEqual(plan.categorySelections.map(c=>c.vector),['direct','indirect']);assert.deepEqual(plan.categorySelections.map(c=>c.id),['instruction-override','instruction-override']);assert.equal(app({saved:data}).api.state.plans.length,1);assert.doesNotThrow(()=>a.validateImport(data));
  for(const mutate of [p=>p.categorySelections[1].vector='direct',p=>p.vectorIds=['direct'],p=>p.vectorIds=['direct','unknown'],p=>p.categorySelections[1].vector='unknown',p=>p.categorySelections[1].scenarioIds=['ATK-S01']]){const d=structuredClone(data);mutate(d.plans[0]);assert.throws(()=>a.validateImport(d));}
  const old=structuredClone(data);old.plans[0].selectionOrder='registered-first-v1';delete old.plans[0].vectorIds;old.plans[0].categorySelections=[{id:'instruction-override',name:'지시 덮어쓰기',count:2,scenarioIds:['ATK-S01','ATK-S03']}];assert.doesNotThrow(()=>a.validateImport(old));assert.equal(app({saved:old}).api.state.plans[0].scenarios.length,2);
});


test('HTTP preview saves plans using getRandomValues when randomUUID is unavailable',()=>{
  let calls=0;const cryptoApi={getRandomValues(bytes){assert.equal(bytes.length,16);bytes.fill(calls++);return bytes;}};
  const {api:a,writes}=app({preview:true,cryptoApi,elements:{'#plan-title':{value:'Local preview'}}});
  a.savePlan();a.savePlan();assert.equal(calls,2);assert.equal(writes.length,2);
  const ids=Array.from(a.state.plans,p=>p.id);
  assert.equal(ids[0],'00000000-0000-4000-8000-000000000000');
  assert.equal(ids[1],'01010101-0101-4101-8101-010101010101');
  for(const id of ids)assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.doesNotThrow(()=>a.validateImport(writes.at(-1).value));
});
