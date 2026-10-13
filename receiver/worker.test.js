// SPDX-License-Identifier: GPL-2.0-or-later
import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker, { newCode, sameSecret } from './worker.js';

const TOKEN = 'a-long-secret-read-token';
const GOOD = { app: 'AMDLSS', version: '0.1.0-beta.8', game: 'Fake Game', files: { 'exe-folder/OptiScaler.log': 'hooked\n' } };

/** A KV stand-in: a Map of key to text (and one of key to metadata); `options` keeps the last put's options.
 * list() answers two keys a page, so the Worker has to follow the cursor. */
function bucket() {
  const objects = new Map();
  const metas = new Map();
  const store = {
    objects,
    metas,
    options: null,
    async put(key, body, options = {}) {
      objects.set(key, body);
      metas.set(key, options.metadata ?? null);
      store.options = options;
    },
    async get(key) {
      return objects.has(key) ? objects.get(key) : null;
    },
    async list({ prefix = '', cursor } = {}) {
      const names = [...objects.keys()].filter((name) => name.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0;
      const page = names.slice(start, start + 2);
      const complete = start + page.length >= names.length;
      return {
        keys: page.map((name) => ({ name, metadata: metas.get(name) ?? null, expiration: 9999999999 })),
        list_complete: complete,
        ...(complete ? {} : { cursor: String(start + page.length) }),
      };
    },
  };
  return store;
}

const env = (extra = {}) => ({ REPORTS: bucket(), READ_TOKEN: TOKEN, ...extra });

function post(body, headers = {}, path = '/reports') {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return new Request('https://r.test' + path, { method: 'POST', body: text, headers: { 'Content-Type': 'application/json', ...headers } });
}

const get = (path, token) => new Request('https://r.test' + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });

test('a good report is stored re-serialized under a Crockford code and answers it', async () => {
  const e = env();
  const response = await worker.fetch(post({ ...GOOD }), e);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('Content-Type'), 'application/json');
  const { code } = await response.json();
  assert.match(code, /^[0-9A-HJKMNP-TV-Z]{8}$/);
  assert.deepEqual([...e.REPORTS.objects.keys()], [`reports/${code}.json`]);
  assert.equal(e.REPORTS.options.expirationTtl, 30 * 24 * 60 * 60);
  assert.equal(e.REPORTS.options.metadata.app_version, GOOD.version);
  assert.equal(e.REPORTS.options.metadata.games, 1);   // no games.txt in GOOD: one game
  assert.ok(Math.abs(e.REPORTS.options.metadata.created - Date.now() / 1000) < 60);
  assert.deepEqual(JSON.parse(e.REPORTS.objects.get(`reports/${code}.json`)), GOOD);
});

test('the stored object is what was validated, not the raw body', async () => {
  const e = env();
  const raw = '{ "files": {"a.txt": "x"},\n "game": "G", "version": "1", "app": "AMDLSS" }';
  const { code } = await (await worker.fetch(post(raw), e)).json();
  assert.equal(e.REPORTS.objects.get(`reports/${code}.json`), '{"app":"AMDLSS","version":"1","game":"G","files":{"a.txt":"x"}}');
});

test('a content type other than application/json is refused', async () => {
  for (const type of ['text/plain', 'application/x-www-form-urlencoded', 'application/jsonx', '']) {
    const request = new Request('https://r.test/reports', { method: 'POST', body: JSON.stringify(GOOD), headers: type ? { 'Content-Type': type } : {} });
    assert.equal((await worker.fetch(request, env())).status, 415, type);
  }
  assert.equal((await worker.fetch(post(GOOD, { 'Content-Type': 'application/json; charset=utf-8' }), env())).status, 200);
});

test('a body over 8 MiB is refused by its Content-Length and by the bytes actually read', async () => {
  const e = env();
  const big = JSON.stringify({ ...GOOD, files: { 'a.txt': 'x'.repeat(8 << 20) } });
  assert.equal((await worker.fetch(post(big), e)).status, 413);
  const stream = new Request('https://r.test/reports', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, duplex: 'half',
    body: new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode(big)); c.close(); } }),
  });
  assert.equal((await worker.fetch(stream, e)).status, 413);   // a stream announces no length: only the bytes count
  assert.equal((await worker.fetch(post('{}', { 'Content-Length': 'abc' }), e)).status, 413);
  assert.equal(e.REPORTS.objects.size, 0);
});

