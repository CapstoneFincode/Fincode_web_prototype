import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
import {webcrypto} from 'node:crypto';
import * as categoryTools from '../public/categories.js';
import * as seeds from '../public/data.js';

const source=readFileSync(new URL('../public/app.js',import.meta.url),'utf8')
  .replace(/^import[^\n]+\n/gm,'')
  .replace(/^/,'const { historicalRuns, attackSeeds, normalSeeds, historicalTypes }=seeds; const { categorySeeds, categoryNameKey, categoryFor, normalizeCategoryData, categoryGroups, categoryScopes, categoryScope, categoryLayout, selectionPlan, attackVectors, vectorCategoryKey, vectorSelectionPlan }=categoryTools;\n')
  .replace('export function validateImport','function validateImport')
  .replace(/route\(\);\s*refreshConnection\(\);\s*$/,'');
const pad=i=>String(i).padStart(3,'0');
const emails=count=>Array.from({length:count},(_,i)=>({...seeds.normalSeeds[0],id:`email-${pad(i)}`,title:`메일 ${pad(i)}`,body:'정상 이메일 본문',requiredFacts:[],reference:'기준 요약',provenance:'user',reviewStatus:'reviewed'}));
const attacks=count=>Array.from({length:count},(_,i)=>({...seeds.attackSeeds[0],id:`attack-${pad(i)}`,name:`시나리오 ${pad(i)}`,payload:'공격 데이터',provenance:'user',categoryId:'instruction-override',vector:i%2?'indirect':'direct',channel:'email',enabled:true}));
const rowIds=html=>Array.from(html.matchAll(/data-action="record-detail" data-id="([^"]+)"/g),match=>match[1]);
const input=()=>({value:'',innerHTML:'',textContent:'',listeners:{},addEventListener(name,handler){this.listeners[name]=handler;},focus(){},scrollIntoView(){},querySelector(){return null;}});
function app({normal=emails(0),attackRows=attacks(0),tab='normal'}={}){
  const writes=[],calls=[];
  const elements=Object.fromEntries(['data-table','data-query','data-filter','data-sort','import-file','data-vector-status','data-pagination-status'].map(id=>['#'+id,input()]));
  const context=vm.createContext({seeds,categoryTools,structuredClone,crypto:webcrypto,console,setTimeout,clearTimeout,AbortController,
    fetch:(...args)=>{calls.push(args);throw Error('Pagination must not request the API');},
    localStorage:{getItem:()=>null,setItem:(...args)=>writes.push(args)},
    document:{querySelector:s=>elements[s]||null,querySelectorAll:()=>[],getElementById:id=>elements['#'+id]||null,addEventListener(){}},
    window:{addEventListener(){},scrollTo(){}},location:{hash:'#data'}});
  vm.runInContext(source+`\nshell=()=>{$('#data-table').innerHTML=dataTable();};
    globalThis.api={state,dataTable,filteredData,dataVectorFilters,refreshDataView,actions,bindPage};`,context);
  Object.assign(context.api.state,{normal,attacks:attackRows,tab,route:'data'});
  return {...context.api,elements,writes,calls};
}

test('normal email pages show at most 20 records without omissions or duplicates',()=>{
  const normal=emails(105),a=app({normal});
  const seen=[];
  for(let page=1;page<=6;page++){
    if(page>1)a.actions['data-page']({dataset:{page:String(page)}});
    const html=a.dataTable(),ids=rowIds(html);
    assert.equal(ids.length,page===6?5:20);
    assert.match(html,new RegExp(`전체 105건 중 ${(page-1)*20+1}–${Math.min(page*20,105)}건 · 페이지당 20건`));
    assert.match(html,new RegExp(`${page} / 6`));
    seen.push(...ids);
  }
  assert.deepEqual(seen,normal.map(row=>row.id));
  assert.equal(new Set(seen).size,normal.length);
  assert.deepEqual(a.writes,[]);assert.deepEqual(a.calls,[]);
});

test('attack scenario pages retain all records and full vector counts',()=>{
  const attackRows=attacks(89),a=app({attackRows,tab:'attacks'}),seen=[];
  a.dataTable();
  for(let page=1;page<=5;page++){
    a.actions['data-page']({dataset:{page:String(page)}});
    const ids=rowIds(a.dataTable());
    assert.equal(ids.length,page===5?9:20);seen.push(...ids);
  }
  assert.deepEqual(seen,attackRows.map(row=>row.id));
  assert.match(a.dataVectorFilters(),/data-vector-count="direct">45</);
  assert.match(a.dataVectorFilters(),/data-vector-count="indirect">44</);
  assert.equal(a.filteredData().length,89);
  assert.deepEqual(a.writes,[]);assert.deepEqual(a.calls,[]);
});

