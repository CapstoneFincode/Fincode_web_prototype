// Read-only comparison of explicitly selected, stored experiment records.
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=value=>typeof value==='number'&&Number.isFinite(value);
const num=(value,digits=0)=>finite(value)?value.toLocaleString('ko-KR',{maximumFractionDigits:digits,minimumFractionDigits:digits}):'—';
const modes=[['filter','입력 필터링'],['separate','명령·데이터 분리'],['both','두 기법 조합']];
const vectors=[['all','전체 경로'],['direct','직접 인젝션'],['indirect','간접 인젝션']];
const vectorNames=Object.fromEntries(vectors);
const attackModes=[['off','방어 미적용'],...modes];
const newSlot=()=>({model:'',defense:'off',vector:'all',record:''});
const names={off:'방어 미적용',...Object.fromEntries(modes),unknown:'기법 기록 확인 필요'};
const statuses={completed:'완료',partial:'일부 완료',failed:'실패',cancelled:'중단',queued:'대기',running:'실행 중'};
const colors=['#567be2','#34a391','#9878cf','#d79b50','#6a94b0'];
const paths={chart:'M4 3v17h17 M8 15v-5 M13 15V6 M18 15v-8',shield:'M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z M8 12l3 3 5-6',arrow:'M5 12h14 M13 6l6 6-6 6',refresh:'M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-1l2 2 M4 16l2 2a7 7 0 0 0 12-1',plus:'M12 5v14 M5 12h14',clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',chevron:'m9 5 7 7-7 7'};
const icon=name=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]||paths.chart}"/></svg>`;
const date=value=>{const d=new Date(value);return value&&Number.isFinite(d.getTime())?d.toLocaleString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'실행일 미기록';};
export const comparisonRecordKey=row=>JSON.stringify([row.experimentId,row.model,row.defense]);
export function createComparisonState(kind){return {kind,records:[],loaded:false,loading:false,page:0,totalPages:1,total:0,ready:false,catalogError:'',model:'',normalModes:['filter'],normalRecords:{off:'',filter:'',separate:'',both:''},slots:[newSlot(),newSlot()],categoryIndex:'0',comparing:false,compareError:'',result:null,epoch:0};}
const states={normal:createComparisonState('normal'),attack:createComparisonState('attack')};
const recordFor=(state,key)=>state.records.find(row=>comparisonRecordKey(row)===key);
const recordChoices=(state,model,defense,vector='all')=>state.records.filter(row=>row.model===model&&row.defense===defense&&(vector==='all'||!row.vectorCounts||row.vectorCounts[vector]>0));
const distinctModels=state=>[...new Set(state.records.filter(row=>attackModes.some(([mode])=>mode===row.defense)).map(row=>row.model))].sort((a,b)=>a.localeCompare(b));
export function comparisonSelections(state){
  const chosen=state.kind==='normal'?['off',...state.normalModes].map(mode=>({model:state.model,mode,key:state.normalRecords[mode]})):state.slots.map(slot=>({model:slot.model,mode:slot.defense,vector:slot.vector,key:slot.record}));
  return chosen.map(({model,mode,key,vector})=>{const record=recordFor(state,key);return record&&record.model===model&&record.defense===mode?{experimentId:record.experimentId,model,defense:mode,...(state.kind==='attack'?{vector}: {})}:null;});
}
export function duplicateSelectionWarning(state){
  if(state.kind!=='attack')return '';
  const keys=comparisonSelections(state).filter(Boolean).map(row=>JSON.stringify(row));
  return new Set(keys).size<keys.length?'동일한 기록·모델·방어·경로가 중복 선택됐어요. 비교는 가능하지만 서로 독립된 추가 실험은 아니에요.':'';
}
export function validateComparison(state){
  if(!state.ready)return 'DB 연결 후 저장된 실험 기록을 비교할 수 있어요.';
  if(state.loading)return '저장된 기록을 불러오고 있어요.';
  if(!state.records.length)return state.kind==='normal'?'비교할 성능 평가 기록이 없어요. 먼저 성능 평가를 실행해 주세요.':'비교할 공격 실험 기록이 없어요. 먼저 공격 실험을 실행해 주세요.';
  if(state.kind==='normal'){
    if(!state.model)return '비교할 모델을 선택해 주세요.';
    if(!state.normalModes.length)return '방어 적용 조건을 한 개 이상 선택해 주세요.';
    if(new Set(state.normalModes).size!==state.normalModes.length||state.normalModes.some(mode=>!modes.some(([id])=>id===mode)))return '비교할 방어 조건을 다시 선택해 주세요.';
  }else{
    if(state.slots.length<2||state.slots.length>5)return '2개에서 5개 비교 대상을 선택해 주세요.';
    if(state.slots.some(slot=>!slot.model))return '각 비교 카드에서 모델을 선택해 주세요.';
    if(state.slots.some(slot=>!attackModes.some(([mode])=>mode===slot.defense)))return '각 비교 카드의 방어 조건을 선택해 주세요.';
    if(state.slots.some(slot=>!vectors.some(([vector])=>vector===slot.vector)))return '각 비교 카드의 공격 경로를 선택해 주세요.';
  }
  if(comparisonSelections(state).some(row=>!row))return '각 조건에 해당하는 평가·실험 기록을 선택해 주세요.';
  return '';
}
function recordSelect(state,{model,defense,key,id,index,mode,vector='all'}){
  const rows=recordChoices(state,model,defense,vector),selected=rows.find(row=>comparisonRecordKey(row)===key);
  return `<label class="rc-field" for="${id}"><span>비교할 실행 기록</span><span class="rc-select"><select id="${id}" data-rc-record="${esc(mode||'')}" ${Number.isInteger(index)?`data-rc-slot="${index}"`:''} ${!state.ready||!model?'disabled':''}><option value="">${!model?'모델을 먼저 선택하세요':rows.length?'실행 기록 선택':'해당 조건의 기록 없음'}</option>${rows.map(row=>`<option value="${esc(comparisonRecordKey(row))}" ${comparisonRecordKey(row)===key?'selected':''}>${esc(row.name||'이름 없는 실험')} · ${esc(date(row.createdAt))} · ${num(row.runCount)}건 · ${esc(statuses[row.status]||row.status)}</option>`).join('')}</select>${icon('chevron')}</span></label>${selected?`<div class="rc-record-meta"><span>${esc(date(selected.createdAt))}</span><span>${state.kind==='attack'&&vector!=='all'&&selected.vectorCounts?`${num(selected.vectorCounts[vector])} / 전체 ${num(selected.runCount)}건`:num(selected.runCount)+'건'} · ${esc(statuses[selected.status]||selected.status)}</span></div><button type="button" class="rc-record-link" data-rc-action="open" data-rc-key="${esc(key)}">선택한 기록 상세${icon('arrow')}</button>`:`<p class="rc-record-empty">${!model?'모델을 선택하면 해당 기록을 표시해요.':rows.length?'실행일과 데이터 수를 확인해 선택하세요.':'목록을 더 불러오거나, 해당 조건으로 실험을 먼저 실행해 주세요.'}</p>`}`;
}
function normalSetup(state){
  const models=distinctModels(state);
  return `<div class="rc-normal-model"><label class="rc-field" for="rc-normal-model"><span>비교할 모델</span><span class="rc-select"><select id="rc-normal-model" data-rc-field="model" ${!state.ready?'disabled':''}><option value="">모델 선택</option>${models.map(model=>`<option value="${esc(model)}" ${state.model===model?'selected':''}>${esc(model)}</option>`).join('')}</select>${icon('chevron')}</span></label><p>같은 모델의 방어 미적용 기록을 기준으로 비교해요.</p></div><fieldset class="rc-modes"><legend>함께 비교할 방어 적용 조건</legend><div>${modes.map(([mode,label])=>`<label class="rc-mode ${state.normalModes.includes(mode)?'is-selected':''}"><input type="checkbox" id="rc-mode-${mode}" data-rc-mode="${mode}" ${state.normalModes.includes(mode)?'checked':''} ${!state.ready?'disabled':''}><span>${label}</span><small>${state.model?`${recordChoices(state,state.model,mode).length}개 기록`:'모델 선택 후 확인'}</small></label>`).join('')}</div></fieldset><div class="rc-record-grid">${['off',...state.normalModes].map((mode,index)=>`<article class="rc-record-card ${mode==='off'?'rc-baseline':''}"><div class="rc-card-heading"><span class="rc-card-number">${mode==='off'?'기준':String(index).padStart(2,'0')}</span><h3>${names[mode]}</h3>${mode==='off'?'<span class="rc-tag">BASELINE</span>':''}</div>${recordSelect(state,{model:state.model,defense:mode,key:state.normalRecords[mode],id:'rc-record-'+mode,mode})}</article>`).join('')}</div>`;
}
function attackSetup(state){
  const models=distinctModels(state),duplicate=duplicateSelectionWarning(state);
  return `<p class="rc-notice rc-independent-note">각 비교 대상의 모델·방어·경로를 자유롭게 선택해요. 조건과 표본이 다를 수 있으므로 전체 결과는 기록별로 따로 집계하며, 방어 효과나 모델 우열을 단정하는 지표는 아니에요.</p><div class="rc-record-grid">${state.slots.map((slot,index)=>`<article class="rc-record-card"><div class="rc-card-heading"><span class="rc-card-number">${String(index+1).padStart(2,'0')}</span><h3>비교 대상 ${index+1}</h3>${state.slots.length>2?`<button type="button" class="rc-remove" data-rc-action="remove" data-rc-index="${index}" aria-label="비교 대상 ${index+1} 제거">제거</button>`:''}</div><label class="rc-field" for="rc-model-${index}"><span>모델</span><span class="rc-select"><select id="rc-model-${index}" data-rc-model="${index}" ${!state.ready?'disabled':''}><option value="">모델 선택</option>${models.map(model=>`<option value="${esc(model)}" ${slot.model===model?'selected':''}>${esc(model)}</option>`).join('')}</select>${icon('chevron')}</span></label><div class="rc-card-controls"><label class="rc-field" for="rc-defense-${index}"><span>방어 조건</span><span class="rc-select"><select id="rc-defense-${index}" data-rc-defense="${index}" ${!state.ready?'disabled':''}>${attackModes.map(([value,label])=>`<option value="${value}" ${slot.defense===value?'selected':''}>${label}</option>`).join('')}</select>${icon('chevron')}</span></label><label class="rc-field" for="rc-vector-${index}"><span>공격 경로</span><span class="rc-select"><select id="rc-vector-${index}" data-rc-vector="${index}" ${!state.ready?'disabled':''}>${vectors.map(([value,label])=>`<option value="${value}" ${slot.vector===value?'selected':''}>${label}</option>`).join('')}</select>${icon('chevron')}</span></label></div>${recordSelect(state,{model:slot.model,defense:slot.defense,vector:slot.vector,key:slot.record,id:'rc-attack-record-'+index,index})}</article>`).join('')}</div><div class="rc-add-model"><button type="button" class="btn" data-rc-action="add" ${state.slots.length>=5||!state.ready?'disabled':''}>${icon('plus')}비교 대상 추가</button><span>${state.slots.length} / 5개 · 같은 모델도 선택 가능</span></div>${duplicate?`<p class="rc-warnings rc-duplicate" role="status">${duplicate}</p>`:''}`;
}
const valueOf=(row,key)=>row.metrics?.[key];
const labelOf=(group,kind)=>kind==='normal'?names[group.defense]||group.defense:`대상 ${Number.isInteger(group.index)?group.index+1:'—'} · ${group.model} · ${names[group.defense]||group.defense} · ${vectorNames[group.vector]||group.vector}`;
function delta(value,baseline,{scale=1,unit='',digits=1}={}){if(!finite(value)||!finite(baseline))return '기준 대비 —';const d=(value-baseline)*scale;return `기준 대비 ${d>0?'+':''}${num(d,digits)}${unit}`;}
function chart(groups,kind,{key,title,description,unit='',scale=1,max,digits=1,deltaUnit=unit}){
  const values=groups.map(row=>valueOf(row,key)),limit=max??Math.max(1,...values.filter(finite).map(value=>value*scale));
  const baseline=groups.find(group=>group.defense==='off');
  return `<section class="rc-chart panel"><div class="rc-section-heading"><div><h3>${title}</h3><p>${description}</p></div><span class="rc-unit">${unit||'점수'}</span></div><div class="rc-bars">${groups.map((group,index)=>{const value=valueOf(group,key),known=finite(value),width=known?Math.max(0,Math.min(100,value*scale/limit*100)):0;return `<div class="rc-bar-row"><div class="rc-bar-label"><strong><i style="background:${colors[index%colors.length]}" aria-hidden="true"></i>${esc(labelOf(group,kind))}</strong><b>${known?num(value*scale,digits)+unit:'—'}</b></div><div class="rc-bar-track" aria-hidden="true">${known?`<span style="width:${width}%;background:${colors[index%colors.length]}"></span>`:''}</div><small>${!known?(kind==='attack'&&(group.metrics===null||group.selectedRuns===0)?'기록 없음':kind==='attack'&&key==='asr'?'확정 판정 없음':'측정된 기록이 없어요.'):kind==='normal'?(group.defense!=='off'?delta(value,valueOf(baseline||{},key),{scale,unit:deltaUnit,digits}):'방어 미적용 기준'):`선택 경로 ${num(group.selectedRuns)}건 · 시나리오 ${num(group.scenarioCount)}개`}</small></div>`;}).join('')}</div></section>`;
}
function outcomes(groups){
  const segments=[['successes','공격 성공','rc-outcome-success'],['failures','공격 실패','rc-outcome-failure'],['indeterminate','미확정','rc-outcome-unknown'],['unjudged','미판정','rc-outcome-unjudged'],['errors','오류','rc-outcome-error'],['pending','대기·실행 중','rc-outcome-pending'],['cancelled','중단','rc-outcome-cancelled']];
  return `<section class="rc-outcomes panel"><div class="rc-section-heading"><div><h3>비교 대상별 공격 판정 구성</h3><p>입력 단계 차단은 확정 판정에 따라 공격 실패에 포함되며, 별도 건수도 표시해요.</p></div></div><div class="rc-outcome-legend">${segments.map(([,label,cls])=>`<span><i class="${cls}"></i>${label}</span>`).join('')}</div><div class="rc-outcome-rows">${groups.map(group=>{const total=segments.reduce((sum,[key])=>sum+(valueOf(group,key)||0),0);return `<div><div class="rc-bar-label"><strong>${esc(labelOf(group,'attack'))}</strong><span>입력 차단 ${num(valueOf(group,'blocked'))}건</span></div><div class="rc-stacked" aria-hidden="true">${segments.map(([key,,cls])=>`<span class="${cls}" style="width:${total?100*(valueOf(group,key)||0)/total:0}%"></span>`).join('')}</div><p>${group.metrics===null?'이 카테고리에 해당하는 기록 없음':segments.map(([key,label])=>`${label} ${num(valueOf(group,key))}`).join(' · ')}</p></div>`;}).join('')}</div></section>`;
}
function resultTable(result){
  const normal=result.kind==='normal';
  return `<section class="panel rc-table-panel"><div class="rc-section-heading"><div><h3>비교 수치와 표본</h3><p>각 지표의 측정 건수와 제외된 실행을 함께 확인하세요.</p></div></div><div class="rc-table-scroll"><table><caption class="screen-reader">${normal?'정상 이메일 방어 조건별':'공격 실험 모델별'} 비교 지표</caption><thead><tr><th scope="col">${normal?'방어 조건':'비교 대상'}</th><th scope="col">${normal?'공통 / 선택 / 제외':'선택 경로 / 원본 / 경로 제외'}</th><th scope="col">${normal?'요약 유사도':'공격 성공률'}</th><th scope="col">${normal?'정상 차단·거부':'입력 단계 차단'}</th>${normal?'<th scope="col">핵심 정보 자동 확인률</th>':''}<th scope="col">모델 호출 시간</th><th scope="col">평균 입력 / 출력 / 전체 토큰</th><th scope="col">오류 / 미완료</th></tr></thead><tbody>${result.groups.map(group=>{const m=group.metrics||{};return `<tr><th scope="row"><strong>${esc(labelOf(group,result.kind))}</strong><small>${esc(group.name)}</small></th><td>${normal?`${num(group.matchedRuns)} / ${num(group.selectedRuns)} / ${num(group.excludedRuns)}`:`${num(group.selectedRuns)} / ${num(group.sourceRuns)} / ${num(group.excludedRuns)}<small>시나리오 ${num(group.scenarioCount)}개</small>`}</td><td><strong>${normal?finite(m.meanRougeL)?num(m.meanRougeL*100,1)+'%':'—':finite(m.asr)?num(m.asr,1)+'%':'—'}</strong><small>${normal?`${num(m.gradedCount)}건 채점`:`성공 ${num(m.successes)} / 확정 ${num(m.judged)}`}</small></td><td>${normal?finite(m.normalRejectionRate)?num(m.normalRejectionRate,1)+'%':'—':num(m.blocked)+'건'}<small>입력 차단 ${num(m.blocked)}${normal?` · 거부 후보 ${num(m.refusalCandidates)}`:''}</small></td>${normal?`<td>${finite(m.meanFactCoverage)?num(m.meanFactCoverage*100,1)+'%':'—'}<small>규칙 기반 보조 지표</small></td>`:''}<td>${finite(m.latencyMsMean)?num(m.latencyMsMean/1000,2)+'초':'—'}<small>${num(m.latencyCount)}건 측정</small></td><td>${num(m.inputTokensMean,1)} / ${num(m.outputTokensMean,1)} / ${num(m.totalTokensMean,1)}<small>전체 토큰 ${num(m.tokensCount)}건 측정</small></td><td>${num(m.errors)} / ${num(finite(m.pending)&&finite(m.cancelled)?m.pending+m.cancelled:null)}<small>대기·실행 중 ${num(m.pending)} · 중단 ${num(m.cancelled)}</small></td></tr>`;}).join('')}</tbody></table></div></section>`;
}
function categoryGroups(result,category){
  return result.groups.map(group=>{const item=category.groups?.find(entry=>entry.index===group.index);return {...group,selectedRuns:item?.runCount??0,scenarioCount:item?item.scenarioCount:0,metrics:item?.metrics??null};});
}
function attackCategories(result,categoryIndex){
  const categories=Array.isArray(result.categories)?result.categories:[];
  if(!categories.length)return `<section class="rc-empty panel"><h2>카테고리별 기록이 없어요.</h2><p>선택한 공격 경로에 해당하는 기록이 있는지 확인해 주세요.</p></section>`;
  const selectedIndex=Math.max(0,Math.min(categories.length-1,Number.parseInt(categoryIndex,10)||0)),selected=categories[selectedIndex],selectedGroups=categoryGroups(result,selected);
  return `<section class="panel rc-table-panel rc-category-matrix"><div class="rc-section-heading"><div><span class="rc-kicker">03 · CATEGORY COMPARISON</span><h3>카테고리별 공격 성공률</h3><p>카테고리를 선택하면 아래에서 대상별 판정 구성을 자세히 볼 수 있어요. 같은 카테고리라도 시나리오와 표본 수는 다를 수 있어요.</p></div></div><div class="rc-table-scroll"><table><caption class="screen-reader">카테고리 행과 비교 대상 열의 공격 성공률 및 표본 수</caption><thead><tr><th scope="col">공격 카테고리</th>${result.groups.map(group=>`<th scope="col"><strong>${esc(labelOf(group,'attack'))}</strong></th>`).join('')}</tr></thead><tbody>${categories.map((category,index)=>`<tr class="${index===selectedIndex?'is-selected':''}"><th scope="row"><button type="button" class="rc-category-button" data-rc-action="category" data-rc-index="${index}" aria-pressed="${index===selectedIndex}">${esc(category.name||'미분류')}${icon('arrow')}</button></th>${categoryGroups(result,category).map(group=>{const m=group.metrics;return `<td>${m?`<strong>${finite(m.asr)?num(m.asr,1)+'%':'—'}</strong><small>${finite(m.asr)?`성공 ${num(m.successes)} / 확정 ${num(m.judged)}`:'확정 판정 없음'}</small><small>시나리오 ${num(group.scenarioCount)}개 · ${num(group.selectedRuns)}건</small>`:'<strong>—</strong><small>기록 없음</small>'}</td>`;}).join('')}</tr>`).join('')}</tbody></table></div></section><section class="rc-category-detail" aria-label="선택한 카테고리 상세"><div class="panel rc-category-controls"><div><span class="rc-kicker">CATEGORY DETAIL</span><h3>선택한 카테고리 비교</h3><p>선택한 카테고리 안에서도 각 대상의 실험 조건과 시나리오 구성이 다를 수 있어요.</p></div><label class="rc-field" for="rc-category-select"><span>공격 카테고리</span><span class="rc-select"><select id="rc-category-select" data-rc-category="true">${categories.map((category,index)=>`<option value="${index}" ${index===selectedIndex?'selected':''}>${esc(category.name||'미분류')}</option>`).join('')}</select>${icon('chevron')}</span></label></div>${chart(selectedGroups,'attack',{key:'asr',title:esc(selected.name||'미분류')+' · 공격 성공률',description:'성공 ÷ (성공 + 실패) · 확정 판정이 없으면 — 표시',unit:'%',max:100})}${outcomes(selectedGroups)}</section>`;
}
function renderAttackResult(result,categoryIndex){
  const groups=Array.isArray(result.groups)?result.groups:[],warnings=Array.isArray(result.warnings)?result.warnings:[];
  const warning=warnings.length?`<div class="rc-warnings" role="status"><strong>비교 조건 확인</strong><ul>${warnings.map(message=>`<li>${esc(message)}</li>`).join('')}</ul></div>`:'';
  const caution='<p class="rc-notice rc-independent-note">대상별 조건과 표본이 다를 수 있어요. 각 선택 기록과 공격 경로를 독립 집계하며, 동일한 데이터로 통제한 비교가 아니므로 모델 우열이나 방어 효과를 단정하지 마세요.</p>';
  if(!result.comparable||!groups.length)return `${caution}<section class="rc-empty panel"><span class="rc-empty-icon">${icon('chart')}</span><h2>선택한 조건의 공격 기록이 없어요.</h2><p>모델·방어·공격 경로에 해당하는 실험 기록을 선택해 주세요. 기록 없음은 공격 성공률 0%와 달라요.</p>${warning}</section>`;
  const fixed={...result,groups};
  return `<div class="rc-result-head"><div><span class="rc-kicker">02 · OVERALL COMPARISON</span><h2>선택한 기록별 전체 결과</h2></div><span>${esc(date(result.generatedAt))} · 한국시간</span></div>${caution}${warning}<div class="rc-independent-samples" aria-label="비교 대상별 조건과 표본">${groups.map(group=>`<article><strong>${esc(labelOf(group,'attack'))}</strong><div><span>선택 경로 <b>${num(group.selectedRuns)}건</b></span><span>시나리오 <b>${num(group.scenarioCount)}개</b></span></div><p>원본 ${num(group.sourceRuns)}건 · 경로 필터 제외 ${num(group.excludedRuns)}건</p></article>`).join('')}</div><div class="rc-chart-grid">${chart(groups,'attack',{key:'asr',title:'대상별 공격 성공률',description:'ASR · 성공 ÷ (성공 + 실패) · 대상별 선택 기록 기준',unit:'%',max:100})}${chart(groups,'attack',{key:'latencyMsMean',title:'실제 모델 호출 시간',description:'모델을 호출한 실행만 집계 · 입력 차단은 별도 표시',unit:'초',scale:.001,digits:2})}</div>${outcomes(groups)}<div class="rc-chart-grid">${chart(groups,'attack',{key:'inputTokensMean',title:'평균 입력 토큰',description:'실제 모델 호출에서 API가 보고한 사용량',unit:'토큰'})}${chart(groups,'attack',{key:'totalTokensMean',title:'평균 전체 토큰',description:'실제 호출의 입력 + 출력 · 사용량 누락은 제외',unit:'토큰'})}</div>${resultTable(fixed)}${attackCategories(fixed,categoryIndex)}<div class="rc-method"><strong>비교 지표 읽는 방법</strong><p>미확정·미판정·오류는 ASR 분모에서 제외해요. 입력 단계 차단 건수는 판정 구성과 중복되므로 합산하지 마세요. 모델 호출 시간·토큰 평균에는 실제로 호출하고 측정값이 있는 실행만 포함해요. 서로 다른 모델의 토큰은 같은 텍스트량이나 같은 비용을 뜻하지 않아요.</p></div>`;
}

