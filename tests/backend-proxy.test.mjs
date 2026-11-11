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

test('attachment multipart forwarding preserves boundary, filename and every binary byte',async()=>{
  const fileBytes=new Uint8Array([0,255,128,13,10,34,92,0,65]);
  const form=new FormData();form.append('file',new Blob([fileBytes],{type:'application/x-hwp'}),'한글 첨부.hwp');
  const request=req('/api/attachments',{method:'POST',headers:{Origin:'https://dashboard.example',Authorization:'browser-secret',Cookie:'private-cookie','X-Fincode-Backend-Token':'forged'},body:form});
  const expectedBody=new Uint8Array(await request.clone().arrayBuffer());
  const expectedType=request.headers.get('content-type');
  const attachment={id:'attachment-id',filename:'한글 첨부.hwp',status:'ready',text:'본문\n표 안의 글자\u200b',sha256:'test-hash',warnings:[]};
  let call;
  const response=await handleProjectApi(request,env,config,{fetchImpl:async(url,options)=>{call={url,options};return json({attachment},201);}});
  assert.equal(response.status,201);
  assert.deepEqual(await response.json(),{attachment});
  assert.equal(String(call.url),'https://backend.example/api/attachments');
  assert.deepEqual(call.options.body,expectedBody);
  assert.deepEqual(call.options.headers,{'Content-Type':expectedType,'Accept':'application/json','X-Fincode-Backend-Token':'test-only-token'});
  const forwarded=await new Request(call.url,{method:'POST',headers:call.options.headers,body:call.options.body}).formData();
  assert.equal(forwarded.get('file').name,'한글 첨부.hwp');
  assert.deepEqual(new Uint8Array(await forwarded.get('file').arrayBuffer()),fileBytes);
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
});

test('attachment multipart requires the exact upload route, method and a valid boundary',async()=>{
  let calls=0;
  const options={fetchImpl:async()=>{calls++;return json({});}};
  for(const [path,method,type] of [
    ['/api/summarize','POST','multipart/form-data; boundary=example'],
    ['/api/data/attacks','POST','multipart/form-data; boundary=example'],
    ['/api/attachments','PUT','multipart/form-data; boundary=example'],
    ['/api/attachments/extra','POST','multipart/form-data; boundary=example'],
    ['/api/attachments','POST','application/json'],
    ['/api/attachments','POST','multipart/form-data'],
    ['/api/attachments','POST','multipart/form-data; boundary='],
    ['/api/attachments','POST','multipart/form-data; boundary=""'],
    ['/api/attachments','POST','multipart/form-data; boundary=first; boundary=second'],
    ['/api/attachments','POST','multipart/form-data; boundary='+ 'x'.repeat(71)],
    ['/api/attachments','POST','multipart/form-data; boundary=invalid boundary'],
  ]) {
    const response=await handleProjectApi(req(path,{method,headers:{'Content-Type':type},body:'body'}),env,config,options);
    assert.equal(response.status,400,`${method} ${path} ${type}`);
  }
  assert.equal(calls,0);
  const quoted='multipart/form-data; boundary="valid boundary"';
  let forwardedType;
  assert.equal((await handleProjectApi(req('/api/attachments',{method:'POST',headers:{'Content-Type':quoted},body:'--valid boundary--\r\n'}),env,config,{fetchImpl:async(_,opts)=>{forwardedType=opts.headers['Content-Type'];return json({});}})).status,200);
  assert.equal(forwardedType,quoted);
});

test('attachment upload rejects a hostile origin before reading or forwarding its body',async()=>{
  let calls=0;
  const response=await handleProjectApi(req('/api/attachments',{method:'POST',headers:{Origin:'https://evil.example','Content-Type':'multipart/form-data; boundary=example'},body:'body'}),env,config,{fetchImpl:async()=>{calls++;return json({});}});
  assert.equal(response.status,403);
  assert.equal((await response.json()).error.code,'ORIGIN_REJECTED');
  assert.equal(calls,0);
});

test('upload limit uses streamed bytes with no length or a lying short length and cancels overflow',async()=>{
  const limit=10*1024*1024+64*1024;
  for(const declared of [null,'1']) {
    let cancelled=false,calls=0;
    const stream=new ReadableStream({start(controller){controller.enqueue(new Uint8Array(limit));controller.enqueue(new Uint8Array(1));},cancel(){cancelled=true;}});
    const uploadHeaders={'Content-Type':'multipart/form-data; boundary=example'};
    if(declared!==null)uploadHeaders['Content-Length']=declared;
    const response=await handleProjectApi(req('/api/attachments',{method:'POST',duplex:'half',headers:uploadHeaders,body:stream}),env,config,{fetchImpl:async()=>{calls++;return json({});}});
    assert.equal(response.status,413);
    assert.equal((await response.json()).error.code,'UPLOAD_TOO_LARGE');
    assert.equal(cancelled,true);assert.equal(calls,0);
  }
  let received;
  const response=await handleProjectApi(req('/api/attachments',{method:'POST',headers:{'Content-Type':'multipart/form-data; boundary=example'},body:new Uint8Array(limit)}),env,config,{fetchImpl:async(_,opts)=>{received=opts.body.length;return json({});}});
  assert.equal(response.status,200);assert.equal(received,limit);
});

test('declared upload overflow and malformed lengths never reach the backend',async()=>{
  let calls=0;const opts={fetchImpl:async()=>{calls++;return json({});}};
  const oversized=await handleProjectApi(req('/api/attachments',{method:'POST',headers:{'Content-Type':'multipart/form-data; boundary=example','Content-Length':String(10*1024*1024+64*1024+1)},body:'small'}),env,config,opts);
  assert.equal(oversized.status,413);
  for(const path of ['/api/attachments','/api/data/normal'])for(const length of ['-1','invalid','1.5','1e3','9007199254740992']) {
    const response=await handleProjectApi(req(path,{method:'POST',headers:{'Content-Type':path==='/api/attachments'?'multipart/form-data; boundary=example':'application/json','Content-Length':length},body:'{}'}),env,config,opts);
    assert.equal(response.status,400,`${path}: ${length}`);
  }
  assert.equal(calls,0);
});

test('broken upload stream produces a controlled error without a backend call',async()=>{
  let calls=0;
  const body=new ReadableStream({start(controller){controller.error(new Error('private failure'));}});
  const response=await handleProjectApi(req('/api/attachments',{method:'POST',duplex:'half',headers:{'Content-Type':'multipart/form-data; boundary=example'},body}),env,config,{fetchImpl:async()=>{calls++;return json({});}});
  assert.equal(response.status,400);assert.equal(calls,0);
  assert.doesNotMatch(await response.text(),/private failure/);
});

test('attachment metadata reads preserve JSON responses and backend error status',async()=>{
  const error={error:{code:'ATTACHMENT_NOT_FOUND',message:'첨부파일을 찾을 수 없습니다.'}};
  let target;
  const response=await handleProjectApi(req('/api/attachments/missing'),env,config,{fetchImpl:async(url)=>{target=String(url);return json(error,404);}});
  assert.equal(target,'https://backend.example/api/attachments/missing');
  assert.equal(response.status,404);assert.deepEqual(await response.json(),error);
});
