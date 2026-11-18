// Read-only dashboard markup. Data fetching and route handling remain in app.js.
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const number = (value, digits = 0) => finite(value) ? value.toLocaleString('ko-KR', {maximumFractionDigits:digits}) : '—';
const stamp = value => {
  if (!value) return '확인 전';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('ko-KR', {timeZone:'Asia/Seoul', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}).format(date) : '확인 전';
};
const paths = {
  arrow:'M5 12h14 M13 6l6 6-6 6',
  refresh:'M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-1l2 2 M4 16l2 2a7 7 0 0 0 12-1',
  mail:'M3 5h18v14H3z M3 6l9 7 9-7',
  data:'M20 6c0 2-3.6 3-8 3S4 8 4 6s3.6-3 8-3 8 1 8 3z M4 6v12c0 2 3.6 3 8 3s8-1 8-3V6 M4 12c0 2 3.6 3 8 3s8-1 8-3',
  flask:'M9 3h6 M10 3v7L4 19q-1 2 2 2h12q3 0 2-2l-6-9V3 M8 15h8',
  chart:'M4 3v17h17 M8 15v-5 M13 15V6 M18 15v-8',
  credit:'M3 6h18v13H3z M3 10h18 M7 15h3',
  models:'m12 3 9 5-9 5-9-5 9-5z M3 12l9 5 9-5 M3 16l9 5 9-5',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.arrow}"/></svg>`;
const statusNames = {queued:'대기',running:'실행 중',completed:'완료',partial:'일부 완료',failed:'실패',cancelled:'중단'};
const purposeNames = {attack:'공격 실험',normal:'정상 응답 확인',performance:'성능 평가'};
const defenseNames = {off:'방어 OFF',filter:'입력 필터링',separate:'명령·데이터 분리',both:'두 기법 조합',mixed:'방어 조건 혼합',unknown:'방어 조건 확인 필요'};
const status = value => `<span class="dash-status ${['queued','running'].includes(value) ? 'is-active' : value === 'completed' ? 'is-complete' : ['failed','partial'].includes(value) ? 'is-warning' : ''}">${escape(statusNames[value] || '상태 확인 필요')}</span>`;
const empty = (heading, text) => `<div class="dash-empty"><strong>${escape(heading)}</strong><p>${escape(text)}</p></div>`;
const title = (heading, description, link = '') => `<div class="dash-section-head"><div><h2>${heading}</h2><p>${description}</p></div>${link}</div>`;

function creditPanel(credits, loading, error) {
  const available = !error && credits?.available === true && finite(credits.balance?.remaining);
  const balance = available ? credits.balance : {};
  const exhausted = available && balance.remaining <= 0;
  const reason = error || credits?.error?.message || 'BAZE 잔액 정보를 아직 확인하지 못했어요.';
  const usage = finite(balance.quota) && balance.quota > 0 && finite(balance.used) && balance.used >= 0
    ? `<progress class="dash-credit-progress" max="${balance.quota}" value="${Math.min(balance.used,balance.quota)}" aria-label="전체 할당 크레딧 중 사용량"></progress>` : '';
  return `<section class="dash-credit" aria-labelledby="dash-credit-title" aria-busy="${loading}">
    <div class="dash-credit-head"><h2 id="dash-credit-title">${icon('credit')}잔여 크레딧</h2><span class="dash-source">BAZE</span></div>
    <div class="dash-credit-value mono">${available ? number(balance.remaining,8) : '—'}<span>크레딧</span></div>
    <p class="dash-credit-state ${exhausted ? 'is-exhausted' : ''}">${loading ? '잔액을 확인하고 있어요…' : available ? exhausted ? '잔액이 소진되었어요. 계정 잔액을 확인해 주세요.' : '연결된 API 키 계정의 잔액' : '잔액 확인 불가'}</p>
    ${usage}
    <dl class="dash-credit-breakdown"><div><dt>사용 크레딧</dt><dd class="mono">${number(balance.used,8)}</dd></div><div><dt>전체 할당 크레딧</dt><dd class="mono">${number(balance.quota,8)}</dd></div></dl>
    ${!available && !loading ? `<p class="dash-credit-error" role="status">${escape(reason)}</p>` : ''}
    ${available && credits.renewalDate ? `<p class="dash-credit-renewal">월간 갱신 예정 · ${escape(stamp(credits.renewalDate))} (한국시간)</p>` : ''}
    <p class="dash-credit-note">토큰과 다른 과금 단위예요. 모델과 입출력 길이에 따라 실험별 차감량이 달라져요.</p>
    <div class="dash-credit-footer"><span>${credits?.checkedAt ? `잔액 확인 · ${escape(stamp(credits.checkedAt))}` : '잔액 조회 대기'}${credits?.cached ? ' · 최근 조회값' : ''}</span><span>잔액 정보는 1분간 재사용</span></div>
  </section>`;
}

