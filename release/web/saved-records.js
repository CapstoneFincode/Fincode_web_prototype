import {mountResultComparison} from '/result-comparison.js';

// Reuse the stored-record comparison UI while routing every request through
// the public projection. Row labels identify a stored run without using titles.
export function renderSavedComparison(kind, api) {
  const root = document.createElement('div');
  root.dataset.resultComparison = kind;
  root.className = 'rc-page release-saved-records';
  async function read(path, method = 'GET', body) {
    const url = new URL(path, location.origin);
    if (method === 'GET' && url.pathname === '/api/results/comparison-records') {
      const page = url.searchParams.get('page') || '1';
      const params = new URLSearchParams({kind, page});
      const result = await api('/api/public/comparison-records?' + params);
      return {...result, records: result.records.map(row => ({...row, name: `기록 ${row.experimentId.slice(0,8)}`}))};
    }
    if (method === 'POST' && url.pathname === '/api/results/compare') {
      return api('/api/public/compare', {kind, selections: body.selections});
    }
    throw new Error('요청한 조회 경로를 찾을 수 없어요.');
  }
  mountResultComparison(root, {kind, api: read, ready: true});
  return root;
}
