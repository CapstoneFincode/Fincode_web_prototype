// Shared by the real experiment detail and the explicitly labelled sample view.
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const resultLabels={all:'전체',success:'공격 성공',failure:'공격 실패',unknown:'미확정',unjudged:'미판정',error:'실행 오류',pending:'대기·실행 중',cancelled:'취소'};
const rate=(n,d)=>d?(100*n/d).toFixed(1)+'%':'—';
const number=v=>Number.isFinite(v)&&v>=0?v.toLocaleString('ko-KR'):'—';
const vectorName=v=>({direct:'직접 인젝션',indirect:'간접 인젝션'}[v]||'경로 기록 없음');
const content=m=>typeof m.content==='string'?m.content:JSON.stringify(m.content??'',null,2);
export function attackVerdict(run){
  const all=(run.evaluations||[]).filter(v=>v.metric==='attack_success');
  return all.filter(v=>v.judge_type==='human').at(-1)||all.at(-1)||null;
}
export function attackOutcome(run){
  if(['failed','timed_out'].includes(run.status))return 'error';
  if(['queued','running'].includes(run.status))return 'pending';
  if(run.status==='cancelled')return 'cancelled';
  if(!['succeeded','blocked'].includes(run.status))return 'unjudged';
  const v=attackVerdict(run);
  if(v?.decision_status==='determinate'&&typeof v.boolean_value==='boolean')return v.boolean_value?'success':'failure';
  return v?.decision_status==='indeterminate'?'unknown':'unjudged';
}
export function scenarioInfo(run,categories=[]){
  const frozen=run.input_snapshot?.attackScenario||{};
  const categoryId=frozen.categoryId||run.input_snapshot?.attackCategoryId||run.attack_category_id||'uncategorized';
  return {id:frozen.key||run.attack_scenario_key||frozen.id||run.attack_scenario_id||null,
    name:frozen.name||run.attack_scenario_name||'시나리오 이름 기록 없음',
    revision:frozen.revision??run.attack_scenario_revision,
    vector:frozen.vector||run.attack_vector,
    categoryId,categoryName:frozen.categoryName||run.attack_category_name||categories.find(c=>c.id===categoryId)?.name||(categoryId==='uncategorized'?'미분류':'분류 이름 기록 없음'),
    frozen:!!frozen.categoryName};
}
export function requestMessages(run){
  const calls=run.calls||[];
  const call=calls.find(c=>c.purpose==='summarize'&&Array.isArray(c.request_snapshot?.messages))||calls.find(c=>Array.isArray(c.request_snapshot?.messages));
  if(call)return {messages:call.request_snapshot.messages,source:'sent'};
  return {messages:Array.isArray(run.input_snapshot?.messages)?run.input_snapshot.messages:[],source:'planned'};
}
export function attackCounts(runs){
  const c={total:runs.length,success:0,failure:0,unknown:0,unjudged:0,error:0,pending:0,cancelled:0,automaticKnown:0,completed:0};
  for(const run of runs){
    c[attackOutcome(run)]++;
    if(['succeeded','blocked'].includes(run.status)){
      c.completed++;
      const rule=(run.evaluations||[]).filter(v=>v.metric==='attack_success'&&v.judge_type==='rule').at(-1);
      if(rule?.decision_status==='determinate'&&typeof rule.boolean_value==='boolean')c.automaticKnown++;
    }
  }
  c.known=c.success+c.failure;c.asr=c.known?100*c.success/c.known:null;
  return c;
}
export function buildAttackReport(detail,categories=[]){
  const runs=(detail.runs||[]).filter(r=>r.expected_label==='attack');
  const groups=new Map();
  for(const run of runs){
    const s=scenarioInfo(run,categories);
    if(!groups.has(s.categoryId))groups.set(s.categoryId,{id:s.categoryId,name:s.categoryName,runs:[],names:new Set(),scenarios:new Set()});
    const group=groups.get(s.categoryId);group.runs.push(run);group.names.add(s.categoryName);if(s.id)group.scenarios.add(s.id);
  }
  return {runs,counts:attackCounts(runs),groups:[...groups.values()].map(g=>({...g,counts:attackCounts(g.runs),scenarioCount:g.scenarios.size}))};
}
export function filterAttackRuns(runs,view={},categories=[]){
  const query=(view.query||'').trim().toLocaleLowerCase();
  return runs.filter(r=>{
    const s=scenarioInfo(r,categories);
    if(view.category&&view.category!=='all'&&s.categoryId!==view.category)return false;
    if(view.status&&view.status!=='all'&&attackOutcome(r)!==view.status)return false;
    if(!query)return true;
    const text=[s.name,s.categoryName,r.id,r.requested_model,r.final_output,r.error_message,attackVerdict(r)?.evidence,...requestMessages(r).messages.map(content)].join(' ').toLocaleLowerCase();
    return text.includes(query);
  });
}
function badge(status){return `<span class="ar-badge ${status}">${resultLabels[status]}</span>`;}
function messageHtml(run){
  const request=requestMessages(run);
  if(!request.messages.length)return '<p class="ar-empty-small">프롬프트 기록이 없어요.</p>';
  return `<p class="ar-source">${request.source==='sent'?'호출 기록에 저장된 실제 전송 메시지':'호출 기록 없음 · 실행 예정 입력 스냅샷'}</p>`+request.messages.map(m=>m.role==='system'?`<details class="ar-system"><summary>SYSTEM · 시스템 지침</summary><pre>${esc(content(m))}</pre></details>`:`<div class="ar-message"><span>${esc(m.role?.toUpperCase()||'MESSAGE')}</span><pre>${esc(content(m))}</pre></div>`).join('');
}
export function attackImages(run){
  const attachments=run.input_snapshot?.attachments;
  if(!Array.isArray(attachments))return [];
  return attachments.filter(a=>a&&['image/png','image/jpeg'].includes(a.mediaType)&&typeof a.id==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(a.id));
}
function promptHtml(run){
  const images=attackImages(run);
  if(!images.length)return messageHtml(run);
  const body=run.input_snapshot?.attackScenario?.payload??run.input_snapshot?.email?.body;
  return `<p class="ar-source">실행에 사용한 원본 이미지 · ${images.length}장</p><div class="ar-images">${images.map((a,i)=>`<figure class="ar-image"><a href="/api/attachments/${a.id}/image" target="_blank" rel="noopener noreferrer" aria-label="${esc(a.filename)} 원본 이미지 열기"><img data-attack-image src="/api/attachments/${a.id}/image" alt="첨부 이미지 ${i+1}: ${esc(a.filename)}" loading="lazy" decoding="async"></a><p class="ar-image-error" hidden>원본 이미지를 불러오지 못했어요. 아래에서 저장된 OCR 텍스트를 확인해 주세요.</p><figcaption><span>${i+1}. ${esc(a.filename)}</span><a href="/api/attachments/${a.id}/image" target="_blank" rel="noopener noreferrer">원본 보기 ↗</a></figcaption></figure>`).join('')}</div>${typeof body==='string'&&body.trim()?`<div class="ar-message"><span>함께 전송한 본문</span><pre>${esc(body)}</pre></div>`:''}<details class="ar-raw ar-ocr"><summary>OCR 추출 텍스트 · 실제 전송 프롬프트</summary>${messageHtml(run)}</details>`;
}
function canaryContextHtml(verdict){
  const occurrences=verdict?.details?.occurrences;if(!Array.isArray(occurrences)||!occurrences.length)return '';
  const names={emitted:'독립 출력',refused_mention:'거부 속 언급',quoted_or_reported:'인용·예시·전달',input_echo:'입력 복사',ambiguous_context:'불명확한 문맥'};
  return `<details class="ar-raw"><summary>표식이 나온 위치와 문맥 ${occurrences.length}건</summary><p class="ar-source">줄바꿈·유니코드 정규화 후 응답 기준 위치예요.</p>${occurrences.map(o=>`<p>${esc(names[o.kind]||'검토 필요')} · ${esc(o.start)}~${esc(o.end)}자</p><pre>${esc(o.context)}</pre>`).join('')}</details>`;
}
export function renderAttackRun(run,index,options={}){
  const s=scenarioInfo(run,options.categories),status=attackOutcome(run),v=attackVerdict(run);
  const call=(run.calls||[]).find(c=>c.purpose==='summarize')||run.calls?.[0]||{};
  const reason=status==='error'?(run.error_message||'실행 오류로 공격 성공 여부를 판정하지 않았어요.'):status==='pending'?'아직 실행이 완료되지 않았어요.':status==='cancelled'?'실행이 취소됐어요.':v?.evidence||'자동 판정이 없어요. 응답을 검토한 뒤 판정을 저장해 주세요.';
  const signal={refusal_hint:'거부 표현 감지 · 보조 정보',unverified_instruction_claim:'원문과 일치하지 않는 지침 주장 · 보조 정보',no_match:'원문 일치 없음'}[v?.details?.responseSignal];
  const response=run.final_output??(['queued','running'].includes(run.status)?'응답 대기 중':'저장된 응답 없음');
  return `<details class="ar-run" data-run-card="${esc(run.id)}" ${options.openAll?'open':''}><summary><span class="ar-chevron" aria-hidden="true">⌄</span><span class="ar-run-number">#${esc(run.case_number??index+1)}</span><span class="ar-run-title"><strong>${esc(s.name)}</strong><small>${esc(s.categoryName)} · ${vectorName(s.vector)} · 반복 ${esc(run.repeat_index??1)}</small></span>${badge(status)}</summary><div class="ar-run-body"><div class="ar-reason ${status}"><span>판정 근거</span><p>${esc(reason)}</p>${v?`<small>${v.judge_type==='human'?'수동 판정 우선':'자동 규칙 판정'} · ${esc(v.evaluator_version)}${signal?' · '+esc(signal):''}</small>`:''}</div>${canaryContextHtml(v)}<div class="ar-conversation"><section class="ar-prompt"><h4>${attackImages(run).length?'INPUT <span>첨부 이미지</span>':'PROMPT <span>입력 프롬프트</span>'}</h4>${promptHtml(run)}</section><section class="ar-response"><h4>RESPONSE <span>모델 응답</span></h4><pre>${esc(response)}</pre>${v?.details?.match?`<details class="ar-match"><summary>판정에 일치한 문자열</summary><pre>${esc(v.details.match.text)}</pre></details>`:''}</section></div><div class="ar-run-meta">${run.input_snapshot?.inputMode==='scenario-only-v1'?'<span>시나리오 단독 입력 · 기준 이메일 없음</span>':''}${run.input_snapshot?.inputMode==='attachment-v1'?`<span>첨부파일 간접 입력 · ${(run.input_snapshot.attachments||[]).map(a=>esc(a.filename)).join(', ')}</span>`:''}${!s.frozen?'<span>이전 실행 · 분류명은 현재 등록 정보로 표시</span>':''}<span>모델 <strong>${esc(run.requested_model||'기록 없음')}</strong></span><span>응답시간 <strong>${Number.isFinite(run.backend_latency_ms)?(run.backend_latency_ms/1000).toFixed(2)+'초':'—'}</strong></span><span>입력 / 출력 토큰 <strong>${number(call.input_tokens)} / ${number(call.output_tokens)}</strong></span><span>전체 토큰 <strong>${number(run.service_total_tokens)}</strong></span></div><details class="ar-raw"><summary>실행 기록 · 판정 이력 ${run.evaluations?.length||0}건</summary><pre>${esc(JSON.stringify({runId:run.id,scenario:s,inputSnapshot:run.input_snapshot,calls:run.calls,evaluations:run.evaluations},null,2))}</pre></details>${options.renderReview?options.renderReview(run):''}</div></details>`;
}
export function renderAttackReport(detail,view={},options={}){
  const report=buildAttackReport(detail,options.categories),c=report.counts;
  const category=view.category||'all',status=view.status||'all';
  const scoped=filterAttackRuns(report.runs,{...view,status:'all'},options.categories),sc=attackCounts(scoped);
  const filtered=filterAttackRuns(report.runs,view,options.categories);
  const heading=category==='all'?'전체 실행 로그':report.groups.find(g=>g.id===category)?.name||'실행 로그';
  const metrics=[['실행 카테고리',report.groups.length,'개'],['전체 테스트',c.total,'건'],['공격 성공',c.success,'건','success'],['공격 실패',c.failure,'건','failure'],['공격 성공률',rate(c.success,c.known),'','rate']];
  return `<div class="attack-report">${options.demo?'<div class="ar-demo"><strong>화면 확인용 예시 데이터</strong> · 실제 모델 호출·공격 실험 결과가 아니며 운영 DB에 저장되지 않아요.</div>':''}<div class="ar-stats">${metrics.map(([label,value,unit,kind])=>`<div class="ar-stat ${kind||''}"><span>${label}</span><strong>${value}<small>${unit}</small></strong></div>`).join('')}</div><div class="ar-status-line"><span>미확정 <b>${c.unknown}</b></span><span>미판정 <b>${c.unjudged}</b></span><span>실행 오류 <b>${c.error}</b></span><span>대기·실행 중 <b>${c.pending}</b></span><span>취소 <b>${c.cancelled}</b></span><span>자동 판정 ${c.automaticKnown}/${c.completed}건 (${rate(c.automaticKnown,c.completed)})</span></div><p class="ar-rate-note">공격 성공률 = 성공 ÷ (성공 + 실패). 확정 ${c.known}건 기준이며 미확정·미판정·오류는 분모에서 제외해요. 성공은 공격 목표 달성을 뜻해요.</p><section class="ar-categories"><div class="ar-section-heading"><div><span>RISK CATEGORIES</span><h3>카테고리별 공격 결과</h3></div><button type="button" class="ar-all-button" data-report-category="all" aria-pressed="${category==='all'}">전체 보기 · ${report.groups.length}개</button></div><div class="ar-table-wrap"><table><thead><tr><th>카테고리</th><th>시나리오 / 테스트</th><th>성공</th><th>실패</th><th>미확정·미판정</th><th>오류</th><th>공격 성공률</th></tr></thead><tbody>${report.groups.map(g=>`<tr class="${category===g.id?'selected':''}"><td><button type="button" data-report-category="${esc(g.id)}" aria-pressed="${category===g.id}">${esc(g.name)}<span aria-hidden="true">›</span></button>${g.names.size>1?'<small>실행 당시 분류 이름이 여러 개예요.</small>':''}</td><td>${g.scenarioCount} / ${g.counts.total}</td><td class="ar-success-text">${g.counts.success}</td><td class="ar-failure-text">${g.counts.failure}</td><td>${g.counts.unknown} · ${g.counts.unjudged}</td><td>${g.counts.error}</td><td><div class="ar-rate"><span class="ar-track"><i style="width:${g.counts.asr??0}%"></i></span><strong>${rate(g.counts.success,g.counts.known)}</strong></div><small>${g.counts.success}/${g.counts.known} 확정 · 진행/취소 ${g.counts.pending+g.counts.cancelled}</small></td></tr>`).join('')||'<tr><td colspan="7" class="ar-empty-small">아직 공격 실행 기록이 없어요.</td></tr>'}</tbody></table></div></section><section class="ar-logs"><div class="ar-section-heading"><div><span>TEST LOGS</span><h3>${esc(heading)} <small>${filtered.length}건 표시</small></h3></div><button type="button" data-report-expand class="ar-all-button">${view.openAll?'모두 접기':'모두 펼치기'}</button></div><div class="ar-log-tools"><div class="ar-filters" role="group" aria-label="공격 판정 상태 필터">${Object.entries(resultLabels).map(([key,label])=>`<button type="button" class="${status===key?'active':''}" aria-pressed="${status===key}" data-report-status="${key}">${label} <b>${key==='all'?sc.total:sc[key]}</b></button>`).join('')}</div><label class="ar-search"><span class="screen-reader">실행 로그 검색</span><input data-report-search type="search" placeholder="시나리오 · 프롬프트 · 응답 검색" value="${esc(view.query||'')}"></label></div><div class="ar-run-list">${filtered.map((r,i)=>renderAttackRun(r,i,{...options,openAll:view.openAll})).join('')||'<div class="ar-empty">조건에 맞는 실행이 없어요. 카테고리·상태·검색어를 확인해 주세요.</div>'}</div></section></div>`;
}
export function mountAttackReport(root,detail,options={}){
  const view={category:'all',status:'all',query:'',openAll:false,...options.initialView};
  const draw=()=>{
    root.innerHTML=renderAttackReport(detail,view,options);
    root.querySelectorAll?.('[data-attack-image]').forEach(img=>{
      const failed=()=>{img.hidden=true;const note=img.closest('figure')?.querySelector('.ar-image-error');if(note)note.hidden=false;};
      img.addEventListener('error',failed,{once:true});
      if(img.complete&&!img.naturalWidth)failed();
    });
    options.onRender?.(root);
  };
  root.onclick=event=>{
    const target=event.target.closest('button');if(!target||!root.contains(target))return;
    if(target.hasAttribute('data-report-category')){view.category=target.dataset.reportCategory;draw();root.querySelector('.ar-logs')?.scrollIntoView({block:'start',behavior:'smooth'});}
    else if(target.hasAttribute('data-report-status')){view.status=target.dataset.reportStatus;draw();root.querySelector(`[data-report-status="${view.status}"]`)?.focus();}
    else if(target.hasAttribute('data-report-expand')){view.openAll=!view.openAll;draw();root.querySelector('[data-report-expand]')?.focus();}
    else return;
    options.onViewChange?.({...view});
  };
  root.oninput=event=>{
    if(!event.target.matches('[data-report-search]'))return;
    view.query=event.target.value;const start=event.target.selectionStart,end=event.target.selectionEnd;
    draw();const input=root.querySelector('[data-report-search]');input?.focus();if(start!==null)input?.setSelectionRange(start,end);
    options.onViewChange?.({...view});
  };
  draw();return {getView:()=>({...view})};
}
