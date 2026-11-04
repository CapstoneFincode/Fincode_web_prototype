import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {attackOutcome,attackCounts,buildAttackReport,filterAttackRuns,requestMessages,scenarioInfo,renderAttackReport,renderAttackRun,mountAttackReport} from '../public/attack-results.js';

const verdict=(value,judge_type='rule')=>({metric:'attack_success',boolean_value:value,decision_status:value===null?'indeterminate':'determinate',judge_type,evidence:'검증 근거'});
const run=(id,categoryId='leak',value=true,extra={})=>({id,expected_label:'attack',status:'succeeded',evaluations:[verdict(value)],input_snapshot:{attackCategoryId:categoryId,attackScenario:{key:'scenario-'+categoryId,name:'시나리오 '+categoryId,categoryId,categoryName:'카테고리 '+categoryId,vector:'direct'},messages:[{role:'user',content:'예정 프롬프트'}]},...extra});

test('report separates repeated runs from unique scenarios and excludes non-attack cases',()=>{
  const detail={runs:[run('1'),run('2'),run('3','override',false),run('4','override',null),run('5','normal',true,{expected_label:'normal'})]};
  const report=buildAttackReport(detail);
  assert.equal(report.groups.length,2);assert.equal(report.counts.total,4);
  assert.equal(report.groups[0].scenarioCount,1);assert.equal(report.groups[0].counts.total,2);
  assert.equal(report.counts.known,3);assert.equal(report.counts.success,2);assert.equal(report.counts.unknown,1);
  assert.equal(report.counts.asr,200/3);
});
test('human precedence, uncertain judgments and failed executions do not inflate ASR',()=>{
  const overridden=run('1','leak',true,{evaluations:[verdict(true),verdict(false,'human'),verdict(true)]});
  const unknown=run('2','leak',true,{evaluations:[verdict(true),verdict(false,'human'),verdict(null,'human')]});
  const runs=[overridden,unknown,run('3','leak',true,{status:'timed_out'}),run('4','leak',true,{status:'queued'}),run('5','leak',true,{status:'cancelled'}),run('6','leak',true,{evaluations:[]})];
  assert.equal(attackOutcome(overridden),'failure');assert.equal(attackOutcome(unknown),'unknown');
  const c=attackCounts(runs);assert.equal(c.known,1);assert.equal(c.asr,0);assert.equal(c.error,1);assert.equal(c.pending,1);assert.equal(c.cancelled,1);assert.equal(c.unjudged,1);assert.equal(c.automaticKnown,2);assert.equal(c.completed,3);
  assert.equal(attackCounts([]).asr,null);assert.doesNotMatch(renderAttackReport({runs:[]}),/NaN|Infinity/);
});
test('saved metadata and actual call messages take precedence over changed live data',()=>{
  const r=run('1','leak',true,{attack_scenario_name:'수정한 제목',attack_category_name:'새 이름',calls:[{purpose:'summarize',request_snapshot:{messages:[{role:'user',content:'실제 전송'}]}}]});
  assert.equal(scenarioInfo(r,[{id:'leak',name:'현재 카테고리'}]).categoryName,'카테고리 leak');
  assert.equal(scenarioInfo(r).name,'시나리오 leak');assert.equal(requestMessages(r).messages[0].content,'실제 전송');assert.equal(requestMessages(r).source,'sent');
  const legacy={...r,input_snapshot:{messages:[]},attack_category_id:'old',attack_scenario_key:'old-key'};
  assert.equal(scenarioInfo(legacy).id,'old-key');assert.equal(scenarioInfo(legacy).categoryName,'새 이름');
  const planned=run('2');assert.equal(requestMessages(planned).source,'planned');assert.match(renderAttackRun(planned,0),/실행 예정 입력 스냅샷/);
});
test('category, outcome and prompt/response search compose without changing overall totals',()=>{
  const a=run('1','leak',true,{final_output:'노출된 원문'}),b=run('2','leak',false,{final_output:'정상 요약'}),c=run('3','override',true);
  const rows=[a,b,c];assert.deepEqual(filterAttackRuns(rows,{category:'leak',status:'failure',query:'정상'}),[b]);
  assert.deepEqual(filterAttackRuns(rows,{query:'원문'}),[a]);assert.equal(filterAttackRuns(rows,{query:'예정 프롬프트'}).length,3);
  assert.equal(filterAttackRuns(rows,{category:'missing'}).length,0);
  const html=renderAttackReport({runs:rows},{category:'leak',status:'failure'});
  assert.match(html,/data-run-card="2"/);assert.doesNotMatch(html,/data-run-card="1"/);assert.match(html,/전체 테스트<\/span><strong>3/);
});
test('all attacker-controlled strings are text, never executable HTML',()=>{
  const payload='<img src=x onerror=alert(1)>';
  const r=run('" onclick="boom','leak',true,{final_output:payload,requested_model:payload,evaluations:[{...verdict(true),evidence:payload,details:{match:{text:payload},occurrences:[{kind:"quoted_or_reported",start:0,end:12,context:payload}]}}],calls:[{purpose:'summarize',request_snapshot:{messages:[{role:'system',content:payload},{role:'user',content:payload}]}}]});
  r.input_snapshot.attackScenario.name=payload;r.input_snapshot.attackScenario.categoryName=payload;
  const html=renderAttackReport({runs:[r]},{query:''});
  assert.doesNotMatch(html,/<img|<script|" onclick="boom/);assert.match(html,/&lt;img/);assert.match(html,/SYSTEM/);assert.match(html,/RESPONSE/);
});
test('mount handles category/status/search/expand actions while retaining filter state',()=>{
  const input={focus(){},setSelectionRange(start,end){this.range=[start,end];}};
  const root={innerHTML:'',contains:()=>true,querySelector:()=>input};let changed;
  const report=mountAttackReport(root,{runs:[run('1'),run('2','override',false)]},{onViewChange:v=>changed=v});
  const click=(attribute,key,value)=>root.onclick({target:{closest:()=>({hasAttribute:a=>a===attribute,dataset:{[key]:value}})}});
  input.scrollIntoView=()=>{};click('data-report-category','reportCategory','leak');assert.equal(report.getView().category,'leak');
  click('data-report-status','reportStatus','failure');assert.match(root.innerHTML,/조건에 맞는 실행이 없어요/);
  click('data-report-status','reportStatus','success');click('data-report-expand','reportExpand','');assert.match(root.innerHTML,/data-run-card="1" open/);
  root.oninput({target:{matches:()=>true,value:'검색',selectionStart:2,selectionEnd:2}});assert.equal(changed.query,'검색');assert.deepEqual(input.range,[2,2]);
});
test('preview fixtures yield the labelled 3 categories and 9 runs using the same judge',async()=>{
  let source=readFileSync(new URL('../preview/attacktest/results-demo.js',import.meta.url),'utf8');
  source=source.replace("'./attack-results.js'",JSON.stringify(new URL('../public/attack-results.js',import.meta.url).href)).replace("'./evaluator.mjs'",JSON.stringify(new URL('../preview/attacktest/evaluator.mjs',import.meta.url).href));
  const {createExampleReport}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
  const config=JSON.parse(readFileSync(new URL('../config/email-baseline.json',import.meta.url),'utf8'));
  const report=buildAttackReport(createExampleReport(config.systemPrompt));
  assert.equal(report.groups.length,3);assert.equal(report.counts.total,9);assert.equal(report.counts.success,3);assert.equal(report.counts.failure,3);assert.equal(report.counts.unknown,1);assert.equal(report.counts.unjudged,1);assert.equal(report.counts.error,1);assert.equal(report.counts.asr,50);
});
