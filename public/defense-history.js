// 기존 GET API만 사용한다. 저장된 실행을 복제하거나 수정하지 않는다.
import {attackOutcome,scenarioInfo,recordedDefense} from './attack-results.js';

export const defaults=()=>({query:'',vector:'all',mode:'all',outcome:'all',sort:'newest'});
export class StaleHistory extends Error {}

export async function loadSummaries(api,{group='all',check=()=>{}}={}){
  const parents=new Map();let total=null,pages=1,activeCount=0;
  for(let page=1;page<=pages;page++){
    check();
    const data=await api(`/api/experiments?page=${page}&pageSize=100${group==='defense'?'&experimentGroup=defense':''}`);
    check();
    if(!Array.isArray(data?.experiments)||data.page!==page||data.pageSize!==100||!Number.isInteger(data.total)||data.total<0||!Number.isInteger(data.totalPages)||data.totalPages<1)throw Error('방어 실험 목록 형식을 확인할 수 없어요.');
    if(total!==null&&total!==data.total)throw Error('조회 중 실험 목록이 바뀌었어요. 새로고침해 주세요.');
    total=data.total;pages=data.totalPages;activeCount=Math.max(activeCount,data.activeCount||0);
    if(data.experiments.some(e=>!e||!(group==='all'?['attack','normal','performance']:['attack']).includes(e.purpose)||typeof e.id!=='string'||parents.has(e.id))||new Set(data.experiments.map(e=>e.id)).size!==data.experiments.length)throw Error('방어 실험 목록에 중복되거나 지원하지 않는 기록이 있어요.');
    for(const row of data.experiments)parents.set(row.id,row);
  }
  if(parents.size!==total)throw Error('방어 실험 목록 일부를 불러오지 못했어요. 새로고침해 주세요.');
  return {entries:[...parents.values()],activeCount};
}

export async function load(api,{isCurrent=()=>true,onProgress=()=>{},group='defense',summaries=loadSummaries}={}){
  let abandoned=false;
  const check=()=>{if(!isCurrent()||abandoned)throw new StaleHistory();};
  const {entries,activeCount}=await summaries(api,{group,check});
  check();
  const jobs=group==='all'?entries.filter(e=>e.purpose==='attack'&&e.defense_enabled===true):entries,details=new Array(jobs.length);let cursor=0,done=0;
  async function worker(){
    while(cursor<jobs.length){
      check();const index=cursor++,job=jobs[index];
      const detail=await api('/api/experiments/'+encodeURIComponent(job.id));check();
      if(detail?.experiment?.id!==job.id||detail.experiment.purpose!=='attack'||!Array.isArray(detail.runs)||!Number.isInteger(job.total)||detail.runs.length!==job.total)throw Error('개별 실행 기록 일부를 확인할 수 없어요. 새로고침해 주세요.');
      details[index]={...detail,summary:job};onProgress(++done,jobs.length);
    }
  }
  // 큰 입력 스냅샷의 동시 조회는 두 개로 제한한다.
  try{await Promise.all(Array.from({length:Math.min(2,jobs.length)},worker));}catch(error){abandoned=true;throw error;}check();
  return group==='all'?{entries,details,activeCount}:details;
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

// 실행 당시 experiment ID로만 묶는다. 같은 이름·시나리오의 재실험은 별도 행이다.
export function groupExperiments(details,attacks=[]){
  const byExperiment=new Map();
  for(const row of flatten(details,attacks)){
    if(!byExperiment.has(row.experimentId))byExperiment.set(row.experimentId,[]);
    byExperiment.get(row.experimentId).push(row);
  }
  const ids=new Set();
  return details.map(detail=>{
    const e={...detail.summary,...detail.experiment};
    if(ids.has(e.id))throw Error('중복된 실험 기록이 있어요. 새로고침해 주세요.');
    ids.add(e.id);
    return {...e,defense_enabled:true,
      models:e.models??[...new Set(detail.runs.map(r=>r.requested_model).filter(Boolean))],
      total:detail.runs.length,finished:detail.runs.filter(r=>!['queued','running'].includes(r.status)).length,
      defense_modes:[...new Set(detail.runs.map(r=>recordedDefense(r,e.execution_plan?.defense)))],
      runRows:byExperiment.get(e.id)||[],
      active:['queued','running'].includes(e.status)||detail.runs.some(r=>['queued','running'].includes(r.status))};
  });
}

export function selectExperiments(rows,filters=defaults(),page=1,pageSize=5,scope={}){
  const query=(filters.query||'').trim().normalize('NFKC').toLocaleLowerCase();
  const selected=rows.filter(e=>{
    const nameMatches=query&&String(e.name||'').normalize('NFKC').toLocaleLowerCase().includes(query);
    const scoped=e.runRows.filter(r=>(!scope.model||r.model===scope.model)&&(!scope.vector||scope.vector==='all'||r.vector===scope.vector)&&(!scope.provider||r.run.calls?.some(c=>c.provider===scope.provider)||r.experiment.environment_snapshot?.provider===scope.provider||scope.provider==='baze'));
    // 모든 조건은 같은 실행에 적용하되, 해당 실행이 속한 실험 전체를 반환한다.
    const matches=select(scoped,{...filters,query:nameMatches?'':query},1,1).total>0;
    const unrestricted=!query&&['vector','mode','outcome'].every(k=>!filters[k]||filters[k]==='all')&&!scope.model&&(!scope.vector||scope.vector==='all')&&!scope.provider;
    return matches||(!e.runRows.length&&unrestricted);
  });
  const time=e=>Date.parse(e.created_at)||0;
  selected.sort((a,b)=>(filters.sort==='newest'?time(b)-time(a):time(a)-time(b))||a.id.localeCompare(b.id));
  const total=selected.length,totalPages=Math.max(1,Math.ceil(total/pageSize));page=Math.max(1,Math.min(Number.isInteger(page)?page:1,totalPages));
  return {rows:selected.slice((page-1)*pageSize,page*pageSize),page,total,totalPages};
}

// 전체 목록에서도 방어 ON/OFF 모두 원래 실험 단위를 유지한다.
export function combined(loaded,attacks=[]){
  const defenseRows=groupExperiments(loaded.details,attacks),byId=new Map(defenseRows.map(e=>[e.id,e]));
  const rows=loaded.entries.map(e=>byId.get(e.id)||{...e});
  const time=v=>{const n=Date.parse(v);return Number.isFinite(n)?n:0;};
  rows.sort((a,b)=>time(b.created_at)-time(a.created_at)||a.id.localeCompare(b.id));
  return {rows,defenseRows,activeCount:loaded.activeCount};
}
