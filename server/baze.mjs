// Fixed deployment: credentials must never be sent to a user-supplied origin.
export const GATEWAY = 'https://factchat-cloud.mindlogic.ai/v1/gateway';
const MAX_BODY_BYTES = 200000;
const NO_CACHE = {'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'};
const json = (value, status=200) => new Response(JSON.stringify(value), {status,headers:NO_CACHE});
const fail = (code,message,status=400,extra={}) => json({error:{code,message},...extra},status);
const token = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
export function usageOf(usage) {
  return {inputTokens:token(usage?.prompt_tokens),outputTokens:token(usage?.completion_tokens),totalTokens:token(usage?.total_tokens),reasoningTokens:token(usage?.completion_tokens_details?.reasoning_tokens)};
}
export function normalizeModels(data) {
  if (!Array.isArray(data?.data)) return null;
  const seen = new Set();
  return data.data.filter(m => typeof m?.id === 'string' && m.id.trim() && m.id.length <= 200 && !/codex/i.test(m.id) && !seen.has(m.id) && seen.add(m.id))
    .map(m => ({id:m.id,ownedBy:typeof m.owned_by==='string'?m.owned_by:null}));
}
function gatewayError(status) {
  if (status === 401 || status === 403) return fail('GATEWAY_AUTH_ERROR','BAZE 인증 또는 접근 권한을 확인해 주세요. 이 게이트웨이에서 발급한 API 키가 필요해요.',502);
  if (status === 429) return fail('GATEWAY_RATE_LIMIT','BAZE 요청 한도에 도달했어요. 잠시 후 직접 다시 시도해 주세요.',429);
  if (status >= 400 && status < 500) return fail('GATEWAY_REQUEST_REJECTED','BAZE가 요청을 거절했어요. 모델 목록을 새로고침하거나 다른 텍스트 모델을 선택해 주세요.',422);
  return fail('GATEWAY_UNAVAILABLE','BAZE에서 정상 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.',502);
}
async function readInput(request) {
  if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase()!=='application/json') throw new Error('JSON_REQUIRED');
  if (Number(request.headers.get('content-length')) > MAX_BODY_BYTES) throw new Error('BODY_TOO_LARGE');
  const reader=request.body?.getReader();
  if (!reader) throw new Error('INVALID_JSON');
  const chunks=[];let size=0;
  try {
    for (;;) {const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>MAX_BODY_BYTES){await reader.cancel();throw new Error('BODY_TOO_LARGE');}chunks.push(value);}
  } finally {reader.releaseLock();}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
  return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