function metric(label, value, unit, detail, iconName, tone = '') {
  return `<div class="dash-metric ${tone}"><div class="dash-metric-label">${label}<span>${icon(iconName)}</span></div><div class="dash-metric-value mono">${value}<small>${unit}</small></div><p>${detail}</p></div>`;
}

function features() {
  const cards = [
    ['01','mail','이메일 요약기','이메일 본문과 첨부파일을 선택한 모델로 요약해요. 결과는 이 화면에서 바로 확인해요.','email','요약해 보기'],
    ['02','data','데이터 준비','정상 이메일과 공격 시나리오를 확인하고 실험에 사용할 데이터를 준비해요.','data','데이터 살펴보기'],
    ['03','flask','공격·방어 실험','직접·간접 공격과 방어 조건을 선택해 실험하고 공격 성공 여부를 확인해요.','experiments','실험 구성하기'],
    ['04','chart','모델 성능 평가','기준 정답으로 요약 품질을 자동 채점해요. 단일 모델 평가와 최대 5개 모델 비교를 지원해요.','single-evaluation','단일 모델 평가'],
  ];
  return `<section class="dash-features" aria-labelledby="dash-features-title">${title('<span id="dash-features-title">처음이라면, 이렇게 시작하세요</span>','사용할 기능을 골라 바로 시작할 수 있어요.')}<div class="dash-feature-grid">${cards.map(([step,symbol,label,description,route,action])=>`<article class="dash-feature"><div class="dash-feature-top"><span class="dash-feature-icon">${icon(symbol)}</span><span class="dash-step">${step}</span></div><h3>${label}</h3><p>${description}</p><div class="dash-feature-links"><a href="#${route}">${action}${icon('arrow')}</a>${route === 'single-evaluation' ? '<a href="#comparison">모델 별 비교</a>' : ''}</div></article>`).join('')}</div></section>`;
}

