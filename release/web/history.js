const purposeLabels = {attack:'공격 실험', normal:'정상 응답 확인', performance:'성능 평가'};
const statusLabels = {draft:'준비', queued:'대기', running:'진행 중', completed:'완료', partial:'부분 완료', failed:'실패', cancelled:'취소'};
const localTime = new Intl.DateTimeFormat('ko-KR', {
  timeZone:'Asia/Seoul', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit',
});

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

export function renderHistory(catalog, api) {
  const root = element('div');
  root.innerHTML = `<header class="page-head"><div><h1>실험 기록</h1></div></header>
    <section class="panel release-main-card"><h2>웹에서 진행한 실험</h2>
    <div class="release-tools"><div class="field"><label for="history-model">실험 모델</label>
    <select id="history-model"><option value="">전체 모델</option></select></div></div>
    <div class="release-history-body" aria-live="polite"></div></section>`;
  const select = root.querySelector('select');
  const body = root.querySelector('.release-history-body');
  let sequence = 0;

  async function load(page) {
    const version = ++sequence;
    const selected = select.value;
    const params = new URLSearchParams({page:String(page)});
    if (selected) params.set('model', selected);
    body.setAttribute('aria-busy','true');
    body.replaceChildren(element('p','실험 기록을 불러오는 중이에요.'));
    try {
      const data = await api('/api/public/history?' + params.toString());
      if (version !== sequence) return;
      select.replaceChildren(new Option('전체 모델',''));
      for (const model of data.models) select.add(new Option(model, model));
      select.value = selected;
      body.replaceChildren();
      if (!data.rows.length) {
        body.append(element('p','선택한 조건에 해당하는 실험 기록이 없어요.'));
        return;
      }
      const wrap = element('div', undefined, 'table-wrap');
      const table = element('table');
      table.setAttribute('aria-label', '웹에서 진행한 실험 기록');
      const header = table.createTHead().insertRow();
      for (const name of ['실행 일시 (한국 시간)', '실험 종류', '참여 모델', '방어 적용', '실행 상태', '처리 건수']) {
        const column = element('th',name);
        column.scope = 'col';
        header.append(column);
      }
      const tbody = table.createTBody();
      for (const record of data.rows) {
        const row = tbody.insertRow();
        const date = record.createdAt ? new Date(record.createdAt) : null;
        const values = [
          date && Number.isFinite(date.getTime()) ? localTime.format(date) : '기록 없음',
          purposeLabels[record.purpose] || '기타', record.models.join(', ') || '기록 없음',
          record.defense === null ? '기록 없음' : record.defense ? '적용' : '미적용',
          statusLabels[record.status] || '기록 없음', record.finished + ' / ' + record.total,
        ];
        for (const value of values) row.insertCell().textContent = value;
      }
      wrap.append(table);
      const pager = element('div', undefined, 'release-pager');
      const previous = element('button','이전','btn');
      const next = element('button','다음','btn');
      previous.type = next.type = 'button';
      previous.disabled = data.page <= 1;
      next.disabled = data.page >= data.totalPages;
      previous.onclick = () => load(data.page - 1);
      next.onclick = () => load(data.page + 1);
      const first = (data.page - 1) * 5 + 1;
      const last = first + data.rows.length - 1;
      pager.append(previous, element('span', first + '–' + last + ' / ' + data.total + '건'), next);
      body.append(wrap, pager);
    } catch (error) {
      if (version !== sequence) return;
      const box = element('div', undefined, 'release-error');
      box.setAttribute('role','alert');
      const retry = element('button','다시 시도','btn');
      retry.type = 'button';
      retry.onclick = () => load(page);
      box.append(element('p',error.message || '실험 기록을 불러오지 못했어요.'),retry);
      body.replaceChildren(box);
    } finally {
      if (version === sequence) body.removeAttribute('aria-busy');
    }
  }

  select.addEventListener('change',() => load(1));
  load(1);
  return root;
}