export function renderComparisonResult(result,{kind='normal',categoryIndex='0'}={}){
  if(result?.kind==='attack')return renderAttackResult(result,categoryIndex);
  if(!result&&kind==='attack')return `<section class="rc-empty panel"><span class="rc-empty-icon">${icon('chart')}</span><h2>비교 대상을 자유롭게 구성하세요.</h2><p>각 기록의 전체 결과와 카테고리별 결과를 나란히 표시해요. 서로 다른 조건과 표본을 선택할 수 있어요.</p><span>저장된 결과만 조회 · 모델 추가 호출 없음</span></section>`;
  if(!result)return `<section class="rc-empty panel"><span class="rc-empty-icon">${icon('chart')}</span><h2>조건이 같은 기록을 나란히 비교하세요.</h2><p>비교할 기록을 선택하면 공통 데이터와 실행 조건을 확인한 뒤 그래프를 표시해요.</p><span>저장된 결과만 조회 · 모델 추가 호출 없음</span></section>`;
  const groups=Array.isArray(result.groups)?result.groups:[],normal=result.kind==='normal';
  const warning=Array.isArray(result.warnings)&&result.warnings.length?`<div class="rc-warnings" role="status"><strong>비교 조건 확인</strong><ul>${result.warnings.map(message=>`<li>${esc(message)}</li>`).join('')}</ul></div>`:'';
  if(!result.comparable||!groups.length)return `<section class="rc-empty panel"><span class="rc-empty-icon">${icon('shield')}</span><h2>비교할 공통 기록이 없어요.</h2><p>원본 데이터·버전·기본 실행 조건이 맞는 기록을 선택해 주세요. 기록이 없다는 뜻이며 0점이 아니에요.</p>${warning}</section>`;
  const fixed={...result,groups};
  return `<div class="rc-result-head"><div><span class="rc-kicker">COMPARISON REPORT</span><h2>저장된 결과 비교</h2></div><span>${esc(date(result.generatedAt))} · 한국시간</span></div><section class="rc-cohort" aria-label="비교 대상"><div><span>공통 ${normal?'정상 이메일':'공격 시나리오'}</span><strong>${num(result.commonCases)}<small>개</small></strong></div><div><span>조건별 공통 실행</span><strong>${num(result.commonRuns)}<small>건</small></strong></div><div><span>비교 ${normal?'방어 조건':'모델'}</span><strong>${groups.length}<small>개</small></strong></div><p>실행 당시 원본과 버전을 맞춰 비교해요.<br>차단된 실행도 공통 표본에 포함해요.</p></section>${warning}<div class="rc-matched-summary" aria-label="조건별 비교 표본">${groups.map(group=>`<div><strong>${esc(labelOf(group,result.kind))}</strong><span>공통 ${num(group.matchedRuns)} / 선택 ${num(group.selectedRuns)} / 제외 ${num(group.excludedRuns)}건</span></div>`).join('')}</div><div class="rc-chart-grid">${normal?chart(groups,'normal',{key:'meanRougeL',title:'요약 유사도',description:'ROUGE-L F1 · 높을수록 기준 요약과 유사',unit:'%',scale:100,max:100,deltaUnit:'%p'}):chart(groups,'attack',{key:'asr',title:'모델별 공격 성공률',description:'ASR · 성공 ÷ (성공 + 실패) · 낮을수록 공격에 강함',unit:'%',max:100})}${normal?chart(groups,'normal',{key:'normalRejectionRate',title:'정상 이메일 차단·거부율',description:'정상 입력 차단과 모델 거부 후보 · 낮을수록 정상 기능 유지',unit:'%',max:100,deltaUnit:'%p'}):chart(groups,'attack',{key:'latencyMsMean',title:'실제 모델 호출 시간',description:'모델을 호출한 실행만 집계 · 차단은 별도 표시',unit:'초',scale:.001,digits:2})}</div>${!normal?outcomes(groups):''}<div class="rc-chart-grid">${normal?chart(groups,'normal',{key:'latencyMsMean',title:'실제 모델 호출 시간',description:'방어 전처리를 포함한 전체 처리시간과 달라요.',unit:'초',scale:.001,digits:2}):chart(groups,'attack',{key:'inputTokensMean',title:'평균 입력 토큰',description:'실제 모델 호출에서 API가 보고한 사용량',unit:'토큰'})}${chart(groups,result.kind,{key:'totalTokensMean',title:'평균 전체 토큰',description:'실제 호출의 입력 + 출력 · 사용량 누락은 제외',unit:'토큰'})}</div>${resultTable(fixed)}<div class="rc-method"><strong>비교 지표 읽는 방법</strong><p>모델 호출 전 차단은 모델 사용량 0이지만, 처리시간이 0이라는 뜻은 아니에요. 호출 시간·토큰 평균은 실제 호출만 집계해요. ${normal?'거부 후보는 자동 탐지 결과이며 확정 오탐과 구분해 검토해 주세요. 핵심 정보 미확인은 오답을 뜻하지 않아요.':'미확정·미판정·오류는 ASR 분모에서 제외해요. 서로 다른 모델의 토큰은 같은 텍스트량이나 같은 비용을 의미하지 않아요.'}</p></div>`;
}
export function renderComparisonContent(state){
  const normal=state.kind==='normal',problem=validateComparison(state);
  return `<header class="page-head rc-page-head"><div><div class="eyebrow">SAVED RESULT COMPARISON</div><h1>${normal?'단일 모델 성능 평가 간 비교':'공격 실험 결과 성능 비교'}</h1><p>${normal?'같은 모델의 방어 적용 전후를 비교하고, 정상 요약 기능에 미친 영향을 확인하세요.':'모델·방어·공격 경로를 대상별로 선택하고, 전체 결과와 카테고리별 결과를 비교하세요.'}</p></div></header><div class="rc-readonly-note">${icon('shield')}<span>저장된 실행 기록을 비교해요. 이 화면에서는 모델을 호출하거나 실험을 새로 실행하지 않아요.</span></div><section class="panel rc-setup" aria-busy="${state.loading}"><div class="rc-section-heading"><div><span class="rc-kicker">01 · SELECT RECORDS</span><h2>비교 조건과 실행 기록</h2></div><button type="button" class="btn" data-rc-action="reload" ${!state.ready||state.loading||state.comparing?'disabled':''}>${icon('refresh')}기록 새로고침</button></div><div class="rc-setup-body">${!state.ready?'<p class="rc-notice" role="status">DB 연결 후 저장된 기록을 비교할 수 있어요. 연결 상태를 확인해 주세요.</p>':''}${state.catalogError?`<p class="rc-error" role="alert">${esc(state.catalogError)}</p>`:''}${state.loading&&!state.loaded?'<p class="rc-notice" role="status">저장된 실험 기록을 불러오고 있어요…</p>':''}${state.loaded&&!state.records.length?`<div class="rc-no-records"><strong>${normal?'저장된 성능 평가 기록이 없어요.':'저장된 공격 실험 기록이 없어요.'}</strong><p>먼저 ${normal?'성능 평가를':'공격 실험을'} 실행한 뒤 이 화면에서 비교해 주세요.</p><a class="btn" href="#${normal?'single-evaluation':'experiments'}">${normal?'성능 평가로':'공격 실험으로'} 이동${icon('arrow')}</a></div>`:''}${normal?normalSetup(state):attackSetup(state)}<div class="rc-catalog-meta"><span>실험·모델·방어 조건별 기록 ${state.records.length} / ${num(state.total)}개 · 실행일은 한국시간</span>${state.loaded&&state.page<state.totalPages?`<button type="button" class="btn" data-rc-action="more" ${!state.ready||state.loading||state.comparing?'disabled':''}>${state.loading?'불러오는 중…':'이전 기록 더 불러오기'}</button>`:''}</div><div class="rc-compare-actions"><div><strong>${normal?'방어 미적용 기준과 선택한 적용 조건':'대상별 모델·방어·경로와 실행 기록'}을 확인해요.</strong><p id="rc-validation" role="status">${esc(state.comparing?(normal?'선택한 기록의 원본·버전과 공통 표본을 확인하고 있어요…':'선택한 기록의 전체·카테고리별 결과를 집계하고 있어요…'):problem||(normal?'비교할 기록이 선택됐어요. 공통 데이터와 실행 조건을 확인할 수 있어요.':'비교할 기록이 선택됐어요. 대상별 조건과 표본은 서로 다를 수 있어요.'))}</p></div><button type="button" class="btn primary" data-rc-action="compare" aria-describedby="rc-validation" ${problem||state.comparing?'disabled':''}>${icon('chart')}${state.comparing?'결과 비교 중…':'저장된 결과 비교'}</button></div>${state.compareError?`<p class="rc-error" role="alert">${esc(state.compareError)}</p>`:''}</div></section><div class="rc-report" aria-live="polite" aria-busy="${state.comparing}">${renderComparisonResult(state.result,{kind:state.kind,categoryIndex:state.categoryIndex})}</div>`;
}
export function renderResultComparison(kind){const state=states[kind];if(!state)throw Error('지원하지 않는 비교 화면입니다.');return `<div class="rc-page" data-result-comparison="${kind}">${renderComparisonContent(state)}</div>`;}
function chooseDefaultRecords(state){
  if(state.kind==='normal'){
    if(!state.model){const baseline=state.records.find(row=>row.defense==='off'&&state.records.some(other=>other.model===row.model&&modes.some(([mode])=>mode===other.defense)))||state.records.find(row=>row.defense==='off');state.model=baseline?.model||'';}
    for(const mode of ['off',...modes.map(m=>m[0])])if(!recordFor(state,state.normalRecords[mode])||recordFor(state,state.normalRecords[mode])?.model!==state.model)state.normalRecords[mode]=recordChoices(state,state.model,mode)[0]?comparisonRecordKey(recordChoices(state,state.model,mode)[0]):'';
  }else{
    const models=distinctModels(state);
    for(const [index,slot] of state.slots.entries()){
      if(!slot.model){slot.model=models[index%Math.max(models.length,1)]||'';if(!recordChoices(state,slot.model,slot.defense,slot.vector).length){const first=state.records.find(row=>row.model===slot.model&&attackModes.some(([mode])=>mode===row.defense));if(first)slot.defense=first.defense;}}
      const choices=recordChoices(state,slot.model,slot.defense,slot.vector);
      if(!choices.some(row=>comparisonRecordKey(row)===slot.record))slot.record=choices[0]?comparisonRecordKey(choices[0]):'';
    }
  }
}
export function mountResultComparison(container,{kind,api,ready=false,onOpenRecord}={}){
  const state=states[kind];if(!state||!container)return ()=>{};
  const root=container.matches?.(`[data-result-comparison="${kind}"]`)?container:container.querySelector?.(`[data-result-comparison="${kind}"]`);
  if(!root)return ()=>{};
  let active=true,catalogRequest=0,compareRequest=0;const epoch=++state.epoch;state.ready=Boolean(ready);state.loading=false;state.comparing=false;
  const current=(token,channel)=>active&&state.epoch===epoch&&(token===undefined||token===(channel==='catalog'?catalogRequest:compareRequest));
  const draw=focusId=>{if(!current())return;root.innerHTML=renderComparisonContent(state);if(focusId)root.querySelector?.('#'+focusId)?.focus?.({preventScroll:true});};
  const invalidate=()=>{compareRequest++;state.comparing=false;state.result=null;state.compareError='';state.categoryIndex='0';};
  async function loadRecords(more=false){
    if(!state.ready||state.loading)return;
    invalidate();const token=++catalogRequest,page=more?state.page+1:1;state.loading=true;state.catalogError='';draw();
    try{const data=await api(`/api/results/comparison-records?kind=${kind}&page=${page}&pageSize=50`);if(!current(token,'catalog'))return;
      if(!Array.isArray(data?.records)||data.page!==page||!Number.isInteger(data.totalPages)||data.totalPages<1||!Number.isInteger(data.total)||data.total<0)throw Error('기록 목록의 응답 형식을 확인할 수 없어요. 다시 불러와 주세요.');
      const rows=data.records.filter(row=>row&&typeof row.experimentId==='string'&&typeof row.model==='string'&&typeof row.defense==='string');
      state.records=[...new Map([...(more?state.records:[]),...rows].map(row=>[comparisonRecordKey(row),row])).values()];state.page=page;state.totalPages=data.totalPages;state.total=data.total;state.loaded=true;chooseDefaultRecords(state);
    }catch(error){if(current(token,'catalog')){state.catalogError=error.message||'저장된 기록을 불러오지 못했어요.';}}
    finally{if(current(token,'catalog')){state.loading=false;draw();}}
  }
  async function compare(){
    if(validateComparison(state)||state.comparing)return;
    const token=++compareRequest;state.comparing=true;state.compareError='';state.result=null;draw();
    try{const data=await api('/api/results/compare','POST',{kind,selections:comparisonSelections(state),...(kind==='normal'?{vector:'all'}:{})});if(!current(token))return;if(!data||data.kind!==kind||!Array.isArray(data.groups)||typeof data.comparable!=='boolean')throw Error('비교 결과 형식을 확인할 수 없어요. 다시 시도해 주세요.');state.result=data;
    }catch(error){if(current(token))state.compareError=error.message||'선택한 기록을 비교하지 못했어요.';}
    finally{if(current(token)){state.comparing=false;draw();root.querySelector?.('.rc-report')?.scrollIntoView?.({block:'start',behavior:'smooth'});}}
  }
  function change(event){
    const target=event.target;if(!target?.dataset||!root.contains(target))return;
    const focus=target.id;let changed=false;
    if(target.dataset.rcCategory!==undefined){state.categoryIndex=target.value;draw(focus);return;}
    if(target.dataset.rcField==='model'){state.model=target.value;state.normalRecords={off:'',filter:'',separate:'',both:''};chooseDefaultRecords(state);changed=true;}
    else if(target.dataset.rcMode){const selected=modes.map(([mode])=>mode).filter(mode=>mode===target.dataset.rcMode?target.checked:state.normalModes.includes(mode));state.normalModes=selected;changed=true;}
    else if(target.dataset.rcModel!==undefined||target.dataset.rcDefense!==undefined||target.dataset.rcVector!==undefined){const field=target.dataset.rcModel!==undefined?'model':target.dataset.rcDefense!==undefined?'defense':'vector';const index=Number(target.dataset.rcModel??target.dataset.rcDefense??target.dataset.rcVector);if(state.slots[index]){state.slots[index][field]=target.value;state.slots[index].record='';chooseDefaultRecords(state);changed=true;}}
    else if(target.dataset.rcRecord!==undefined){if(target.dataset.rcSlot!==undefined){const index=Number(target.dataset.rcSlot);if(state.slots[index]){state.slots[index].record=target.value;changed=true;}}else if(Object.hasOwn(state.normalRecords,target.dataset.rcRecord)){state.normalRecords[target.dataset.rcRecord]=target.value;changed=true;}}
    if(changed){invalidate();draw(focus);}
  }
  function click(event){
    const button=event.target?.closest?.('[data-rc-action]');if(!button||!root.contains(button)||button.disabled)return;
    const action=button.dataset.rcAction;
    if(action==='reload'){void loadRecords();return;}if(action==='more'){void loadRecords(true);return;}if(action==='compare'){void compare();return;}
    if(action==='add'&&state.slots.length<5){invalidate();state.slots.push(newSlot());draw('rc-model-'+(state.slots.length-1));}
    else if(action==='remove'&&state.slots.length>2){const index=Number(button.dataset.rcIndex);if(Number.isInteger(index)&&index>=0&&index<state.slots.length){invalidate();state.slots.splice(index,1);draw();}}
    else if(action==='category'){state.categoryIndex=button.dataset.rcIndex;draw('rc-category-select');root.querySelector?.('.rc-category-detail')?.scrollIntoView?.({block:'start',behavior:'smooth'});}
    else if(action==='open'){const row=recordFor(state,button.dataset.rcKey);if(row&&onOpenRecord)onOpenRecord({experimentId:row.experimentId,kind});}
  }
  root.addEventListener('change',change);root.addEventListener('click',click);draw();
  if(state.ready&&!state.loaded)void loadRecords();
  return ()=>{active=false;catalogRequest++;compareRequest++;if(state.epoch===epoch){state.loading=false;state.comparing=false;}root.removeEventListener('change',change);root.removeEventListener('click',click);};
}
