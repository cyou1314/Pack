import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { createBrowserSessions } from './playwright.mjs';

test('Chromium isolated persistent session on a loopback fixture', { timeout: 90000 }, async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'pack-browser-smoke-'));
  await fs.chmod(root, 0o700);
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'text/html');
    res.end('<html><title>Pack fixture</title></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = 'http://127.0.0.1:' + server.address().port;
  const one = { id: '11111111-1111-4111-8111-111111111111', mode: 'nativeAccount' };
  const two = { id: '22222222-2222-4222-8222-222222222222', mode: 'nativeAccount' };
  const sessions = await createBrowserSessions({ root, headless: true });
  t.after(async () => {
    for (const profile of [one, two]) {
      if (sessions.status(profile) === 'open') await sessions.close(profile);
    }
    await new Promise(resolve => server.close(resolve));
    await fs.rm(root, { recursive: true, force: true });
  });
  async function page(context) {
    const p = context.pages()[0] || await context.newPage();
    await p.goto(url, { timeout: 10000 });
    return p;
  }
  const a = await page(await sessions.open(one));
  await a.evaluate(() => localStorage.setItem('pack-fixture', 'profile-one'));
  const b = await page(await sessions.open(two));
  assert.equal(await b.evaluate(() => localStorage.getItem('pack-fixture')), null);
  await sessions.close(two);
  await sessions.close(one);
  const reopened = await page(await sessions.open(one));
  assert.equal(await reopened.evaluate(() => localStorage.getItem('pack-fixture')), 'profile-one');
  await sessions.close(one);
});
