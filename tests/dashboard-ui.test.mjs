import test from 'node:test';
import assert from 'node:assert/strict';
import {renderDashboard} from '../public/dashboard.js';

const makeData = () => ({
  generatedAt:'2026-11-16T05:00:00Z',
  datasets:{normal:105,attacks:79,direct:60,indirect:19,categories:7,groundTruth:{total:100,ready:100,error:null}},
  experiments:{total:55,active:0,recent:[{id:'recent',name:'실제 DB 실험',purpose:'attack',status:'completed',models:['model-a'],defense:'both',createdAt:'2026-11-16T04:00:00Z'}]},
  performance:{total:3,completed:3,modelCount:3,recent:[{id:'evaluation',name:'정상 이메일 100건 평가',purpose:'performance',status:'completed',models:['model-b'],defense:'off',createdAt:'2026-11-16T04:00:00Z'}]},
});
const makeCredits = () => ({available:true,provider:'baze',unit:'credits',balance:{remaining:123.45,used:76.55,quota:200},renewalDate:'2026-12-01T00:00:00Z',checkedAt:'2026-11-16T05:00:00Z',cached:false,error:null});

test('dashboard displays current DB records and feature navigation without historical sample claims',()=>{
  const html=renderDashboard({data:makeData(),credits:makeCredits()});
  for(const text of ['실제 DB 실험','정상 이메일 100건 평가','model-a','model-b','두 기법 조합','방어 OFF','평가에 등록된 모델'])assert.ok(html.includes(text),text);
  for(const route of ['email','data','experiments','single-evaluation','comparison','results'])assert.ok(html.includes(`href="#${route}"`),route);
  assert.doesNotMatch(html,/9월 30일|10월 29일|61\.22|undefined|NaN/);
  assert.match(html,/data-action="dashboard-refresh"/);
  assert.doesNotMatch(html,/data-action="(?:run|submit|delete)/);
});

test('missing credit balance remains unavailable, while a measured zero is exhausted',()=>{
  const unavailable=renderDashboard({credits:{...makeCredits(),available:false,balance:{remaining:null,used:null,quota:null},error:{code:'UPSTREAM',message:'잔액 조회를 지원하지 않아요.'}}});
  assert.match(unavailable,/dash-credit-value mono">—<span>크레딧/);
  assert.match(unavailable,/잔액 조회를 지원하지 않아요\./);
  assert.doesNotMatch(unavailable,/잔액이 소진되었어요/);
  const zero=renderDashboard({credits:{...makeCredits(),balance:{remaining:0,used:200,quota:200}}});
  assert.match(zero,/dash-credit-value mono">0<span>크레딧/);
  assert.match(zero,/잔액이 소진되었어요/);
  assert.doesNotMatch(zero,/잔액 확인 불가/);
});

test('credits preserve fractional values, optional unknown fields, and negative balance',()=>{
  const fractional=renderDashboard({credits:{...makeCredits(),balance:{remaining:0.00001,used:null,quota:null}}});
  assert.match(fractional,/0\.00001<span>크레딧/);
  assert.match(fractional,/<dt>사용 크레딧<\/dt><dd class="mono">—<\/dd>/);
  assert.doesNotMatch(fractional,/<progress/);
  const negative=renderDashboard({credits:{...makeCredits(),balance:{remaining:-1.2,used:201.2,quota:200}}});
  assert.match(negative,/>-1\.2<span>크레딧/);
  assert.match(negative,/잔액이 소진되었어요/);
  assert.match(negative,/<progress[^>]*max="200" value="200"/);
});

test('transport failure never continues showing a previously successful balance or DB record',()=>{
  const html=renderDashboard({data:makeData(),error:'DB 연결 실패',credits:makeCredits(),creditsError:'크레딧 조회 실패'});
  assert.match(html,/role="alert"/);assert.match(html,/DB 연결 실패/);assert.match(html,/크레딧 조회 실패/);
  assert.doesNotMatch(html,/실제 DB 실험|정상 이메일 100건 평가|123\.45/);
  assert.match(html,/dash-credit-value mono">—<span>크레딧/);
});

test('experiment and evaluation lists are capped independently at five records',()=>{
  const data=makeData();
  data.experiments.recent=Array.from({length:7},(_,i)=>({...data.experiments.recent[0],name:`EXPERIMENT_${i}`}));
  data.performance.recent=Array.from({length:7},(_,i)=>({...data.performance.recent[0],name:`EVALUATION_${i}`}));
  const html=renderDashboard({data});
  assert.match(html,/EXPERIMENT_4/);assert.match(html,/EVALUATION_4/);
  assert.doesNotMatch(html,/EXPERIMENT_[56]|EVALUATION_[56]/);
});

test('external record names, model IDs, and error messages cannot inject markup',()=>{
  const data=makeData();
  data.experiments.recent[0].name='<img src=x onerror="alert(1)">';
  data.performance.recent[0].models=['<script>bad()</script>'];
  data.datasets.groundTruth.error='<svg onload=bad()>';
  const html=renderDashboard({data,credits:{...makeCredits(),available:false,error:{message:'<iframe src="bad">'}}});
  assert.match(html,/&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html,/&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.match(html,/&lt;svg onload=bad\(\)&gt;/);
  assert.match(html,/&lt;iframe src=&quot;bad&quot;&gt;/);
  assert.doesNotMatch(html,/<img|<script|<iframe|<svg onload/);
});

test('loading and empty states do not invent measurements or imply completed model evaluation',()=>{
  const loading=renderDashboard({loading:true,creditsLoading:true});
  assert.match(loading,/data-action="dashboard-refresh" disabled/);
  assert.match(loading,/실험 기록을 불러오고 있어요/);
  assert.doesNotMatch(loading,/undefined|NaN|잔액이 소진/);
  const data=makeData();
  data.experiments={total:0,active:0,recent:[]};
  data.performance={total:0,completed:0,modelCount:0,recent:[]};
  const empty=renderDashboard({data});
  assert.match(empty,/아직 저장된 실험이 없어요/);
  assert.match(empty,/첫 성능 평가를 시작해 보세요/);
  assert.match(empty,/성능 평가 기록 기준 · 대기 포함/);
  assert.doesNotMatch(empty,/모델별 순위|최고 성능|완료된 모델/);
});