function recentExperiments(data, loading, error) {
  const rows = Array.isArray(data?.experiments?.recent) ? data.experiments.recent.slice(0,5) : [];
  let content;
  if (loading && !data) content = empty('실험 기록을 불러오고 있어요','현재 DB에 저장된 최근 기록을 확인하고 있어요.');
  else if (error || !data) content = empty('실험 기록을 표시할 수 없어요','새로고침 후 다시 확인해 주세요.');
  else if (!rows.length) content = empty('아직 저장된 실험이 없어요','공격 실험이나 성능 평가를 실행하면 여기에 표시돼요.');
  else content = `<div class="dash-table-scroll"><table class="dash-recent-table"><caption class="dash-sr-only">DB에 저장된 최근 실험 최대 5건</caption><thead><tr><th scope="col">실험</th><th scope="col">상태</th><th scope="col">생성일 · 한국시간</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${escape(row.name || '이름 없는 실험')}</strong><div class="dash-row-meta"><span>${escape(purposeNames[row.purpose] || '실험')}</span><span>${escape(defenseNames[row.defense] || defenseNames.unknown)}</span></div><p class="dash-row-model">${escape(Array.isArray(row.models) ? row.models.join(' · ') : '')}</p></td><td>${status(row.status)}</td><td class="dash-date mono">${escape(stamp(row.createdAt))}</td></tr>`).join('')}</tbody></table></div>`;
  return `<section class="dash-panel">${title('최근 실험','웹에서 진행한 실험 중 최근 5건',`<a class="dash-text-link" href="#results">전체 결과${icon('arrow')}</a>`)}${content}</section>`;
}

function recentPerformance(data, loading, error) {
  const performance = data?.performance;
  const rows = Array.isArray(performance?.recent) ? performance.recent.slice(0,5) : [];
  let content;
  if (loading && !data) content = empty('성능 평가를 확인하고 있어요','평가 기록과 모델 정보를 불러오고 있어요.');
  else if (error || !data) content = empty('성능 평가 현황을 표시할 수 없어요','연결 상태를 확인한 뒤 새로고침해 주세요.');
  else if (!rows.length) content = empty('첫 성능 평가를 시작해 보세요','검토가 완료된 정상 이메일로 요약 품질을 평가할 수 있어요.');
  else content = `<ol class="dash-evaluation-list">${rows.map(row=>`<li><div class="dash-evaluation-title"><strong>${escape(row.name || '성능 평가')}</strong>${status(row.status)}</div><p class="dash-evaluation-model">${escape(Array.isArray(row.models) ? row.models.join(' · ') : '')}</p><div class="dash-evaluation-meta"><span>${escape(defenseNames[row.defense] || defenseNames.unknown)}</span><span>${escape(stamp(row.createdAt))}</span></div></li>`).join('')}</ol>`;
  return `<section class="dash-panel dash-performance">${title('성능 평가 현황','최근 평가의 모델과 방어 조건을 확인하세요.')}<div class="dash-performance-totals"><div><span>등록된 평가</span><strong class="mono">${number(performance?.total)}<small>건</small></strong></div><div><span>완료된 평가</span><strong class="mono">${number(performance?.completed)}<small>건</small></strong></div></div>${content}<div class="dash-panel-foot"><p>품질·응답시간·토큰 지표는 실험 결과에서 조건별로 확인할 수 있어요.</p><a class="dash-text-link" href="#results">모델 지표 확인${icon('arrow')}</a></div></section>`;
}

export function renderDashboard({data = null, loading = false, error = '', credits = null, creditsLoading = false, creditsError = ''} = {}) {
  const current = error ? null : data;
  const dataset = current?.datasets;
  const groundTruth = dataset?.groundTruth;
  const experiments = current?.experiments;
  const busy = loading || creditsLoading;
  return `<div class="live-dashboard">
    <div class="page-head dash-page-head"><div><div class="eyebrow">RESEARCH WORKSPACE</div><h1>실험의 시작부터, 결과까지.</h1><p>데이터를 준비하고, 공격·방어 효과와 모델의 요약 성능을 확인하세요.</p></div><button type="button" class="btn" data-action="dashboard-refresh" ${busy ? 'disabled' : ''}>${icon('refresh')}${busy ? '확인 중…' : '현황 새로고침'}</button></div>
    <div class="dash-intro-grid"><section class="dash-welcome"><span class="dash-kicker">FINCODE SECURITY LAB</span><h2>어떤 실험을<br>시작해 볼까요?</h2><p>이메일 요약부터 프롬프트 인젝션 공격 실험,<br class="dash-desktop-break">정상 데이터 기반 성능 평가까지 한곳에서 진행해요.</p><div class="dash-welcome-actions"><a href="#experiments" class="btn primary">${icon('flask')}공격 실험 시작</a><a href="#comparison" class="btn">모델 비교하기${icon('arrow')}</a></div><div class="dash-welcome-note"><span class="dash-live-dot" aria-hidden="true"></span>아래 현황은 현재 DB에 저장된 기록을 기준으로 표시해요.</div></section>${creditPanel(credits,creditsLoading,creditsError)}</div>
    ${features()}
    <section class="dash-data" aria-label="현재 데이터와 실험 현황" aria-busy="${loading}"><div class="dash-data-heading"><h2>현재 연구 현황</h2><span role="status">${loading ? '데이터 확인 중…' : current ? `DB 조회 · ${escape(stamp(current.generatedAt))} (한국시간)` : 'DB 조회 전'}</span></div>
    ${error ? `<div class="dash-error" role="alert"><strong>현재 현황을 불러오지 못했어요.</strong><p>${escape(error)}</p></div>` : ''}
    <div class="dash-metrics">${metric('공격 시나리오',number(dataset?.attacks),'건',`직접 ${number(dataset?.direct)} · 간접 ${number(dataset?.indirect)}`,'flask')}${metric('검토 완료 평가 이메일',number(groundTruth?.ready),`/ ${number(groundTruth?.total)}건`,'정상 라벨·기준 정답 검토 완료','data','mint')}${metric('저장된 실험',number(experiments?.total),'건',`현재 대기·실행 중 ${number(experiments?.active)}건`,'chart')}${metric('평가에 등록된 모델',number(current?.performance?.modelCount),'종','성능 평가 기록 기준 · 대기 포함','models','violet')}</div>
    <div class="dash-dataset-note"><span>전체 정상 이메일 <b>${number(dataset?.normal)}건</b></span><span>공격 카테고리 <b>${number(dataset?.categories)}개</b></span><a href="#data">데이터 관리${icon('arrow')}</a></div>${groundTruth?.error ? `<p class="dash-groundtruth-error" role="status">평가 데이터 확인 필요 · ${escape(groundTruth.error)}</p>` : ''}</section>
    <div class="dash-records-grid">${recentExperiments(current,loading,error)}${recentPerformance(current,loading,error)}</div>
  </div>`;
}
