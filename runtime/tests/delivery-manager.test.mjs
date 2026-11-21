import test from 'node:test';
import assert from 'node:assert/strict';
import { createDeliveryManager } from '../delivery-manager.mjs';

function makeManager(overrides = {}) {
  const receipts = {};
  const saved = [];
  const calls = [];
  const defaults = {
    stateStore: { loadDeliveryReceipts: () => receipts, saveDeliveryReceipts: value => saved.push(structuredClone(value)) },
    call: async (name, args) => { calls.push({ name, args }); return {}; },
    settleTaskMemory: async () => ({ written: false }),
    completeWithEnvelope: async (_hub, args) => { calls.push({ name: 'complete', args }); },
    listActiveTasks: async () => [], sleep: async () => {}, memberName: 'pack-agent',
    resultsDir: '/tmp/pack-results', mkdir() {}, writeFile() {}, pollMs: 1000,
    log: { log() {}, error() {} },
  };
  const manager = createDeliveryManager({ ...defaults, ...overrides });
  return { manager, receipts, saved, calls };
}
const task = { id: 23, title: 'Build report', body: 'Create a report', attempts: 2, status: 'working', assignee: 'pack-agent' };

test('stores a pending receipt and only replays it for the same task payload', () => {
  const { manager, receipts, saved } = makeManager();
  manager.storeReceipt(task, 'verified reply', 'https://chat.example/room');
  assert.equal(receipts[23].state, 'pending');
  assert.equal(saved.length, 1);
  assert.equal(manager.matchingReceipt(task).reply, 'verified reply');
  assert.equal(manager.matchingReceipt({ ...task, body: 'changed task' }), null);
});

test('completes from a saved receipt, settles memory once and persists completion', async () => {
  let memoryCalls = 0, delivered = 0;
  const { manager, receipts, calls } = makeManager({
    settleTaskMemory: async () => { memoryCalls++; return { written: true, version_id: 'v4' }; },
    onDelivered: () => delivered++,
  });
  manager.storeReceipt(task, 'verified reply', '');
  const receipt = manager.matchingReceipt(task);
  assert.equal(await manager.completeSafely(task, 'claim-token', receipt, 3), 'completed');
  assert.equal(receipts[23].state, 'completed');
  assert.equal(memoryCalls, 1);
  assert.equal(delivered, 1);
  assert.equal(calls.find(x => x.name === 'complete').args.taskId, 23);
});

test('recognizes a completion that succeeded before a transport error', async () => {
  const { manager, receipts } = makeManager({
    completeWithEnvelope: async () => { throw new Error('response lost'); },
    listActiveTasks: async () => [{ ...task, status: 'done', result: 'verified reply' }],
  });
  manager.storeReceipt(task, 'verified reply', '');
  assert.equal(await manager.completeSafely(task, 'claim-token', manager.matchingReceipt(task), 3), 'completed');
  assert.equal(receipts[23].state, 'completed');
});

test('retains pending receipt when the claim is no longer held', async () => {
  const { manager, receipts } = makeManager({
    completeWithEnvelope: async () => { throw new Error('temporary failure'); },
    listActiveTasks: async () => [{ ...task, status: 'claimed', assignee: 'someone-else', attempts: 3 }],
  });
  manager.storeReceipt(task, 'verified reply', '');
  assert.equal(await manager.completeSafely(task, 'claim-token', manager.matchingReceipt(task), 3), 'pending');
  assert.equal(receipts[23].state, 'pending');
});

test('writes long replies as protected artifacts and includes their path in completion', async () => {
  let artifact = null;
  const reply = 'x'.repeat(501);
  const { manager, calls } = makeManager({
    mkdir: (...args) => { artifact = { mkdir: args }; },
    writeFile: (...args) => { artifact = { ...artifact, write: args }; },
  });
  manager.storeReceipt(task, reply, '');
  await manager.completeSafely(task, 'claim-token', manager.matchingReceipt(task), 3);
  assert.equal(artifact.mkdir[1].mode, 0o700);
  assert.equal(artifact.write[2].mode, 0o600);
  assert.equal(calls.find(x => x.name === 'complete').args.envelope.artifacts.length, 1);
});
