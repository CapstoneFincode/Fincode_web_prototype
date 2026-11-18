const purposes = {attack: '공격 실험', normal: '정상 응답 확인', performance: '성능 평가'};
const defenses = {off: '방어 미적용', filter: '입력 필터링', separate: '명령·데이터 분리', both: '두 방어 기법 적용'};
const vectors = {all: '전체 경로', direct: '직접', indirect: '간접'};
const format = new Intl.NumberFormat('ko-KR', {maximumFractionDigits: 1});

function el(tag, value, classes) {
  const node = document.createElement(tag);
  if (value != null) node.textContent = value;
  if (classes) node.className = classes;
  return node;
}

function number(value, suffix = '') {
  return Number.isFinite(value) ? format.format(value) + suffix : '—';
}

function selectField(label, values) {
  const field = el('label', null, 'saved-field');
  const labelText = el('span', label);
  const select = el('select');
  for (const [value, caption] of Object.entries(values)) select.add(new Option(caption, value));
  field.append(labelText, select);
  return {field, select};
}

function rateOf(row, purpose) {
  if (purpose === 'attack') return row?.attack?.asr;
  if (purpose === 'normal') return row?.normal?.fpr;
  const score = row?.quality?.meanRougeL;
  return Number.isFinite(score) ? score * 100 : null;
}

function makeTable(headers, rows) {
  const wrap = el('div', null, 'table-wrap saved-table-wrap');
  const table = el('table');
  const head = table.createTHead().insertRow();
  for (const name of headers) {
    const th = el('th', name);
    th.scope = 'col';
    head.append(th);
  }
  const body = table.createTBody();
  for (const [name, ...values] of rows) {
    const tr = body.insertRow();
    const th = el('th', name);
    th.scope = 'row';
    tr.append(th);
    for (const value of values) tr.append(el('td', value));
  }
  wrap.append(table);
  return wrap;
}

