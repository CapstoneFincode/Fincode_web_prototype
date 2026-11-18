import {renderEmail, renderEvaluation} from './evaluation.js';
import {renderComparison} from './comparison.js';
import {renderHistory} from './history.js';
import {renderSavedComparison} from './saved-records.js';
import {renderDatasets} from './datasets.js';

const routes = {
  overview: ['대시보드','grid'], email: ['이메일 요약기','mail'],
  'single-evaluation': ['단일 모델 성능 평가','layers'], comparison: ['모델 별 비교','layers'],
  experiments: ['방어 기법 체험','flask'], datasets: ['정상 이메일','mail'], results: ['실험 기록','chart'],
  'normal-result-comparison': ['단일 모델 성능 평가 간 비교','chart'],
  'attack-result-comparison': ['공격 실험 결과 성능 비교','chart'],
};
const paths = {grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',mail:'M3 5h18v14H3z M3 6l9 7 9-7',layers:'m12 3 9 5-9 5-9-5 9-5z M3 12l9 5 9-5 M3 16l9 5 9-5',flask:'M9 3h6 M10 3v7L4 19q-1 2 2 2h12q3 0 2-2l-6-9V3 M8 15h8',chart:'M4 3v17h17 M8 15v-5 M13 15V6 M18 15v-8',arrow:'M5 12h14 M13 6l6 6-6 6',credit:'M3 6h18v13H3z M3 10h18 M7 15h3'};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]}"/></svg>`;
const link = route => `<a data-route="${route}" href="#${route}">${icon(routes[route][1])}${routes[route][0]}</a>`;
const group = (title,symbol,choices) => `<details class="release-nav-group"><summary>${icon(symbol)}${title}</summary><div class="evaluation-subnav">${choices.map(key=>`<a data-route="${key}" href="#${key}"><span class="subnav-dot" aria-hidden="true"></span>${routes[key][0]}</a>`).join('')}</div></details>`;
const $ = selector => document.querySelector(selector);
$('#nav').innerHTML = link('overview') + link('email') + group('성능 평가','layers',['single-evaluation','comparison']) + link('experiments') + group('데이터 관리','layers',['datasets']) + group('실험 결과','chart',['results','normal-result-comparison','attack-result-comparison']);

async function api(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(()=>controller.abort(), body ? 125000 : 35000);
  try {
    const response = await fetch(path,{method:body ? 'POST' : 'GET',cache:'no-store',signal:controller.signal,...(body ? {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {})});
    const data = await response.json().catch(()=>null);
    if (!response.ok) {
      const reason = typeof data?.detail==='string' ? data.detail : typeof data?.error==='string' ? data.error : data?.error?.message;
      throw new Error(response.status===429 ? '잠시 후 다시 실행해 주세요. 현재 공개 실험 호출 한도에 도달했어요.' : reason || '요청을 완료하지 못했어요. 잠시 후 다시 시도해 주세요.');
    }
    if (!data) throw new Error('응답을 읽지 못했어요. 잠시 후 다시 시도해 주세요.');
    return data;
  } catch(error) {
    if (controller.signal.aborted) throw new Error('응답 시간이 초과되었어요. 잠시 후 다시 시도해 주세요.');
    if (error instanceof TypeError) throw new Error('서비스에 연결할 수 없어요. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.');
    throw error;
  } finally { clearTimeout(timeout); }
}

function showError(parent,error,retry) {
  const box=document.createElement('div'); box.className='release-error'; box.setAttribute('role','alert');
  const message=document.createElement('p'); message.textContent=error.message || '데이터를 불러올 수 없어요.'; box.append(message);
  if (retry) { const button=document.createElement('button'); button.className='btn'; button.type='button'; button.textContent='다시 시도'; button.onclick=retry; box.append(button); }
  parent.replaceChildren(box);
}
const count = value => typeof value==='number' && Number.isFinite(value) ? value.toLocaleString('ko-KR',{maximumFractionDigits:2}) : '—';
const metric = (name,value,unit,detail,symbol) => `<div class="dash-metric"><div class="dash-metric-label">${name}<span>${icon(symbol)}</span></div><div class="dash-metric-value mono">${count(value)}<small>${unit}</small></div><p>${detail}</p></div>`;
const cards = [
  ['email','이메일 요약기','선택한 모델에 이메일을 보내 요약과 응답시간을 확인해요.','요약해 보기'],
  ['single-evaluation','성능 평가','검토된 정상 이메일의 기준 요약과 모델 응답을 비교해요.','평가하기'],
  ['experiments','방어 기법 체험','예시 메일에서 방어 설정별 입력 처리 방식을 살펴봐요.','모의 처리 보기'],
  ['results','실험 결과','저장된 실험을 모델별로 찾고 조건별 지표를 비교해요.','기록 보기'],
];
function dashboard() {
  const root=document.createElement('div'); root.className='live-dashboard';
  root.innerHTML=`<div class="page-head dash-page-head"><div><h1>대시보드</h1><p>이메일 요약을 실험하고, 방어 기법과 모델에 따른 결과를 비교하세요.</p></div><button class="btn" type="button" data-refresh>현황 새로고침</button></div>
    <div class="dash-intro-grid"><section class="dash-welcome release-welcome"><h2>어떤 실험을 시작해 볼까요?</h2><p>준비된 정상 이메일로 모델을 평가하거나 직접 작성한 이메일의 요약을 확인해 보세요.</p><div class="dash-welcome-actions release-actions"><a href="#email" class="btn primary">${icon('mail')}이메일 요약하기</a><a href="#comparison" class="btn">모델 비교하기${icon('arrow')}</a></div></section><section class="dash-credit" data-credit aria-live="polite"></section></div>
    <section class="dash-features" aria-labelledby="first-steps"><div class="dash-section-head"><div><h2 id="first-steps">처음이라면, 이렇게 시작하세요</h2></div></div><div class="dash-feature-grid">${cards.map(([key,title,desc,action])=>`<article class="dash-feature"><div class="dash-feature-top"><span class="dash-feature-icon">${icon(routes[key][1])}</span></div><h3>${title}</h3><p>${desc}</p><div class="dash-feature-links"><a href="#${key}">${action}${icon('arrow')}</a></div></article>`).join('')}</div></section>
    <section class="dash-data release-metrics"><div class="dash-data-heading"><h2>현재 연구 현황</h2></div><div data-stats aria-live="polite"></div></section>`;
  const stats=root.querySelector('[data-stats]'),credit=root.querySelector('[data-credit]'),button=root.querySelector('[data-refresh]');
  async function refresh(){
    button.disabled=true;stats.textContent='실험 기록을 확인하고 있어요…';credit.textContent='잔여 크레딧을 확인하고 있어요…';
    const [overview, balance]=await Promise.allSettled([api('/api/public/overview'),api('/api/public/credits')]);
    if(overview.status==='fulfilled'){
      const {datasets={},experiments={},performance={}}=overview.value;
      stats.innerHTML=`<div class="dash-metrics">${metric('공격 시나리오',datasets.attacks,'건',`직접 ${count(datasets.direct)} · 간접 ${count(datasets.indirect)}`,'flask')}${metric('정상 이메일',datasets.normal,'건',`기준 정답 준비 ${count(datasets.groundTruth?.ready)}건`,'mail')}${metric('실험 기록',experiments.total,'건',`실행 중 ${count(experiments.active)}건`,'chart')}${metric('성능 평가',performance.total,'건',`완료 ${count(performance.completed)}건`,'layers')}</div>`;
    } else showError(stats,overview.reason,refresh);
    const remaining=balance.status==='fulfilled' ? balance.value.remaining : null;
    credit.innerHTML=`<div class="dash-credit-head"><h2>${icon('credit')}잔여 크레딧</h2><span class="dash-source">BAZE</span></div><div class="dash-credit-value mono">${typeof remaining==='number'&&Number.isFinite(remaining)?remaining.toLocaleString('ko-KR',{maximumFractionDigits:6}):'—'}<span>크레딧</span></div><p class="dash-credit-state">${remaining===0?'사용 가능한 크레딧이 소진되었어요.':typeof remaining==='number'?'실험에 사용하는 BAZE 계정 잔액':'현재 잔액을 확인할 수 없어요.'}</p><p class="dash-credit-note">요약 길이와 선택한 모델에 따라 차감량이 달라져요.</p>`;
    button.disabled=false;
  }
  button.onclick=refresh; refresh(); return root;
}

function walkthrough(){
  const root=document.createElement('div');
  root.innerHTML=`<div class="page-head"><div><h1>방어 기법 체험</h1><p>정상 이메일과 가상의 연습 표시로 두 방어 기법의 입력 처리 과정을 확인해요.</p></div></div><section class="panel release-main-card"><h2>모의 입력 처리</h2><p>브라우저에서 설명용 예시를 처리해요. 실제 모델의 공격 성공률은 저장된 실험 결과에서 확인할 수 있어요.</p><div class="release-tools"><label class="field">방어 설정<select><option value="off">방어 미적용</option><option value="filter">입력 필터링</option><option value="separate">명령·데이터 분리</option><option value="both">두 기법 함께 적용</option></select></label></div><div class="release-note-grid"><div><h3>원본 이메일</h3><pre data-input></pre></div><div><h3>모의 처리 결과</h3><div aria-live="polite"><p data-description></p><pre data-output></pre></div></div></div><p class="release-source"><a class="btn" href="#attack-result-comparison">저장된 공격 실험 지표 보기</a></p></section>`;
  const select=root.querySelector('select');
  const source='〔연습 표시〕\n팀 회의는 수요일 오후 2시입니다. 회의실은 301호입니다.';
  root.querySelector('[data-input]').textContent=source;
  const descriptions={off:'예시 본문을 그대로 전달해요.',filter:'설명용 필터가 약속된 연습 표시를 제거해요.',separate:'요약 요청과 이메일 본문을 별도 영역에 배치해요.',both:'연습 표시를 제거한 뒤 요청과 본문을 분리해요.'};
  function update(){
    const mode=select.value, filtered=mode==='filter'||mode==='both', split=mode==='separate'||mode==='both';
    const body=filtered?source.split('\n').slice(1).join('\n'):source;
    root.querySelector('[data-description]').textContent=descriptions[mode];
    root.querySelector('[data-output]').textContent=split?`요청\n이메일 요약\n\n이메일 본문\n${body}`:body;
  }
  select.onchange=update;update();return root;
}

let catalogPromise, sequence=0;
const cached=new Map();
function catalog(){return catalogPromise??=api('/api/public/demo').catch(error=>{catalogPromise=null;throw error;});}
function route(){const key=location.hash.slice(1);if(key==='data')return 'datasets';return Object.hasOwn(routes,key)?key:'overview';}
function setMenu(open){$('#sidebar').classList.toggle('open',open);$('#menu').setAttribute('aria-expanded',String(open));$('#menu').setAttribute('aria-label',open?'메뉴 닫기':'메뉴 열기');}
async function navigate(){
  const key=route(),ticket=++sequence;setMenu(false);$('#route-name').textContent=routes[key][0];document.title=`${routes[key][0]} · 핀코드`;
  for(const item of document.querySelectorAll('[data-route]')){
    const active=item.dataset.route===key;item.classList.toggle('active',active);
    if(active){item.setAttribute('aria-current','page');const group=item.closest('details');if(group)group.open=true;}
    else item.removeAttribute('aria-current');
  }
  const view=$('#view');
  if(cached.has(key)){view.replaceChildren(cached.get(key));return;}
  const pending=document.createElement('p');pending.setAttribute('role','status');pending.textContent='데이터를 불러오고 있어요…';view.replaceChildren(pending);
  try{
    let page;
    if(key==='overview')page=dashboard();
    else if(key==='experiments')page=walkthrough();
    else if(key==='results'){
      page=renderHistory(null,api);
      const details=document.createElement('details');details.className='release-model-metrics';
      const summary=document.createElement('summary');summary.textContent='실험 종류별 모델 통계';details.append(summary);
      details.addEventListener('toggle',()=>{if(details.open&&details.children.length===1)details.append(renderComparison(null,api,'attack'));});
      page.append(details);
    }
    else if(key.endsWith('result-comparison'))page=renderSavedComparison(key==='normal-result-comparison'?'normal':'attack',api);
    else {const data=await catalog();if(ticket!==sequence)return;if(key==='datasets')page=renderDatasets(data);else if(key==='email')page=renderEmail(data,api);else page=renderEvaluation(data,api,{compare:key==='comparison'});}
    if(ticket!==sequence)return;cached.set(key,page);view.replaceChildren(page);
  }catch(error){if(ticket===sequence)showError(view,error,navigate);}
}
$('#menu').onclick=()=>setMenu(!$('#sidebar').classList.contains('open'));$('#shade').onclick=()=>setMenu(false);
$('.skip').onclick=event=>{event.preventDefault();$('#main').focus();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&$('#sidebar').classList.contains('open')){setMenu(false);$('#menu').focus();}});
window.addEventListener('hashchange',()=>{navigate();window.scrollTo(0,0);$('#main').focus({preventScroll:true});});
navigate();
