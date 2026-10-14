import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {handleApi,GATEWAY,usageOf} from '../server/baze.mjs';
const config=JSON.parse(readFileSync(new URL('../config/email-baseline.json',import.meta.url)));
const input={model:'gemini-test',email:{subject:'회의',body:'내일 17시 연구실에서 만납시다.'},defense:'off',attachments:[]};
const req=(body=input,headers={})=>new Request('https://fincode.example/api/summarize',{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});
const env={BAZE_API_KEY:'test-secret-never-in-output'};
const payload={id:'chat-test',model:'gemini-test',choices:[{message:{content:'내일 17시 연구실 회의'},finish_reason:'stop'}],usage:{prompt_tokens:31,completion_tokens:12,total_tokens:43,completion_tokens_details:{reasoning_tokens:3}}};
test('health exposes configuration state without key and no network call',async()=>{
  for(const [e,configured] of [[{},false],[env,true]]){
    const r=await handleApi(new Request('https://fincode.example/api/health'),e,config,{fetchImpl:()=>assert.fail('network')});
    const data=await r.json();assert.equal(data.configured,configured);assert.equal(data.features.attachments,false);assert.doesNotMatch(JSON.stringify(data),/test-secret/);
  }
});
test('missing key returns actionable error without network',async()=>{
  const r=await handleApi(req(),{},config,{fetchImpl:()=>assert.fail('network')});assert.equal(r.status,503);assert.equal((await r.json()).error.code,'API_KEY_MISSING');
});
test('real gateway request uses fixed origin, baseline prompt, no retries or synthetic metrics',async()=>{
  let calls=0;let tick=0;
  const r=await handleApi(req(),env,config,{now:()=>tick++*421,fetchImpl:async(url,options)=>{
    calls++;assert.equal(url,GATEWAY+'/chat/completions/');assert.equal(options.headers.Authorization,'Bearer '+env.BAZE_API_KEY);assert.equal(options.redirect,'manual');
    const b=JSON.parse(options.body);assert.equal(b.model,input.model);assert.equal(b.max_tokens,16384);assert.equal(b.stream,false);assert.equal(b.messages[0].content,config.systemPrompt);assert.equal(b.messages[1].content,'제목: 회의\n\n내일 17시 연구실에서 만납시다.');assert.equal(b.temperature,undefined);assert.equal(b.tools,undefined);
    return Response.json(payload);
  }});
  assert.equal(calls,1);const data=await r.json();assert.equal(data.mode,'live');assert.equal(data.summary,payload.choices[0].message.content);assert.equal(data.latencyMs,421);assert.deepEqual(data.usage,{inputTokens:31,outputTokens:12,totalTokens:43,reasoningTokens:3});assert.equal(data.promptVersion,config.version);assert.equal(r.headers.get('cache-control'),'no-store');
});
test('models come from live text-model discovery and exclude unsupported Codex models',async()=>{
  const r=await handleApi(new Request('https://fincode.example/api/models'),env,config,{fetchImpl:async(url)=>{
    assert.equal(url,GATEWAY+'/models/?type=llm');return Response.json({data:[{id:'gemini-live',owned_by:'google'},{id:'gpt-codex'},{id:'gemini-live'},{id:null},{id:'claude-live'}]});
  }});
  assert.deepEqual((await r.json()).models,[{id:'gemini-live',ownedBy:'google'},{id:'claude-live',ownedBy:null}]);
});
test('missing usage stays null, valid zero remains zero',()=>{
  assert.deepEqual(usageOf(null),{inputTokens:null,outputTokens:null,totalTokens:null,reasoningTokens:null});
  assert.deepEqual(usageOf({prompt_tokens:0,completion_tokens:-1,total_tokens:'5'}),{inputTokens:0,outputTokens:null,totalTokens:null,reasoningTokens:null});
});
test('reject unsupported defense, attachment, blank/oversized body before spending tokens',async()=>{
  for(const [body,code] of [[{...input,defense:'filter'},'UNSUPPORTED_DEFENSE'],[{...input,attachments:[{name:'x.pdf'}]},'UNSUPPORTED_ATTACHMENT'],[{...input,email:{body:'  '}},'INVALID_REQUEST'],[{...input,email:{body:'x'.repeat(30001)}},'INVALID_REQUEST']]){
    const r=await handleApi(req(body),env,config,{fetchImpl:()=>assert.fail('network')});assert.equal(r.status,400);assert.equal((await r.json()).error.code,code);
  }
  const r=await handleApi(req({...input,padding:'x'.repeat(200001)}),env,config,{fetchImpl:()=>assert.fail('network')});assert.equal(r.status,400);
});
test('cross-origin browser POST is rejected',async()=>{
  const r=await handleApi(req(input,{origin:'https://evil.example'}),env,config,{fetchImpl:()=>assert.fail('network')});assert.equal(r.status,403);
});
test('gateway errors do not leak raw credential, prompt or response details and never retry',async()=>{
  for(const [status,expected,code] of [[401,502,'GATEWAY_AUTH_ERROR'],[403,502,'GATEWAY_AUTH_ERROR'],[429,429,'GATEWAY_RATE_LIMIT'],[400,422,'GATEWAY_REQUEST_REJECTED'],[500,502,'GATEWAY_UNAVAILABLE']]){
    let calls=0;const r=await handleApi(req(),env,config,{fetchImpl:async()=>{calls++;return new Response('sensitive upstream text '+env.BAZE_API_KEY,{status});}});
    assert.equal(r.status,expected);const data=await r.json();assert.equal(data.error.code,code);assert.doesNotMatch(JSON.stringify(data),/sensitive|test-secret/);assert.equal(calls,1);
  }
});
test('empty reasoning response preserves measured usage and latency as an error',async()=>{
  const r=await handleApi(req(),env,config,{fetchImpl:async()=>Response.json({...payload,choices:[{message:{content:''},finish_reason:'length'}]})});const data=await r.json();assert.equal(r.status,502);assert.equal(data.error.code,'EMPTY_RESPONSE');assert.equal(data.usage.totalTokens,43);assert.ok(data.latencyMs>=0);
});
test('refusal text and truncated output are visible, not silently replaced with a reference',async()=>{
  const r=await handleApi(req(),env,config,{fetchImpl:async()=>Response.json({...payload,choices:[{message:{content:null,refusal:'거절 응답'},finish_reason:'length'}]})});const data=await r.json();assert.equal(data.summary,'거절 응답');assert.equal(data.warnings.length,1);
});
test('timeout aborts once and returns controlled error',async()=>{
  const r=await handleApi(req(),env,{...config,timeoutMs:5},{fetchImpl:async(_,options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(new Error('aborted'))))});assert.equal(r.status,504);assert.equal((await r.json()).error.code,'GATEWAY_TIMEOUT');
});