function validText(value,max,required=false) {return typeof value==='string' && [...value].length<=max && (!required||value.trim().length>0);}
export function validateInput(value) {
  if (!value || typeof value!=='object' || Array.isArray(value)) return 'INVALID_REQUEST';
  if (value.defense!==undefined && value.defense!=='off') return 'UNSUPPORTED_DEFENSE';
  if (value.attachments!==undefined && (!Array.isArray(value.attachments)||value.attachments.length)) return 'UNSUPPORTED_ATTACHMENT';
  if (!validText(value.model,200,true) || /codex/i.test(value.model) || !value.email || typeof value.email!=='object' || Array.isArray(value.email) || !validText(value.email.subject===undefined?'':value.email.subject,200) || !validText(value.email.body,30000,true)) return 'INVALID_REQUEST';
  return null;
}
export async function handleApi(request, env, config, {fetchImpl=fetch,now=()=>performance.now()}={}) {
  const path = new URL(request.url).pathname.replace(/\/$/,'');
  const key = typeof env?.BAZE_API_KEY==='string'?env.BAZE_API_KEY.trim():'';
  if(path==='/api/health' && request.method==='GET') return json({configured:!!key,provider:'baze',promptVersion:config.version,features:{summarize:true,attachments:false,defenses:false}});
  const isModels=path==='/api/models';const isSummary=path==='/api/summarize';
  if(!isModels&&!isSummary) return fail('NOT_FOUND','API 경로를 찾을 수 없어요.',404);
  if(request.method!==(isModels?'GET':'POST')) return fail('METHOD_NOT_ALLOWED','지원하지 않는 요청 방식이에요.',405);
  let input;
  if(isSummary) {
    const origin=request.headers.get('origin');
    if(origin&&origin!==new URL(request.url).origin) return fail('ORIGIN_REJECTED','같은 대시보드에서 요약을 요청해 주세요.',403);
    try {input=await readInput(request);} catch {return fail('INVALID_REQUEST','올바른 JSON 본문을 보내 주세요. 요청은 200 KB 이하여야 해요.');}
    const invalid=validateInput(input);
    if(invalid) return fail(invalid,invalid==='UNSUPPORTED_DEFENSE'?'방어 기법은 아직 구현되지 않았어요. 미적용 설정만 실행할 수 있어요.':invalid==='UNSUPPORTED_ATTACHMENT'?'첨부파일 내용 추출은 아직 구현되지 않았어요. 첨부파일을 제거해 주세요.':'모델, 제목(200자 이하), 본문(1~30,000자)을 확인해 주세요.');
    input.model=input.model.trim();
  }
  if(!key) return fail('API_KEY_MISSING','서버에 BAZE_API_KEY가 설정되지 않았어요. 서버 환경변수 설정 후 다시 연결해 주세요.',503);
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),isModels?20000:config.timeoutMs);
  const propagate=()=>controller.abort();request.signal?.addEventListener('abort',propagate,{once:true});
  if(request.signal?.aborted)controller.abort();
  const started=now();
  try {
    const headers={Authorization:`Bearer ${key}`,Accept:'application/json'};
    const options={method:isModels?'GET':'POST',headers,signal:controller.signal,redirect:'error'};
    if(isSummary){headers['Content-Type']='application/json';options.body=JSON.stringify({model:input.model,messages:[{role:'system',content:config.systemPrompt},{role:'user',content:`제목: ${input.email.subject??''}\n\n${input.email.body}`}],stream:false,max_tokens:config.maxTokens});}
    const response=await fetchImpl(`${GATEWAY}${isModels?'/models/?type=llm':'/chat/completions/'}`,options);
    if(!response.ok) return gatewayError(response.status);
    let data;try {data=await response.json();} catch {return fail('GATEWAY_UNAVAILABLE','BAZE 응답 형식을 읽을 수 없어요.',502);}
    const latencyMs=Math.max(0,Math.round(now()-started));
    if(isModels){const models=normalizeModels(data);return models?json({models,provider:'baze'}):fail('GATEWAY_UNAVAILABLE','BAZE 모델 목록 형식을 읽을 수 없어요.',502);}
    const choice=data?.choices?.[0];const message=choice?.message;
    let summary=typeof message?.content==='string'?message.content:Array.isArray(message?.content)?message.content.filter(p=>p?.type==='text'&&typeof p.text==='string').map(p=>p.text).join('\n'):'';
    if(!summary.trim()&&typeof message?.refusal==='string') summary=message.refusal;
    const usage=usageOf(data?.usage);
    if(!summary.trim()) return fail('EMPTY_RESPONSE','모델이 텍스트 요약을 반환하지 않았어요. 추론에 토큰이 사용되었거나 출력이 제한되었을 수 있어요. 다른 모델로 직접 재시도해 주세요.',502,{usage,latencyMs});
    const finishReason=typeof choice?.finish_reason==='string'?choice.finish_reason:null;
    const warnings=[];
    if(finishReason==='length')warnings.push('출력 토큰 한도에 도달해 요약이 잘렸을 수 있어요.');
    if(finishReason==='content_filter')warnings.push('모델 제공자의 콘텐츠 필터가 응답에 영향을 주었어요.');
    return json({mode:'live',summary,model:typeof data.model==='string'&&data.model?data.model:input.model,requestedModel:input.model,provider:'baze',requestId:typeof data.id==='string'?data.id:null,createdAt:new Date().toISOString(),promptVersion:config.version,defense:'off',usage,latencyMs,finishReason,warnings});
  } catch {
    return controller.signal.aborted?fail('GATEWAY_TIMEOUT','요청이 취소되었거나 응답 대기 시간을 초과했어요. 이미 시작된 모델 호출은 처리·과금되었을 수 있어요.',504):fail('GATEWAY_UNAVAILABLE','BAZE 연결 중 오류가 발생했어요. 잠시 후 직접 다시 시도해 주세요.',502);
  } finally {clearTimeout(timeout);request.signal?.removeEventListener('abort',propagate);}
}