test('invalid bodies are 400 with a JSON error and nothing is stored', async () => {
  const e = env();
  const bodies = ['not json', '', '[]', '{}', JSON.stringify({ ...GOOD, files: { '../x.log': 'x' } }),
    JSON.stringify({ ...GOOD, files: { 'a.exe': 'x' } }), JSON.stringify({ ...GOOD, files: { 'a.log': 'x\u0000' } }),
    JSON.stringify({ ...GOOD, app: 'Other' })];
  for (const body of bodies) {
    const response = await worker.fetch(post(body), e);
    assert.equal(response.status, 400, body);
    assert.equal(typeof (await response.json()).error, 'string');
  }
  const notUtf8 = new Request('https://r.test/reports', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: new Uint8Array([0x7b, 0xff, 0x7d]) });
  assert.equal((await worker.fetch(notUtf8, e)).status, 400);
  assert.equal(e.REPORTS.objects.size, 0);
});

test('a code that is taken is not overwritten: the Worker tries another', async () => {
  const e = env();
  const first = await (await worker.fetch(post(GOOD), e)).json();
  const other = await (await worker.fetch(post({ ...GOOD, game: 'Other' }), e)).json();
  assert.notEqual(first.code, other.code);
  assert.equal(JSON.parse(e.REPORTS.objects.get(`reports/${first.code}.json`)).game, 'Fake Game');
  const full = env({ REPORTS: { get: async () => '{}', put: async () => assert.fail('overwrote a report') } });   // every key taken
  assert.equal((await worker.fetch(post(GOOD), full)).status, 503);
});

test('newCode draws five bits per byte from the alphabet', () => {
  assert.equal(newCode(() => new Uint8Array(8)), '00000000');
  assert.equal(newCode(() => new Uint8Array([31, 32, 255, 10, 18, 22, 21, 17])), 'Z0ZAJPNH');
  assert.match(newCode(), /^[0-9A-HJKMNP-TV-Z]{8}$/);
});

test('uploads are rate limited per IP when the binding exists', async () => {
  const keys = [];
  const limiter = { limit: async ({ key }) => (keys.push(key), { success: keys.length <= 1 }) };
  const e = env({ UPLOAD_LIMITER: limiter });
  assert.equal((await worker.fetch(post(GOOD, { 'CF-Connecting-IP': '203.0.113.7' }), e)).status, 200);
  const limited = await worker.fetch(post(GOOD, { 'CF-Connecting-IP': '203.0.113.7' }), e);
  assert.equal(limited.status, 429);
  assert.equal(limited.headers.get('Retry-After'), '60');
  assert.deepEqual(keys, ['203.0.113.7', '203.0.113.7']);
  assert.equal(e.REPORTS.objects.size, 1);
});

test('reading a report needs the bearer token', async () => {
  const e = env();
  const { code } = await (await worker.fetch(post(GOOD), e)).json();
  assert.equal((await worker.fetch(get(`/reports/${code}`), e)).status, 401);
  assert.equal((await worker.fetch(get(`/reports/${code}`, 'wrong'), e)).status, 401);
  assert.equal((await worker.fetch(get(`/reports/${code}`, TOKEN + 'x'), e)).status, 401);
  assert.equal((await worker.fetch(new Request(`https://r.test/reports/${code}`, { headers: { Authorization: `Basic ${TOKEN}` } }), e)).status, 401);
  assert.equal((await worker.fetch(get('/reports/NOSUCHCD'), e)).status, 401);   // no token: no hint whether the code exists
  const ok = await worker.fetch(get(`/reports/${code}`, TOKEN), e);
  assert.equal(ok.status, 200);
  assert.deepEqual(await ok.json(), GOOD);
  assert.equal((await worker.fetch(get(`/reports/${code}`, TOKEN), env({ READ_TOKEN: undefined }))).status, 401);
  assert.equal((await worker.fetch(get(`/reports/${code}`, ''), env({ READ_TOKEN: '' }))).status, 401);
});