test('normal search, review status and sorting operate on the full dataset before pagination',()=>{
  const normal=emails(60).map((row,i)=>({...row,title:`${i>=10?'대상':'제외'} ${pad(60-i)}`,reviewStatus:i%2?'pending':'reviewed'}));
  const a=app({normal});
  Object.assign(a.state,{query:'대상',filter:'reviewed',dataSort:'name'});
  const expected=normal.filter(row=>row.title.startsWith('대상')&&row.reviewStatus==='reviewed').reverse().map(row=>row.id);
  assert.deepEqual(rowIds(a.dataTable()),expected.slice(0,20));
  a.actions['data-page']({dataset:{page:'2'}});
  assert.deepEqual(rowIds(a.elements['#data-table'].innerHTML),expected.slice(20));
  assert.match(a.elements['#data-table'].innerHTML,/전체 25건 중 21–25건/);
  assert.equal(a.state.query,'대상');assert.equal(a.state.filter,'reviewed');assert.equal(a.state.dataSort,'name');
  assert.deepEqual(normal.map(row=>row.id),emails(60).map(row=>row.id));
});

test('attack category, route, search and sorting combine across every page',()=>{
  const attackRows=attacks(80).map((row,i)=>({...row,name:`후보 ${pad(80-i)}`,categoryId:i<60?'instruction-override':'special-token'}));
  const a=app({attackRows,tab:'attacks'});
  Object.assign(a.state,{categoryFilter:'instruction-override',filter:'direct',query:'후보',dataSort:'name'});
  const expected=attackRows.filter(row=>row.categoryId==='instruction-override'&&row.vector==='direct').reverse().map(row=>row.id);
  assert.deepEqual(rowIds(a.dataTable()),expected.slice(0,20));
  a.actions['data-page']({dataset:{page:'2'}});
  assert.deepEqual(rowIds(a.elements['#data-table'].innerHTML),expected.slice(20));
  assert.match(a.elements['#data-table'].innerHTML,/전체 30건 중 21–30건/);
  assert.match(a.dataVectorFilters(),/data-vector-count="direct">30</);
  assert.match(a.dataVectorFilters(),/data-vector-count="indirect">30</);
  assert.equal(a.state.categoryFilter,'instruction-override');assert.equal(a.state.filter,'direct');assert.equal(a.state.query,'후보');
});

test('changing search, status, category, route, sorting or dataset returns to page one',()=>{
  const changes=[
    ['normal','query','메일'],['normal','filter','reviewed'],['normal','dataSort','name'],
    ['attacks','categoryFilter','instruction-override'],['attacks','filter','direct'],['normal','tab','attacks'],
  ];
  for(const [tab,key,value] of changes){
    const a=app({normal:emails(90),attackRows:attacks(90),tab});
    a.dataTable();a.actions['data-page']({dataset:{page:'3'}});assert.equal(a.state.dataPageIndex,3);
    a.state[key]=value;
    assert.equal(rowIds(a.dataTable()).length,20,`${key}: still has multiple pages`);
    assert.equal(a.state.dataPageIndex,1,`${key}: first page after the condition changes`);
  }
});

test('search and sort controls reset pagination while page controls preserve their values',()=>{
  const a=app({normal:emails(70)});a.dataTable();a.bindPage();
  a.actions['data-page']({dataset:{page:'3'}});
  a.elements['#data-query'].listeners.input({target:{value:'메일'}});
  assert.equal(a.state.dataPageIndex,1);assert.equal(a.state.query,'메일');
  a.actions['data-page']({dataset:{page:'2'}});
  a.elements['#data-sort'].listeners.change({target:{value:'name'}});
  assert.equal(a.state.dataPageIndex,1);assert.equal(a.state.dataSort,'name');
  a.actions['data-page']({dataset:{page:'2'}});
  assert.equal(a.state.query,'메일');assert.equal(a.state.dataSort,'name');
  a.elements['#data-filter'].listeners.change({target:{value:'reviewed'}});
  assert.equal(a.state.dataPageIndex,1);assert.equal(a.state.filter,'reviewed');
  assert.deepEqual(a.writes,[]);assert.deepEqual(a.calls,[]);
});

test('archiving the final rows clamps to the last remaining page and renders an accurate empty state',()=>{
  const a=app({normal:emails(41)});a.dataTable();a.actions['data-page']({dataset:{page:'3'}});
  a.state.normal.pop();
  assert.equal(rowIds(a.dataTable()).length,20);assert.equal(a.state.dataPageIndex,2);
  assert.match(a.dataTable(),/전체 40건 중 21–40건/);
  a.state.normal=[];
  const html=a.dataTable();assert.deepEqual(rowIds(html),[]);assert.equal(a.state.dataPageIndex,1);
  assert.match(html,/전체 0건 중 0–0건/);assert.match(html,/조건에 맞는 이메일이 없어요/);assert.match(html,/1 \/ 1/);
});

test('pagination endpoints disable unavailable navigation for exact, partial and empty pages',()=>{
  for(const count of [0,1,20,21,40]){
    const a=app({normal:emails(count)});
    let html=a.dataTable();
    const controls=html=>Array.from(html.matchAll(/<button\b[^>]*data-action="data-page"[^>]*>[^]*?<\/button>/g),match=>match[0]);
    assert.match(controls(html)[0],/\bdisabled\b/,`${count}: no page before first`);
    const last=Math.max(1,Math.ceil(count/20));
    if(last>1)a.actions['data-page']({dataset:{page:String(last)}});
    html=a.dataTable();assert.match(controls(html).at(-1),/\bdisabled\b/,`${count}: no page after last`);
    assert.equal(rowIds(html).length,count===0?0:count%20||20);
    assert.match(html,/aria-label="데이터 목록 페이지"/);
  }
});
