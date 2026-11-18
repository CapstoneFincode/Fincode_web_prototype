import test from 'node:test';
import assert from 'node:assert/strict';
import {createDashboardController} from '../public/dashboard-controller.js';

test('dashboard coalesces reads, keeps credits independent, and refreshes after cache expiry',async()=>{
  let time=100,calls=[];
  const controller=createDashboardController({now:()=>time,fetchImpl:async(path,options)=>{
    calls.push({path,method:options.method});
    return Response.json(path==='/api/dashboard'?{datasets:{attacks:79}}:{available:false,balance:{remaining:null},error:{message:'잔액 조회 불가'}});
  }});
  const first=controller.load();assert.equal(controller.load(true),first);await first;
  assert.deepEqual(calls,[{path:'/api/dashboard',method:'GET'},{path:'/api/credits',method:'GET'}]);
  assert.equal(controller.state.data.datasets.attacks,79);assert.equal(controller.state.credits.available,false);
  await controller.load();assert.equal(calls.length,2);
  time+=60001;await controller.load();assert.equal(calls.length,4);
});

test('failed refresh clears outdated data while successful credit reading remains visible',async()=>{
  let fail=false;
  const controller=createDashboardController({fetchImpl:async path=>{
    if(path==='/api/credits')return Response.json({available:true,balance:{remaining:0}});
    return fail?Response.json({error:{message:'DB 연결 확인 필요'}},{status:503}):Response.json({datasets:{normal:105}});
  }});
  await controller.load();fail=true;await controller.load(true);
  assert.equal(controller.state.data,null);assert.equal(controller.state.error,'DB 연결 확인 필요');
  assert.equal(controller.state.credits.balance.remaining,0);assert.equal(controller.state.loading,false);
});

test('preview mode never queries team data or the credit gateway',async()=>{
  const controller=createDashboardController({preview:true,fetchImpl:()=>{throw new Error('must not fetch');}});
  await controller.load();assert.match(controller.state.error,/검토 모드/);assert.equal(controller.state.data,null);
});
