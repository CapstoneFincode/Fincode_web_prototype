import test from 'node:test';
import assert from 'node:assert/strict';
import {load,flatten,select,defaults,StaleHistory} from '../public/defense-history.js';
const run=(id,extra={})=>({id,expected_label:'attack',security_gateway_enabled:true,status:'succeeded',repeat_index:1,requested_model:'gemini-test',created_at:'2026-11-16T01:00:00Z',input_snapshot:{defenseMode:'separate',attackScenario:{key:'s-'+id,name:'시나리오 '+id,vector:'direct',payload:'공격 본문 '+id},messages:[{role:'system',content:'검색 제외 시스템 지침'},{role:'user',content:'원본 공격 본문 '+id}]},evaluations:[{metric:'attack_success',judge_type:'rule',decision_status:'determinate',boolean_value:false}],...extra});
const detail=(id,runs)=>({experiment:{id,purpose:'attack',execution_plan:{defense:'separate'}},runs});
const page=(rows,total=rows.length,totalPages=1,n=1)=>({experiments:rows,page:n,pageSize:100,total,totalPages});
const parent=(id,total=1)=>({id,purpose:'attack',total});

test('all parent pages load with at most two details in flight and preserve each run',async()=>{
 const calls=[];let active=0,max=0;
 const ds={a:detail('a',[run('1'),run('2')]),b:detail('b',[run('3')]),c:detail('c',[run('4')])};
 const api=async url=>{calls.push(url);if(url.includes('?'))return url.includes('page=1&')?page([parent('a',2),parent('b')],3,2):page([parent('c')],3,2,2);active++;max=Math.max(max,active);await new Promise(r=>setImmediate(r));active--;return ds[url.split('/').at(-1)];};
 const rows=flatten(await load(api));assert.equal(rows.length,4);assert.equal(max,2);assert.equal(calls.length,5);assert.ok(calls.every(x=>x.startsWith('/api/experiments')));
});
test('run IDs retain separate repeats and defense modes even with equal scenario names',()=>{
 const a=run('1');const b=run('2',{repeat_index:2,input_snapshot:{...a.input_snapshot,defenseMode:'filter'}});const rows=flatten([detail('p',[a,b])]);assert.equal(rows.length,2);assert.equal(rows[0].name,rows[1].name);assert.notEqual(rows[0].id,rows[1].id);assert.deepEqual(rows.map(r=>r.mode),['separate','filter']);
});
test('snapshot name and vector remain authoritative and OFF rows do not enter defense records',()=>{
 const rows=flatten([detail('p',[run('1',{attack_scenario_name:'현재 바뀐 이름',attack_vector:'indirect'}),run('2',{security_gateway_enabled:false})])]);assert.equal(rows.length,1);assert.equal(rows[0].name,'시나리오 1');assert.equal(rows[0].vector,'direct');
});
test('query, vector, mode and outcome combine before pagination without mutating input',()=>{
 const a=run('1');a.input_snapshot.attackScenario.vector='indirect';a.input_snapshot.defenseMode='filter';a.status='blocked';a.input_snapshot.defensePlan={originalMessages:[{role:'user',content:'첨부 원문 ＡＢＣ'}]};const rows=flatten([detail('p',[a,run('2')])]);const before=structuredClone(rows);const filters={...defaults(),query:'abc',vector:'indirect',mode:'filter',outcome:'failure'};const result=select(rows,filters);assert.equal(result.total,1);assert.equal(result.rows[0].id,'1');assert.deepEqual(rows,before);assert.equal(select(rows,{...defaults(),query:'검색 제외 시스템 지침'}).total,0);
});
test('oldest/newest sorting and filtered page counts are deterministic',()=>{
 const rows=flatten([detail('p',Array.from({length:11},(_,i)=>run(String(i),{created_at:`2026-11-16T01:${String(i).padStart(2,'0')}:00Z`})))]);const newest=select(rows,{...defaults(),sort:'newest'},2);assert.equal(newest.total,11);assert.equal(newest.totalPages,3);assert.deepEqual(newest.rows.map(r=>r.id),['5','4','3','2','1']);assert.equal(select(rows,{...defaults(),sort:'oldest'}).rows[0].id,'0');assert.equal(select(rows,defaults(),99).page,3);assert.equal(select(rows,{...defaults(),query:'no-match'},99).page,1);
});
test('registration order uses scenario registration metadata rather than last execution',()=>{
 const a=run('1'),b=run('2');const rows=flatten([detail('p',[a,b])],[{id:'s-1',registeredAt:'2026-11-10'},{id:'s-2',registeredAt:'2026-11-01'}]);assert.equal(select(rows).rows[0].id,'2');
});
test('unknown ON mode remains unknown and latest human verdict wins',()=>{
 const a=run('1',{input_snapshot:{},evaluations:[{metric:'attack_success',judge_type:'rule',decision_status:'determinate',boolean_value:false},{metric:'attack_success',judge_type:'human',decision_status:'determinate',boolean_value:true}]});const rows=flatten([{experiment:{id:'p'},runs:[a]}]);assert.equal(rows[0].mode,'unknown');assert.equal(rows[0].outcome,'success');
});
test('failed detail and partial list fail explicitly instead of reporting an incomplete success',async()=>{
 await assert.rejects(load(async url=>url.includes('?')?page([parent('a')]):{experiment:{id:'a',purpose:'attack'},runs:[]}),/일부/);
 await assert.rejects(load(async()=>page([],2)),/일부/);
 await assert.rejects(load(async()=>page([parent('a'),parent('a')],2)),/중복/);
});
test('changed totals across pages require a fresh complete lookup',async()=>{
 await assert.rejects(load(async url=>url.includes('page=1&')?page([parent('a')],2,2):page([parent('b')],3,2,2)),/바뀌었어요/);
});
test('stale navigation aborts before further requests',async()=>{
 let current=true,calls=0;await assert.rejects(load(async()=>{calls++;current=false;return page([]);},{isCurrent:()=>current}),StaleHistory);assert.equal(calls,1);
});
test('duplicate runs are rejected and an empty history remains valid',async()=>{
 assert.throws(()=>flatten([detail('p',[run('1'),run('1')])]),/중복/);assert.deepEqual(await load(async()=>page([])),[]);
});
