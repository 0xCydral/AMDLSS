// SPDX-License-Identifier: GPL-2.0-or-later
// AMDLSS log receiver (Cloudflare Worker + R2).
//   POST /reports        a validated report goes to R2 as reports/<code>.json; answers {"code": "..."}
//   GET  /reports/<code> the stored report, for whoever holds READ_TOKEN
// Bindings: REPORTS (R2 bucket), READ_TOKEN (secret), UPLOAD_LIMITER (rate limit, optional).
import { MAX_BODY_BYTES, validateReport } from './validate.js';

const ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';   // Crockford base32
const CODE = /^[0-9A-HJKMNP-TV-Z]{8}$/;
const CODE_TRIES = 5;

function reply(status, body, extra = {}) {
  return new Response(typeof body === 'string' ? body : JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
      'X-Content-Type-Options': 'nosniff',
      ...extra,
    },
  });
}

const refuse = (status, error, extra) => reply(status, { error }, extra);

export function newCode(random = (bytes) => crypto.getRandomValues(bytes)) {
  // 256 is a multiple of 32, so the low five bits of a byte are uniform.
  return Array.from(random(new Uint8Array(8)), (byte) => ALPHABET[byte & 31]).join('');
}

/** The request body as bytes, or null as soon as it passes `limit` bytes. */
async function readCapped(request, limit) {
  if (!request.body) return new Uint8Array(0);
  const reader = request.body.getReader();
  const chunks = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.byteLength;
  }
  return bytes;
}

/** Compares two strings in time that does not depend on where they differ (both are hashed to 32 bytes first). */
export async function sameSecret(given, secret) {
  const digest = async (text) => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)));
  const [a, b] = await Promise.all([digest(given), digest(secret)]);
  let different = 0;
  for (let i = 0; i < a.length; i++) different |= a[i] ^ b[i];
  return different === 0;
}

async function upload(request, env) {
  if (!/^application\/json\s*(;|$)/i.test(request.headers.get('Content-Type') || '')) {
    return refuse(415, 'send application/json');
  }
  const announced = request.headers.get('Content-Length');
  if (announced !== null && (!/^\d+$/.test(announced) || Number(announced) > MAX_BODY_BYTES)) {
    return refuse(413, 'the report is larger than 8 MiB');
  }
  if (env.UPLOAD_LIMITER) {
    const { success } = await env.UPLOAD_LIMITER.limit({ key: request.headers.get('CF-Connecting-IP') || 'unknown' });
    if (!success) return refuse(429, 'too many reports, try again in a minute', { 'Retry-After': '60' });
  }
  const bytes = await readCapped(request, MAX_BODY_BYTES);
  if (bytes === null) return refuse(413, 'the report is larger than 8 MiB');
  let value;
  try {
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    return refuse(400, 'the body is not valid UTF-8 JSON');
  }
  const checked = validateReport(value);
  if (!checked.ok) return refuse(400, checked.error);
  const stored = JSON.stringify(checked.report);
  for (let attempt = 0; attempt < CODE_TRIES; attempt++) {
    const code = newCode();
    // etagDoesNotMatch "*": R2 stores the object only when no object has that key yet, and answers null otherwise.
    const put = await env.REPORTS.put(`reports/${code}.json`, stored, {
      httpMetadata: { contentType: 'application/json' },
      onlyIf: { etagDoesNotMatch: '*' },
    });
    if (put) return reply(200, { code });
  }
  return refuse(503, 'could not pick a free code, try again');
}

async function download(request, env, code) {
  const token = (request.headers.get('Authorization') || '').match(/^Bearer (\S+)$/)?.[1];
  if (!env.READ_TOKEN || !token || !(await sameSecret(token, env.READ_TOKEN))) {
    return refuse(401, 'a read token is needed', { 'WWW-Authenticate': 'Bearer' });
  }
  if (!CODE.test(code)) return refuse(404, 'not found');
  const object = await env.REPORTS.get(`reports/${code}.json`);
  return object ? reply(200, await object.text()) : refuse(404, 'not found');
}

export default {
  async fetch(request, env) {
    const { pathname } = new URL(request.url);
    if (pathname === '/reports') {
      return request.method === 'POST' ? upload(request, env) : refuse(405, 'use POST', { Allow: 'POST' });
    }
    const match = pathname.match(/^\/reports\/([^/]+)$/);
    if (!match) return refuse(404, 'not found');
    return request.method === 'GET' ? download(request, env, match[1]) : refuse(405, 'use GET', { Allow: 'GET' });
  },
};
