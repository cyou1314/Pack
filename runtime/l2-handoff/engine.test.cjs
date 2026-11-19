const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'pack-l2-test-'));
process.env.PACK_L2_SOCKET_DIR = temp;
const { cookieDetails, startHandoff } = require('./engine.cjs');
test.after(() => fs.rmdirSync(temp));

test('only Muse cookies are accepted and attributes survive', () => {
  const c = cookieDetails({ name: 'dummy', value: 'fixture', domain: '.muse.ai', path: '/', secure: true, httpOnly: true, sameSite: 'Strict', expirationDate: 2000000000 });
  assert.equal(c.url, 'https://muse.ai/');
  assert.equal(c.domain, '.muse.ai'); assert.equal(c.sameSite, 'strict');
  assert.equal(c.httpOnly, true); assert.equal(c.expirationDate, 2000000000);
  assert.throws(() => cookieDetails({ name: 'x', value: 'x', domain: 'notmuse.ai' }));
});

test('socket rejects unencrypted engine before any account or keychain use', async () => {
  let touched = false;
  const stop = await startHandoff({ encryptionEnabled: () => false, repo: { listAccounts: () => { touched = true; throw Error(); } } });
  const socketPath = path.join(temp, 'handoff.sock');
  assert.equal(fs.statSync(socketPath).mode & 0o077, 0);
  const result = await new Promise((resolve, reject) => {
    const c = net.createConnection(socketPath); let output = '';
    c.on('connect', () => c.write(JSON.stringify({ member: 'test', ticket: '12345678-1234-1234-1234-123456789abc' }) + '\n'));
    c.on('data', x => { output += x; }); c.on('end', () => resolve(JSON.parse(output))); c.on('error', reject);
  });
  assert.equal(result.ok, false); assert.equal(touched, false);
  stop();
});
