import { handleApi } from './baze.mjs';
const headers={'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'};
const fail=(code,message,status=503)=>new Response(JSON.stringify({error:{code,message}}),{status,headers});
const JSON_BODY_LIMIT=200000;
// The backend independently enforces the 10 MiB file limit. Leave only a small
// multipart envelope here, and bound actual streamed bytes even without a length.
const UPLOAD_BODY_LIMIT=10*1024*1024+64*1024;

function validMultipartBoundary(contentType) {
  const parameters=contentType.split(';').slice(1).map(value=>value.trim());
  const boundaries=parameters.filter(value=>/^boundary\s*=/i.test(value));
  if(boundaries.length!==1)return false;
  const value=boundaries[0].slice(boundaries[0].indexOf('=')+1).trim();
  const quoted=value.startsWith('"')&&value.endsWith('"');
  const boundary=quoted?value.slice(1,-1):value;
  if(!quoted&&/\s/.test(boundary))return false;
  return /^[0-9A-Za-z'()+_,./:=? -]{0,69}[0-9A-Za-z'()+_,./:=?-]$/.test(boundary);
}

// Only deployment-owned configuration selects the backend. Never accept a browser URL/key.
export async function handleProjectApi(request,env,config,{fetchImpl=fetch}={}) {
  if (!env.BACKEND_API_URL && !env.BACKEND_SHARED_TOKEN) {
    const response=await handleApi(request,env,config,{fetchImpl});
    if(new URL(request.url).pathname==='/api/health') {
      return new Response(JSON.stringify({...await response.json(),database:{configured:false,ready:false}}),{status:response.status,headers});
    }
    return response;
  }
  let backend;
  try {
    backend=new URL(env.BACKEND_API_URL);
    if(backend.protocol!=='https:'||backend.username||backend.password||backend.search||backend.hash||backend.pathname!=='/'||!env.BACKEND_SHARED_TOKEN)throw Error();
  }catch{return fail('BACKEND_CONFIG_INVALID','서버의 BACKEND_API_URL(HTTPS 주소)과 BACKEND_SHARED_TOKEN을 확인해 주세요.');}
  const incoming=new URL(request.url);
  const origin=request.headers.get('origin');
  if(origin!==null && origin!==incoming.origin)return fail('ORIGIN_REJECTED','같은 대시보드에서 요청해 주세요.',403);
  if(!['GET','POST','PUT','DELETE'].includes(request.method))return fail('METHOD_NOT_ALLOWED','지원하지 않는 요청 방식입니다.',405);
  let body;
  let forwardedContentType='application/json';
  if(request.method!=='GET') {
    const contentType=request.headers.get('content-type')||'';
    const mediaType=contentType.split(';')[0].trim().toLowerCase();
    const upload=request.method==='POST'&&incoming.pathname==='/api/attachments';
    if(upload) {
      if(mediaType!=='multipart/form-data'||!validMultipartBoundary(contentType))return fail('INVALID_REQUEST','파일은 경계 정보가 포함된 multipart/form-data 형식으로 보내 주세요.',400);
      forwardedContentType=contentType;
    } else if(mediaType!=='application/json')return fail('INVALID_REQUEST','JSON 요청이 필요합니다.',400);
    const limit=upload?UPLOAD_BODY_LIMIT:JSON_BODY_LIMIT;
    const tooLarge=()=>upload?fail('UPLOAD_TOO_LARGE','첨부파일은 10 MiB 이하여야 합니다.',413):fail('INVALID_REQUEST','요청 크기는 200 KB 이하여야 합니다.',400);
    const length=request.headers.get('content-length');
    if(length!==null) {
      if(!/^\d+$/.test(length)||!Number.isSafeInteger(Number(length)))return fail('INVALID_REQUEST','요청 크기가 올바르지 않습니다.',400);
      if(Number(length)>limit)return tooLarge();
    }
    const reader=request.body?.getReader();
    if(!reader)return fail('INVALID_REQUEST','요청 본문이 필요합니다.',400);
    const chunks=[];let size=0;
    try {
      for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>limit){await reader.cancel();return tooLarge();}chunks.push(value);}
      body=new Uint8Array(size);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.length;}
    }catch{return fail('INVALID_REQUEST','요청 본문을 읽을 수 없습니다. 파일을 다시 선택해 주세요.',400);
    }finally{reader.releaseLock();}
  }
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),130000);
  try {
    const response=await fetchImpl(new URL(incoming.pathname+incoming.search,backend),{
      method:request.method,body,redirect:'manual',signal:controller.signal,
      headers:{'Content-Type':forwardedContentType,'Accept':'application/json','X-Fincode-Backend-Token':env.BACKEND_SHARED_TOKEN},
    });
    if(response.status>=300&&response.status<400)return fail('BACKEND_REDIRECT_REJECTED','DB 백엔드 주소의 리디렉션 설정을 확인해 주세요.');
    if(!response.headers.get('content-type')?.includes('application/json'))return fail('BACKEND_INVALID_RESPONSE','DB 백엔드 응답 형식을 확인해 주세요.');
    return new Response(await response.text(),{status:response.status,headers});
  }catch{return fail('BACKEND_UNAVAILABLE','DB 백엔드에 연결하지 못했습니다. 실행 기록을 확인한 뒤 다시 시도해 주세요.');}
  finally{clearTimeout(timer);}
}
