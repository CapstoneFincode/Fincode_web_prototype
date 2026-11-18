const PAGE_SIZE = 20;

function emailRow(email) {
  const details = document.createElement('details');
  details.className = 'release-email-row';
  const title = document.createElement('summary');
  title.textContent = email.title;
  const id = document.createElement('small');
  id.className = 'release-source';
  id.textContent = ' · ' + email.id;
  title.append(id);
  const content = document.createElement('div');
  content.className = 'release-note-grid';
  for (const [heading, value] of [['이메일 본문', email.body], ['기준 요약', email.reference]]) {
    const block = document.createElement('div');
    const label = document.createElement('h3');
    label.textContent = heading;
    const text = document.createElement('pre');
    text.textContent = value;
    block.append(label, text);
    content.append(block);
  }
  details.append(title, content);
  return details;
}

export function renderDatasets(catalog) {
  const emails = catalog.emails;
  const root = document.createElement('div');
  root.innerHTML = `<div class="page-head"><div><h1>정상 이메일</h1>
    <p>성능 평가에 사용할 합성 이메일의 본문과 기준 요약을 확인하세요.</p></div></div>
    <section class="panel release-main-card">
      <div class="release-tools"><label class="field">이메일 검색
        <input type="search" placeholder="제목, 본문 또는 이메일 ID" autocomplete="off"></label></div>
      <p data-count role="status"></p><div data-emails></div>
      <nav class="release-pager" aria-label="정상 이메일 페이지">
        <button type="button" class="btn" data-prev>이전</button><span data-range></span>
        <button type="button" class="btn" data-next>다음</button>
      </nav>
    </section>`;
  const search = root.querySelector('input');
  const list = root.querySelector('[data-emails]');
  const previous = root.querySelector('[data-prev]');
  const next = root.querySelector('[data-next]');
  let page = 0;

  function update() {
    const term = search.value.trim().toLowerCase();
    const matches = emails.filter(email =>
      [email.id, email.title, email.body].some(text => text.toLowerCase().includes(term)));
    const start = page * PAGE_SIZE;
    const end = Math.min(start + PAGE_SIZE, matches.length);
    root.querySelector('[data-count]').textContent = term
      ? `전체 ${emails.length}건 중 검색 결과 ${matches.length}건`
      : `전체 ${emails.length}건`;
    list.replaceChildren(...matches.slice(start, end).map(emailRow));
    if (!matches.length) list.textContent = '조건에 맞는 이메일이 없어요.';
    root.querySelector('[data-range]').textContent = matches.length ? `${start + 1}–${end} / ${matches.length}건` : '0건';
    previous.disabled = page === 0;
    next.disabled = end >= matches.length;
  }
  search.oninput = () => { page = 0; update(); };
  previous.onclick = () => { page -= 1; update(); };
  next.onclick = () => { page += 1; update(); };
  update();
  return root;
}
