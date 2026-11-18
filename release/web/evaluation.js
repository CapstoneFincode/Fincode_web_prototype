const DEFENSE_NAMES = {
  off: '방어 미적용',
  filter: '입력 필터링',
  separate: '명령·데이터 분리',
  both: '두 기법 적용',
};
const formatNumber = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 1 });
const formatPercent = new Intl.NumberFormat('ko-KR', { style: 'percent', maximumFractionDigits: 1 });

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function addOptions(select, values) {
  for (const [value, label] of values) select.add(new Option(label, value));
}

function page(title, description, formTitle, resultsTitle) {
  const root = element('div', 'public-evaluation');
  root.innerHTML = `<div class="page-head"><div><h1></h1><p></p></div></div>
    <div class="public-eval-grid">
      <section class="panel public-eval-panel"><h2></h2><form><fieldset></fieldset></form></section>
      <section class="panel public-eval-panel"><h2></h2><p class="public-eval-status" role="status"></p><div class="public-eval-output"></div></section>
    </div>`;
  root.querySelector('h1').textContent = title;
  root.querySelector('.page-head p').textContent = description;
  const [inputHeading, outputHeading] = root.querySelectorAll('h2');
  inputHeading.textContent = formTitle;
  outputHeading.textContent = resultsTitle;
  return root;
}

function addField(parent, labelText, input) {
  const label = element('label', 'field public-eval-field');
  label.append(element('span', 'field-label', labelText), input);
  parent.append(label);
  return input;
}

function submitButton(parent, label) {
  const button = element('button', 'btn primary public-eval-run', label);
  button.type = 'submit';
  parent.append(button);
  return button;
}

function updateStatus(node, text, isError = false) {
  node.textContent = text;
  node.classList.toggle('public-eval-error', isError);
}

function metric(label, renderedValue) {
  const item = element('div');
  item.append(element('dt', '', label), element('dd', 'mono', renderedValue));
  return item;
}

function resultCard(result, model, defense, scored) {
  const card = element('article', 'public-eval-result');
  const title = element('h3', '', model);
  card.append(title, element('div', 'public-eval-context', DEFENSE_NAMES[defense]));
  const summary = element('p', 'public-eval-text', result.summary || '요약문이 비어 있어요.');
  const metrics = element('dl', 'public-eval-metrics');
  const percent = value => Number.isFinite(value) ? formatPercent.format(value) : '—';
  if (scored) metrics.append(metric('기준 요약 유사도', percent(result.quality?.referenceSimilarity)),
    metric('핵심 정보 자동 확인률', percent(result.quality?.confirmedFactRate)));
  const latency = result.latencyMs;
  const tokens = result.usage?.totalTokens;
  metrics.append(metric('응답 시간', Number.isFinite(latency) ? formatNumber.format(latency / 1000) + '초' : '—'),
    metric('사용 토큰', Number.isFinite(tokens) ? formatNumber.format(tokens) : '—'));
  card.append(summary, metrics);
  return card;
}

export function renderEmail(catalog, api) {
  const models = catalog?.models || [];
  const emails = catalog?.emails || [];
  const root = page('이메일 요약기', '직접 입력한 이메일이나 합성 이메일 예시를 선택한 모델로 요약해요.',
    '이메일 입력', '요약 결과');
  const form = root.querySelector('form');
  const fields = form.querySelector('fieldset');
  const status = root.querySelector('[role=status]');
  const output = root.querySelector('.public-eval-output');
  const model = addField(fields, '요약 모델', element('select'));
  addOptions(model, models.map(name => [name, name]));
  const example = addField(fields, '합성 이메일 예시', element('select'));
  addOptions(example, [['', '직접 입력'], ...emails.map(email => [email.id, email.title])]);
  const subject = addField(fields, '이메일 제목', element('input'));
  subject.maxLength = 200;
  subject.placeholder = '예: 다음 주 회의 일정';
  const body = addField(fields, '이메일 본문', element('textarea'));
  body.maxLength = 5000;
  body.required = true;
  body.rows = 9;
  body.placeholder = '요약할 이메일 본문을 입력하세요.';
  const submit = submitButton(fields, '요약 실행');
  submit.disabled = !models.length;
  updateStatus(status, models.length ? '요약을 실행하면 이곳에 결과가 표시돼요.' : '현재 사용할 수 있는 모델이 없어요.');
  example.addEventListener('change', () => {
    const selected = emails.find(email => email.id === example.value);
    subject.value = selected?.title || '';
    body.value = selected?.body || '';
  });
  for (const input of [subject, body]) input.addEventListener('input', () => { example.value = ''; });
  const clearSummary = () => {
    if (!output.childElementCount) return;
    output.replaceChildren();
    updateStatus(status, '변경한 조건으로 요약을 실행해 주세요.');
  };
  form.addEventListener('input', clearSummary);
  form.addEventListener('change', clearSummary);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (fields.disabled || !model.value) return;
    if (!body.value.trim()) {
      body.focus();
      updateStatus(status, '이메일 본문을 입력해 주세요.', true);
      return;
    }
    fields.disabled = true;
    output.replaceChildren();
    updateStatus(status, '모델이 이메일을 요약하고 있어요…');
    try {
      const response = await api('/api/public/summarize', {model: model.value, email: {subject: subject.value.trim(), body: body.value.trim()}});
      output.append(resultCard(response, model.value, 'both', false));
      updateStatus(status, '요약을 완료했어요.');
    } catch (error) {
      updateStatus(status, error.message || '요약 요청을 완료할 수 없어요.', true);
    } finally {
      fields.disabled = false;
    }
  });
  return root;
}

