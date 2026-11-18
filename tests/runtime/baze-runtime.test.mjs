import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Optional hosting-runtime regression. Python/Docker users do not need to
// install Node dependencies to run the dependency-free tests/*.test.mjs suite.
const require = createRequire(import.meta.url);
let Miniflare;
try {
  const wranglerRequire = createRequire(require.resolve('wrangler/package.json'));
  ({ Miniflare } = wranglerRequire('miniflare'));
} catch (error) {
  if (!['MODULE_NOT_FOUND', 'ERR_MODULE_NOT_FOUND'].includes(error.code)) throw error;
}

test('workerd performs gateway calls and rejects redirects without forwarding credentials', {
  skip: Miniflare ? false : 'Optional hosting dev dependencies are not installed (wrangler/miniflare).',
  timeout: 30000,
}, async () => {
  const root = fileURLToPath(new URL('../../', import.meta.url));
  const config = JSON.parse(readFileSync(path.join(root, 'config/email-baseline.json'), 'utf8'));
  const source = readFileSync(path.join(root, 'server/baze.mjs'), 'utf8');
  const calls = [];
  let redirect = false;
  const mf = new Miniflare({
    cf: false,
    modulesRoot: root,
    modules: [
      {
        type: 'ESModule',
        path: path.join(root, '__baze_runtime_test__.mjs'),
        contents: `import { handleApi } from './server/baze.mjs';
          export default { fetch(request, env) {
            return handleApi(request, env, ${JSON.stringify(config)});
          }};`,
      },
      { type: 'ESModule', path: path.join(root, 'server/baze.mjs'), contents: source },
    ],
    compatibilityDate: '2026-05-15',
    compatibilityFlags: ['nodejs_compat'],
    bindings: { BAZE_API_KEY: 'offline-runtime-fixture-key' },
    // All Worker outbound requests terminate here. No external API or key is used.
    outboundService: async request => {
      const url = new URL(request.url);
      calls.push({ url: request.url, method: request.method });
      assert.equal(url.origin, 'https://factchat-cloud.mindlogic.ai');
      assert.equal(request.headers.get('Authorization'), 'Bearer offline-runtime-fixture-key');
      if (redirect) return new Response(null, { status: 302, headers: { Location: 'https://never-contact.invalid/' } });
      if (request.method === 'GET') {
        assert.equal(url.pathname + url.search, '/v1/gateway/models/?type=llm');
        return Response.json({ data: [{ id: 'offline-text-model', owned_by: 'fixture' }] });
      }
      assert.equal(url.pathname, '/v1/gateway/chat/completions/');
      const body = await request.json();
      assert.equal(body.model, 'offline-text-model');
      assert.equal(body.messages[0].content, config.systemPrompt);
      assert.equal(body.messages[1].content, '제목: 회의\n\n오늘 17시 연구실 회의입니다.');
      return Response.json({
        id: 'offline-response', model: body.model,
        choices: [{ message: { content: '오늘 17시 연구실 회의' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 11, completion_tokens: 7, total_tokens: 18 },
      });
    },
  });
  try {
    // Node's fetch accepts redirect:'error'; workerd rejects it before any
    // outbound request. This test exercises the actual default runtime fetch.
    const models = await mf.dispatchFetch('https://fincode.test/api/models');
    assert.equal(models.status, 200, await models.clone().text());
    assert.deepEqual((await models.json()).models, [{ id: 'offline-text-model', ownedBy: 'fixture' }]);
    assert.equal(calls.length, 1);

    const summary = await mf.dispatchFetch('https://fincode.test/api/summarize', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Origin: 'https://fincode.test' },
      body: JSON.stringify({ model: 'offline-text-model', email: { subject: '회의', body: '오늘 17시 연구실 회의입니다.' }, defense: 'off', attachments: [] }),
    });
    assert.equal(summary.status, 200, await summary.clone().text());
    const result = await summary.json();
    assert.equal(result.mode, 'live');
    assert.equal(result.summary, '오늘 17시 연구실 회의');
    assert.equal(result.usage.totalTokens, 18);
    assert.equal(calls.length, 2);

    redirect = true;
    const refused = await mf.dispatchFetch('https://fincode.test/api/models');
    assert.equal(refused.status, 502);
    assert.equal((await refused.json()).error.code, 'GATEWAY_UNAVAILABLE');
    assert.equal(calls.length, 3, 'The Location target must never be requested.');
  } finally {
    await mf.dispose();
  }
});
