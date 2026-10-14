import { handleApi } from './baze.mjs';
const headers={'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'};
const fail=(code,message,status=503)=>new Response(JSON.stringify({error:{code,message}}),{status,headers});

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
  if(request.method!=='GET') {
    if(request.headers.get('content-type')?.split(';')[0].trim().toLowerCase()!=='application/json')return fail('INVALID_REQUEST','JSON 요청이 필요합니다.',400);
    if(Number(request.headers.get('content-length'))>200000)return fail('INVALID_REQUEST','요청 크기는 200 KB 이하여야 합니다.',400);
    const reader=request.body?.getReader();
    if(!reader)return fail('INVALID_REQUEST','요청 본문이 필요합니다.',400);
    const chunks=[];let size=0;
    try {
      for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>200000){await reader.cancel();return fail('INVALID_REQUEST','요청 크기는 200 KB 이하여야 합니다.',400);}chunks.push(value);}
      body=new Uint8Array(size);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.length;}
    }finally{reader.releaseLock();}
  }
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),130000);
  try {
    const response=await fetchImpl(new URL(incoming.pathname+incoming.search,backend),{
      method:request.method,body,redirect:'manual',signal:controller.signal,
      headers:{'Content-Type':'application/json','Accept':'application/json','X-Fincode-Backend-Token':env.BACKEND_SHARED_TOKEN},
    });
    if(response.status>=300&&response.status<400)return fail('BACKEND_REDIRECT_REJECTED','DB 백엔드 주소의 리디렉션 설정을 확인해 주세요.');
    if(!response.headers.get('content-type')?.includes('application/json'))return fail('BACKEND_INVALID_RESPONSE','DB 백엔드 응답 형식을 확인해 주세요.');
    return new Response(await response.text(),{status:response.status,headers});
  }catch{return fail('BACKEND_UNAVAILABLE','DB 백엔드에 연결하지 못했습니다. 실행 기록을 확인한 뒤 다시 시도해 주세요.');}
  finally{clearTimeout(timer);}
}