test('an unknown or malformed code is 404 for the token holder', async () => {
  const e = env();
  for (const code of ['ABCDEFGH', 'abcdefgh', 'ILOU0000', '123', '1234567890', '..%2f..', '12345678.json']) {
    assert.equal((await worker.fetch(get(`/reports/${code}`, TOKEN), e)).status, 404, code);
  }
});

test('other paths and methods are 404 or 405', async () => {
  const e = env();
  const send = (method, path) => worker.fetch(new Request('https://r.test' + path, { method }), e);
  assert.equal((await send('GET', '/')).status, 404);
  assert.equal((await send('GET', '/reports/')).status, 404);
  assert.equal((await send('GET', '/reports/A/B')).status, 404);
  assert.equal((await send('GET', '/admin')).status, 404);
  assert.equal((await send('GET', '/reports')).status, 401);   // the list exists but needs the token
  assert.equal((await send('PUT', '/reports')).status, 405);
  assert.equal((await send('DELETE', '/reports/ABCDEFGH')).status, 405);
  assert.equal((await send('POST', '/reports/ABCDEFGH')).status, 405);
  assert.equal((await send('HEAD', '/reports/ABCDEFGH')).status, 405);
});

test('listing the reports needs the Bearer [REDACTED] and reveals no code without it', async () => {
  const e = env();
  const { code } = await (await worker.fetch(post(GOOD), e)).json();
  for (const request of [get('/reports'), get('/reports', 'wrong')]) {
    const response = await worker.fetch(request, e);
    assert.equal(response.status, 401);
    assert.ok(!(await response.text()).includes(code));
  }
  const ok = await worker.fetch(get('/reports', TOKEN), e);
  assert.equal(ok.status, 200);
  const { reports } = await ok.json();
  assert.equal(reports.length, 1);
  assert.equal(reports[0].code, code);
  assert.ok(Math.abs(reports[0].created - Date.now() / 1000) < 60);
  assert.equal((await worker.fetch(get('/reports', TOKEN), env({ READ_TOKEN: undefined }))).status, 401);
});

test('the list is newest first, follows the cursor, and a key without metadata has no date', async () => {
  const e = env();
  const seed = (code, created) => e.REPORTS.put(`reports/${code}.json`, '{}',
    created === null ? {} : { metadata: { created, app_version: '1', games: 1 } });
  await seed('AAAA2222', 100);
  await seed('BBBB3333', 300);
  await seed('CCCC4444', 200);
  await seed('DDDD5555', null);   // stored before metadata existed
  await seed('EEEE6666', 400);
  e.REPORTS.objects.set('other/FFFF7777.json', '{}');   // outside the prefix: never listed
  const { reports } = await (await worker.fetch(get('/reports', TOKEN), e)).json();
  assert.deepEqual(reports, [
    { code: 'EEEE6666', created: 400 },
    { code: 'BBBB3333', created: 300 },
    { code: 'CCCC4444', created: 200 },
    { code: 'AAAA2222', created: 100 },
    { code: 'DDDD5555', created: null },
  ]);
});

test('an upload stores when it arrived, the app version and the game count as KV metadata', async () => {
  const e = env();
  const two = { ...GOOD, files: { 'games.txt': 'A/ | A\nB/ | B\n', 'system.txt': 'x\n' } };
  const { code } = await (await worker.fetch(post(two), e)).json();
  const metadata = e.REPORTS.metas.get(`reports/${code}.json`);
  assert.equal(metadata.app_version, GOOD.version);
  assert.equal(metadata.games, 2);
  assert.equal(typeof metadata.created, 'number');
});

test('sameSecret compares by value', async () => {
  assert.equal(await sameSecret('abc', 'abc'), true);
  assert.equal(await sameSecret('abc', 'abd'), false);
  assert.equal(await sameSecret('abc', 'abcd'), false);
});