export function renderComparison(catalog, api, initialPurpose = 'attack') {
  const root = el('div', null, 'saved-comparison');
  const header = el('div', null, 'page-head');
  const introduction = el('div');
  introduction.append(el('h1', '실험 결과 비교'), el('p', '저장된 실험에서 모델과 방어 조건을 선택해 지표를 비교하세요.'));
  const refresh = el('button', '기록 새로고침', 'btn');
  refresh.type = 'button';
  header.append(introduction, refresh);
  const form = el('section', null, 'panel saved-controls');
  const purposeField = selectField('실험 종류', purposes);
  const cards = el('div', null, 'saved-cards');
  const add = el('button', '+ 비교 조건 추가', 'btn');
  add.type = 'button';
  const output = el('section', null, 'panel saved-output');
  const outputTitle = el('h2', '측정 지표');
  const contents = el('div');
  contents.setAttribute('aria-live', 'polite');
  output.append(outputTitle, contents);
  form.append(purposeField.field, cards, add);
  root.append(header, form, output);

  let purpose = Object.hasOwn(purposes, initialPurpose) ? initialPurpose : 'attack';
  purposeField.select.value = purpose;
  const conditions = [];
  const reports = new Map();

  function renderOutput() {
    contents.replaceChildren();
    refresh.disabled = conditions.some(c => c.busy);
    const rows = conditions.map(c => c.rows.find(row => row.model === c.model.value));
    const metricTitle = {attack: '공격 성공률', normal: '정상 차단·거부율', performance: '평균 요약 유사도 (ROUGE-L)'}[purpose];
    const help = {
      attack: '공격 성공률은 성공과 실패로 판정된 실행 중 성공 비율이에요.',
      normal: '정상 차단·거부율은 판정된 정상 응답 중 차단 또는 거부된 비율이에요.',
      performance: '요약 유사도는 응답과 기준 요약의 ROUGE-L 평균이에요. 각 조건에 저장된 이메일 구성과 채점 건수가 다를 수 있어요.'
    }[purpose];
    contents.append(el('p', help, 'saved-note'));
    if (!rows.some(Boolean)) {
      const loading = conditions.some(c => c.busy);
      const error = conditions.some(c => c.failed);
      contents.append(el('p', loading ? '실험 결과를 불러오는 중이에요.' : error ? '조회에 실패했어요. 비교 조건에서 다시 시도해 주세요.' : '선택한 조건으로 저장된 결과가 없어요.', 'saved-empty'));
      return;
    }
    const chart = el('div', null, 'saved-chart');
    conditions.forEach((condition, index) => {
      const row = rows[index];
      const line = el('div', null, 'saved-series');
      const title = '조건 ' + (index + 1);
      const rate = rateOf(row, purpose);
      line.append(el('div', title + (row ? ' · ' + row.model : ''), 'saved-series-name'));
      const metric = el('div', null, 'saved-meter-row');
      if (Number.isFinite(rate)) {
        const meter = el('meter');
        meter.min = 0;
        meter.max = 100;
        meter.value = rate;
        meter.setAttribute('aria-label', title + ' ' + metricTitle);
        meter.setAttribute('aria-valuetext', number(rate, '%'));
        metric.append(meter);
      }
      metric.append(el('strong', condition.busy ? '조회 중' : condition.failed ? '조회 실패' : !row ? '기록 없음' : number(rate, '%')));
      line.append(metric);
      chart.append(line);
    });
    contents.append(chart);
    const headers = ['지표', ...conditions.map((c, i) => '조건 ' + (i + 1) + (c.model.value ? ' · ' + c.model.value : ''))];
    const metrics = [['전체 실행', row => number(row.total, '건')], [metricTitle, row => number(rateOf(row, purpose), '%')]];
    if (purpose === 'attack') {
      metrics.push(['공격 성공 / 판정', row => number(row.attack?.successes) + ' / ' + number(row.attack?.judged)]);
    } else if (purpose === 'normal') {
      metrics.push(['정상 차단·거부 / 판정', row => number(row.normal?.rejected) + ' / ' + number(row.normal?.judged)]);
    } else {
      metrics.push(['자동 채점', row => number(row.quality?.gradedCount, '건')]);
    }
    metrics.push(['평균 응답시간', row => number(row.latencyMsMean, ' ms')], ['평균 사용 토큰', row => number(row.tokens?.totalMean)]);
    contents.append(makeTable(headers, metrics.map(([name, get]) => [name, ...rows.map(row => row ? get(row) : '—')])));
  }

  function updateCards() {
    conditions.forEach((c, i) => {
      c.legend.textContent = '비교 조건 ' + (i + 1);
      c.remove.hidden = conditions.length <= 2;
      c.remove.setAttribute('aria-label', c.legend.textContent + ' 삭제');
    });
    add.disabled = conditions.length >= 5;
  }

  async function load(c, retry = false) {
    const sequence = ++c.sequence;
    const vector = purpose === 'attack' ? c.vector.value : 'all';
    const query = new URLSearchParams({purpose, defense: c.defense.value, vector});
    const path = '/api/public/comparison?' + query;
    if (retry) reports.delete(path);
    const lastModel = c.model.value || c.lastModel;
    c.busy = true;
    c.failed = false;
    c.rows = [];
    c.retry.hidden = true;
    c.model.disabled = true;
    c.model.replaceChildren(new Option('조회 중…', ''));
    c.status.textContent = '조건에 맞는 기록을 조회하고 있어요.';
    renderOutput();
    try {
      if (!reports.has(path)) reports.set(path, Promise.resolve().then(() => api(path)));
      const report = await reports.get(path);
      if (sequence !== c.sequence) return;
      if (!Array.isArray(report?.models)) throw new Error();
      c.rows = report.models.filter(row => typeof row?.model === 'string' && row.model.length);
      c.model.replaceChildren(...c.rows.map(row => new Option(row.model, row.model)));
      if (c.rows.length) {
        if (c.rows.some(row => row.model === lastModel)) c.model.value = lastModel;
        c.lastModel = c.model.value;
        c.model.disabled = false;
        c.status.textContent = '';
      } else {
        c.model.add(new Option('기록 없음', ''));
        c.status.textContent = '이 조건으로 저장된 모델이 없어요.';
      }
    } catch {
      if (sequence !== c.sequence) return;
      reports.delete(path);
      c.failed = true;
      c.model.replaceChildren(new Option('조회 실패', ''));
      c.status.textContent = '기록을 불러오지 못했어요.';
      c.retry.hidden = false;
    } finally {
      if (sequence === c.sequence) {
        c.busy = false;
        renderOutput();
      }
    }
  }

  function addCondition(initialDefense) {
    const group = el('fieldset', null, 'saved-card');
    const legend = el('legend');
    const defense = selectField('방어 기법', defenses);
    defense.select.value = initialDefense;
    const vector = selectField('공격 경로', vectors);
    vector.field.hidden = purpose !== 'attack';
    const model = selectField('기록된 모델', {});
    const status = el('p', '', 'saved-status');
    status.setAttribute('role', 'status');
    const retry = el('button', '다시 조회', 'btn');
    retry.type = 'button';
    const remove = el('button', '조건 삭제', 'btn');
    remove.type = 'button';
    const c = {legend, group, defense: defense.select, vector: vector.select, vectorField: vector.field,
      model: model.select, rows: [], status, retry, remove, sequence: 0, lastModel: '', busy: false, failed: false};
    conditions.push(c);
    group.append(legend, model.field, defense.field, vector.field, status, retry, remove);
    cards.append(group);
    c.defense.addEventListener('change', () => load(c));
    c.vector.addEventListener('change', () => load(c));
    c.model.addEventListener('change', () => { c.lastModel = c.model.value; renderOutput(); });
    retry.addEventListener('click', () => load(c, true));
    remove.addEventListener('click', () => {
      c.sequence++;
      conditions.splice(conditions.indexOf(c), 1);
      group.remove();
      updateCards();
      renderOutput();
      add.focus();
    });
    updateCards();
    load(c);
  }
  refresh.addEventListener('click', () => {
    reports.clear();
    for (const c of conditions) load(c);
  });
  purposeField.select.addEventListener('change', () => {
    purpose = purposeField.select.value;
    // Reset all rows before rendering a report of a new kind.
    for (const c of conditions) { c.rows = []; c.vectorField.hidden = purpose !== 'attack'; }
    for (const c of conditions) load(c);
  });
  add.addEventListener('click', () => {
    if (conditions.length < 5) addCondition('off');
  });
  addCondition('off');
  addCondition('both');
  return root;
}
