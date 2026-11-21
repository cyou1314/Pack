import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { createDriverRunner } from '../driver-runner.mjs';

function fakeSpawn(output, stderr = '') {
  let captured = '';
  let invocation;
  const spawnImpl = (command, args, options) => {
    invocation = { command, args, options };
    const child = new EventEmitter();
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => setImmediate(() => child.emit('close', 137));
    child.stdin.on('data', chunk => { captured += chunk.toString(); });
    child.stdin.on('finish', () => {
      setImmediate(() => {
        if (output !== null) child.stdout.end(output);
        if (stderr) child.stderr.end(stderr);
        setImmediate(() => child.emit('close', 0));
      });
    });
    return child;
  };
  return { spawnImpl, getSource: () => captured, getInvocation: () => invocation };
}

const cfg = { profileId: 'profile-a', spaceName: 'space-a', egoBin: '/usr/local/bin/ego-browser' };
const readFile = file => file.endsWith('driver-primitives.js') ? '// primitives' : file.endsWith('chat-driver.js') ? '// chat core' : file.endsWith('quota-driver.js') ? '// quota core' : '// task core';

test('composes task, primitives and chat core, then parses the driver result', async () => {
  const fake = fakeSpawn('DRIVER-RESULT {"ok":true,"reply":"done"}\n');
  const runDriver = createDriverRunner({ dir: '/runtime', cfg, spawnImpl: fake.spawnImpl, readFile });
  assert.deepEqual(await runDriver('hello', null, { action: 'send', chatUrl: 'https://chat.example' }), { ok: true, reply: 'done' });
  assert.equal(fake.getInvocation().command, cfg.egoBin);
  assert.deepEqual(fake.getInvocation().args, ['nodejs']);
  assert.deepEqual(fake.getInvocation().options.stdio, ['pipe', 'pipe', 'pipe']);
  assert.match(fake.getSource(), /"profileId":"profile-a"/);
  assert.match(fake.getSource(), /"chatUrl":"https:\/\/chat\.example"/);
  assert.match(fake.getSource(), /\/\/ primitives/);
  assert.match(fake.getSource(), /\/\/ chat core/);
});

test('selects the quota driver for quota actions', async () => {
  const fake = fakeSpawn('DRIVER-RESULT {"ok":true}\n');
  const runDriver = createDriverRunner({ dir: '/runtime', cfg, spawnImpl: fake.spawnImpl, readFile });
  await runDriver('', null, { action: 'quota' });
  assert.match(fake.getSource(), /\/\/ quota core/);
});

test('rejects missing or malformed driver result lines', async () => {
  const missing = fakeSpawn(null, 'driver crashed');
  const runMissing = createDriverRunner({ dir: '/runtime', cfg, spawnImpl: missing.spawnImpl, readFile });
  await assert.rejects(runMissing('task'), /driver no result.*driver crashed/);
  const malformed = fakeSpawn('DRIVER-RESULT not-json\n');
  const runMalformed = createDriverRunner({ dir: '/runtime', cfg, spawnImpl: malformed.spawnImpl, readFile });
  await assert.rejects(runMalformed('task'), /driver result not json/);
});