export function renderEvaluation(catalog, api, {compare = false} = {}) {
  const emails = catalog?.emails || [];
  const models = catalog?.models || [];
  const defenses = (catalog?.defenseModes || []).filter(mode => Object.hasOwn(DEFENSE_NAMES, mode));
  const root = page(compare ? '모델 별 비교' : '단일 모델 성능 평가',
    '검토를 마친 합성 이메일 한 건을 선택해 모델의 요약과 기준 정답을 비교해요.',
    '평가 조건', '평가 결과');
  const form = root.querySelector('form');
  const fields = form.querySelector('fieldset');
  const status = root.querySelector('[role=status]');
  const output = root.querySelector('.public-eval-output');
  const sample = addField(fields, '평가 이메일', element('select'));
  addOptions(sample, emails.map(email => [email.id, email.title]));
  const preview = element('details', 'public-eval-preview');
  preview.append(element('summary', '', '이메일 원문과 기준 정답 보기'));
  const emailBody = element('p', 'public-eval-text');
  const answer = element('p', 'public-eval-text');
  preview.append(element('h3', '', '이메일 원문'), emailBody, element('h3', '', '기준 정답'), answer);
  fields.append(preview);
  const showEmail = () => {
    const selected = emails.find(email => email.id === sample.value);
    emailBody.textContent = selected?.body || '';
    answer.textContent = selected?.reference || '';
  };
  sample.addEventListener('change', showEmail);
  showEmail();

  const list = element('div', 'public-eval-models');
  const add = element('button', 'btn', '+ 모델 추가');
  add.type = 'button';
  const slots = [];
  function clearEvaluation() {
    if (!output.childElementCount) return;
    output.replaceChildren();
    updateStatus(status, '변경한 조건으로 평가를 실행해 주세요.');
  }
  form.addEventListener('change', clearEvaluation);
  function renderSlots() {
    list.replaceChildren();
    for (const [index, slot] of slots.entries()) {
      const row = element('div', 'public-eval-slot');
      const model = addField(row, compare ? `모델 ${index + 1}` : '모델', element('select'));
      addOptions(model, models.map(name => [name, name]));
      model.value = slot.model;
      model.addEventListener('change', () => { slot.model = model.value; });
      const defense = addField(row, '방어 기법', element('select'));
      addOptions(defense, defenses.map(mode => [mode, DEFENSE_NAMES[mode]]));
      defense.value = slot.defense;
      defense.addEventListener('change', () => { slot.defense = defense.value; });
      if (compare && slots.length > 2) {
        const remove = element('button', 'btn public-eval-remove', '삭제');
        remove.type = 'button';
        remove.setAttribute('aria-label', `모델 ${index + 1} 삭제`);
        remove.addEventListener('click', () => {
          slots.splice(index, 1);
          clearEvaluation();
          renderSlots();
          add.focus();
        });
        row.append(remove);
      }
      list.append(row);
    }
    add.disabled = slots.length >= 5;
  }
  function appendSlot() {
    const model = models.find(name => !slots.some(slot => slot.model === name)) || models[0] || '';
    slots.push({model, defense: defenses[0] || ''});
  }
  appendSlot();
  if (compare) appendSlot();
  renderSlots();
  fields.append(list);
  if (compare) {
    add.addEventListener('click', () => {
      if (slots.length >= 5) return;
      clearEvaluation();
      appendSlot();
      renderSlots();
      list.lastElementChild?.querySelector('select')?.focus();
    });
    fields.append(add);
  }
  const submit = submitButton(fields, compare ? '모델 비교 실행' : '성능 평가 실행');
  submit.disabled = !emails.length || !models.length || !defenses.length;
  updateStatus(status, submit.disabled ? '평가에 사용할 이메일 또는 모델을 불러오지 못했어요.' : '평가를 실행하면 자동 채점 결과와 모델 요약이 표시돼요.');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled || fields.disabled) return;
    const conditions = new Set(slots.map(({model, defense}) => model + '\0' + defense));
    if (conditions.size !== slots.length) {
      updateStatus(status, '서로 다른 모델이나 방어 기법을 선택해 주세요.', true);
      return;
    }
    fields.disabled = true;
    output.replaceChildren();
    let completed = 0;
    try {
      for (const {model, defense} of slots) {
        updateStatus(status, `${completed + 1} / ${slots.length} · ${model} 평가 중…`);
        const result = await api('/api/public/evaluate', {model, emailId: sample.value, defense});
        output.append(resultCard(result, model, defense, true));
        completed++;
      }
      updateStatus(status, `평가 ${completed}건을 완료했어요.`);
    } catch (error) {
      updateStatus(status, `${completed} / ${slots.length}건 완료. ${error.message || '평가를 완료할 수 없어요.'}`, true);
    } finally {
      fields.disabled = false;
    }
  });
  return root;
}
