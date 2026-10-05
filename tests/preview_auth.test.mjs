import test from 'node:test';
import assert from 'node:assert/strict';
import {previewCookies} from '../tools/preview_auth.mjs';

test('preview credentials never go to an unrelated or insecure host', async () => {
  const original = globalThis.fetch;
  let called = false;
  globalThis.fetch = async () => { called = true; };
  try {
    for (const url of ['https://vercel.app.attacker.test/', 'http://preview.vercel.app/', 'https://user@preview.vercel.app/']) {
      await assert.rejects(previewCookies(url, 'private'));
    }
    assert.equal(called, false);
    assert.deepEqual(await previewCookies('http://127.0.0.1:4317/', 'private'), []);
  } finally { globalThis.fetch = original; }
});

test('authentication follows no redirect and creates only a host-scoped cookie', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    assert.equal(options.redirect, 'manual');
    assert.equal(options.headers['x-vercel-protection-bypass'], 'private');
    return {status: 307, headers: {getSetCookie: () => ['_vercel_jwt=cookie-value; Path=/; Secure; HttpOnly']}};
  };
  try {
    const [cookie] = await previewCookies('https://preview.vercel.app/blog/test/', 'private');
    assert.equal(cookie.url, 'https://preview.vercel.app');
    assert.equal(cookie.domain, undefined);
    assert.equal(cookie.httpOnly, true);
    assert.equal(cookie.secure, true);
    assert.equal(cookie.value, 'cookie-value');
  } finally { globalThis.fetch = original; }
});
