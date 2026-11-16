// 기존 GET API만 사용한다. 저장된 실행을 복제하거나 수정하지 않는다.
import {attackOutcome,scenarioInfo,recordedDefense} from './attack-results.js';

export const defaults=()=>({query:'',vector:'all',mode:'all',outcome:'all',sort:'registered'});
export class StaleHistory extends Error {}

export async function load(api,{isCurrent=()=>true,onProgress=()=>{}}={}){
  let abandoned=false;
  const check=()=>{if(!isCurrent()||abandoned)throw new StaleHistory();};
  const parents=new Map();let total=null,pages=1;
  for(let page=1;page<=pages;page++){
    check();
    const data=await api(`/api/experiments?page=${page}&pageSize=100&experimentGroup=defense`);
    check();
    if(!Array.isArray(data?.experiments)||data.page!==page||data.pageSize!==100||!Number.isInteger(data.total)||data.total<0||!Number.isInteger(data.totalPages)||data.totalPages<1)throw Error('방어 실험 목록 형식을 확인할 수 없어요.');
    if(total!==null&&total!==data.total)throw Error('조회 중 실험 목록이 바뀌었어요. 새로고침해 주세요.');
    total=data.total;pages=data.totalPages;
    if(data.experiments.some(e=>!e||e.purpose!=='attack'||typeof e.id!=='string'||parents.has(e.id))||new Set(data.experiments.map(e=>e.id)).size!==data.experiments.length)throw Error('방어 실험 목록에 중복되거나 지원하지 않는 기록이 있어요.');
    for(const row of data.experiments)parents.set(row.id,row);
  }
  if(parents.size!==total)throw Error('방어 실험 목록 일부를 불러오지 못했어요. 새로고침해 주세요.');
  const jobs=[...parents.values()],details=new Array(jobs.length);let cursor=0,done=0;
  async function worker(){
    while(cursor<jobs.length){
      check();const index=cursor++,job=jobs[index];
      const detail=await api('/api/experiments/'+encodeURIComponent(job.id));check();
      if(detail?.experiment?.id!==job.id||detail.experiment.purpose!=='attack'||!Array.isArray(detail.runs)||!Number.isInteger(job.total)||detail.runs.length!==job.total)throw Error('개별 실행 기록 일부를 확인할 수 없어요. 새로고침해 주세요.');
      details[index]=detail;onProgress(++done,jobs.length);
    }
  }
  // 큰 입력 스냅샷의 동시 조회는 두 개로 제한한다.
  try{await Promise.all(Array.from({length:Math.min(2,jobs.length)},worker));}catch(error){abandoned=true;throw error;}check();
  return details;
}

export function flatten(details,attacks=[]){
  const registered=new Map(attacks.map(a=>[a.id,a.registeredAt]));const ids=new Set(),rows=[];
  for(const detail of details)for(const run of detail.runs){
    if(run.expected_label!=='attack'||run.security_gateway_enabled!==true)continue;
    if(typeof run.id!=='string'||ids.has(run.id))throw Error('중복된 개별 실행 기록이 있어요. 새로고침해 주세요.');
    ids.add(run.id);const info=scenarioInfo(run),snapshot=run.input_snapshot||{};
    const source=snapshot.defensePlan?.originalMessages??snapshot.messages??[];
    rows.push({id:run.id,experimentId:detail.experiment.id,name:info.name,scenarioKey:info.id,
      vector:info.vector,mode:recordedDefense(run,detail.experiment.execution_plan?.defense),
      outcome:attackOutcome(run),model:run.requested_model,repeat:run.repeat_index??1,
      createdAt:run.created_at??detail.experiment.created_at,registeredAt:registered.get(info.id),
      search:[info.name,snapshot.attackScenario?.payload,...source.filter(m=>m.role!=='system').map(m=>m.content),...(snapshot.attachments||[]).map(a=>a.text)].filter(v=>typeof v==='string').join('\n').normalize('NFKC').toLocaleLowerCase(),
      run,experiment:detail.experiment});
  }
  return rows;
}

export function select(rows,filters=defaults(),page=1,pageSize=5){
  const query=(filters.query||'').trim().normalize('NFKC').toLocaleLowerCase();
  const timestamp=value=>{const n=Date.parse(value);return Number.isFinite(n)?n:0;};
  const selected=rows.filter(r=>(!query||r.search.includes(query))&&(filters.vector==='all'||r.vector===filters.vector)&&(filters.mode==='all'||r.mode===filters.mode)&&(filters.outcome==='all'||r.outcome===filters.outcome));
  selected.sort((a,b)=>{
    if(filters.sort==='newest')return timestamp(b.createdAt)-timestamp(a.createdAt)||a.id.localeCompare(b.id);
    if(filters.sort==='oldest')return timestamp(a.createdAt)-timestamp(b.createdAt)||a.id.localeCompare(b.id);
    return timestamp(a.registeredAt??a.createdAt)-timestamp(b.registeredAt??b.createdAt)||String(a.scenarioKey).localeCompare(String(b.scenarioKey),'ko')||timestamp(a.createdAt)-timestamp(b.createdAt)||a.id.localeCompare(b.id);
  });
  const total=selected.length,totalPages=Math.max(1,Math.ceil(total/pageSize));page=Math.max(1,Math.min(Number.isInteger(page)?page:1,totalPages));
  return {rows:selected.slice((page-1)*pageSize,page*pageSize),page,total,totalPages};
}
