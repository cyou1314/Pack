import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { BrowserSessions } from './sessions.mjs';

const one = { id: '11111111-1111-4111-8111-111111111111', mode: 'nativeAccount' };
const two = { id: '22222222-2222-4222-8222-222222222222', mode: 'nativeAccount' };
async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'pack-browser-'));
  await fs.chmod(root, 0o700);
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

test('separate private profile directories and persistent data across reopen', async t => {
  const root = await fixture(t), paths = [];
  const sessions = new BrowserSessions({ root, launch: async directory => {
    paths.push(directory);
    return { close: async () => {} };
  } });
  await sessions.open(one);
  await sessions.open(two);
  assert.notEqual(paths[0], paths[1]);
  await fs.writeFile(path.join(paths[0], 'fixture'), 'preserved');
  await sessions.close(one);
  await sessions.open(one);
  assert.equal(paths[0], paths[2]);
  assert.equal(await fs.readFile(path.join(paths[2], 'fixture'), 'utf8'), 'preserved');
  await sessions.close(one);
  await sessions.close(two);
});

test('rejects API mode, traversal, duplicate open and another manager lease', async t => {
  const root = await fixture(t);
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const sessions = new BrowserSessions({ root, launch: async () => { await gate; return { close: async () => {} }; } });
  await assert.rejects(sessions.open({ ...one, mode: 'officialAPI' }), /native_profile_required/);
  await assert.rejects(sessions.open({ ...one, id: '../escape' }), /invalid_profile_id/);
  const opening = sessions.open(one);
  await assert.rejects(sessions.open(one), /session_busy/);
  release();
  await opening;
  const other = new BrowserSessions({ root, launch: async () => assert.fail('must not launch') });
  await assert.rejects(other.open(one), /session_lease_exists/);
  await sessions.close(one);
});

test('refuses linked profile and non-private directory', async t => {
  const root = await fixture(t);
  const outside = path.join(root, 'other');
  await fs.mkdir(outside, { mode: 0o700 });
  await fs.symlink(outside, path.join(root, one.id));
  const sessions = new BrowserSessions({ root, launch: async () => assert.fail('must not launch') });
  await assert.rejects(sessions.open(one), /unsafe_session_directory/);
  assert.deepEqual(await fs.readdir(outside), []);
  await fs.chmod(root, 0o755);
  await assert.rejects(sessions.open(two), /unsafe_session_directory/);
});

test('ambiguous launch and close failures retain exclusive lease', async t => {
  const root = await fixture(t);
  const failed = new BrowserSessions({ root, launch: async () => { throw Error('injected'); } });
  await assert.rejects(failed.open(one), /injected/);
  assert.equal(failed.status(one), 'recoveryRequired');
  const stuck = new BrowserSessions({ root, closeTimeoutMs: 20, launch: async () => ({
    close: () => new Promise(() => {})
  }) });
  await stuck.open(two);
  await assert.rejects(stuck.close(two), /browser_close_timeout/);
  assert.equal(stuck.status(two), 'recoveryRequired');
  const other = new BrowserSessions({ root, launch: async () => assert.fail('must not launch') });
  await assert.rejects(other.open(one), /session_lease_exists/);
  await assert.rejects(other.open(two), /session_lease_exists/);
});
