import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as seeds from '../dist/data.js';

const source=readFileSync(new URL('../dist/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/, 'const { historicalRuns, attackSeeds, normalSeeds, historicalTypes } = seeds;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*$/, '');
function app(saved=null){
  const listeners={};
  const context=vm.createContext({seeds:structuredClone({...seeds}),structuredClone,console,crypto:webcrypto,setTimeout,clearTimeout,
    localStorage:{getItem:()=>saved?JSON.stringify(saved):null,setItem(){}},
    document:{addEventListener:(name,handler)=>listeners[name]=handler,querySelector:()=>null},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#overview'},listeners});
  vm.runInContext(source+'\nglobalThis.api={state,pages,gtCount,validateImport,filteredResults,historyTable,chart,esc};',context);
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
  assert.match(a.pages.email(),/API 연동 후 측정/);
  assert.match(a.pages.experiments(),/미실행/);
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
