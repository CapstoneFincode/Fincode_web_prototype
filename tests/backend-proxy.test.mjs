import test from 'node:test';
import assert from 'node:assert/strict';
import {handleProjectApi} from '../server/backend-proxy.mjs';
import {readFileSync} from 'node:fs';
const config=JSON.parse(readFileSync(new URL('../config/email-baseline.json',import.meta.url)));
const env={BACKEND_API_URL:'https://backend.example',BACKEND_SHARED_TOKEN:'test-only-token'};
const req=(path='/api/data',options={})=>new Request('https://dashboard.example'+path,options);
const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json'}});

test('Worker forwards API requests only to configured backend with server token',async()=>{
  let call;
  const result=await handleProjectApi(req('/api/data/normal',{method:'POST',headers:{Origin:'https://dashboard.example','Content-Type':'application/json',Authorization:'browser-token',Cookie:'private-cookie'},body:'{"title":"test"}'}),env,config,{fetchImpl:async(url,options)=>{call={url,options};return json({saved:true});}});
  assert.equal(result.status,200);assert.equal(String(call.url),'https://backend.example/api/data/normal');
  assert.deepEqual(call.options.headers,{'Content-Type':'application/json','Accept':'application/json','X-Fincode-Backend-Token':'test-only-token'});
  assert.equal(call.options.redirect,'manual');assert.equal(new TextDecoder().decode(call.options.body),'{"title":"test"}');
  assert.equal(result.headers.get('cache-control'),'no-store');
});
test('Worker rejects invalid destination and cross-origin mutations before network',async()=>{
  let calls=0;const opts={fetchImpl:async()=>{calls++;return json({});}};
  for(const url of ['http://backend.example','https://user:password@backend.example','https://backend.example/nested','https://backend.example/?token=x'])assert.equal((await handleProjectApi(req(),{...env,BACKEND_API_URL:url},config,opts)).status,503);
  assert.equal((await handleProjectApi(req('/api/data',{method:'POST',headers:{Origin:'https://evil.example'}}),env,config,opts)).status,403);
  assert.equal(calls,0);
});
test('Worker rejects large streamed bodies and does not pass redirects or HTML',async()=>{
  let calls=0;
  const oversized=await handleProjectApi(req('/api/data/normal',{method:'POST',headers:{'Content-Type':'application/json'},body:'x'.repeat(200001)}),env,config,{fetchImpl:async()=>{calls++;return json({});}});
  assert.equal(oversized.status,400);assert.equal(calls,0);
  for(const response of [new Response(null,{status:302,headers:{Location:'https://evil.example'}}),new Response('<html/>')])assert.equal((await handleProjectApi(req(),env,config,{fetchImpl:async()=>response})).status,503);
});
test('Worker default mode honestly reports DB unconfigured and keeps baseline API',async()=>{
  const result=await handleProjectApi(req('/api/health'),{},config,{fetchImpl:()=>{throw Error('no call');}});
  assert.deepEqual((await result.json()).database,{configured:false,ready:false});
  assert.equal((await handleProjectApi(req(),{},config)).status,404);
});
test('Worker backend outage does not fall back to an unrecorded BAZE call',async()=>{
  let calls=0;const result=await handleProjectApi(req('/api/summarize',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),{...env,BAZE_API_KEY:'do-not-use'},config,{fetchImpl:async()=>{calls++;throw Error('network');}});
  assert.equal(result.status,503);assert.equal((await result.json()).error.code,'BACKEND_UNAVAILABLE');assert.equal(calls,1);
});
