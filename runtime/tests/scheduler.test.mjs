import test from 'node:test';
import assert from 'node:assert/strict';
import { createScheduler } from '../scheduler.mjs';

function setup(overrides = {}) {
  const events = [];
  const tasks = [];
  const options = {
    heartbeat: async () => events.push('heartbeat'),
    checkApprovals: async () => events.push('approvals'),
    listActiveTasks: async () => tasks,
    memberName: 'pack-agent', hasReceipt: () => false,
    processTask: async task => events.push('task:' + task.id),
    checkChat: async busy => events.push('chat:' + busy.size),
    quotaDue: () => false, collectQuota: async () => events.push('quota'),
    recover: async () => events.push('recover'), sleep: async () => {}, pollMs: 1000,
    log: { log() {}, error() {} },
    ...overrides,
  };
  return { scheduler: createScheduler(options), events, tasks };
}

test('runs heartbeat, approvals, task listing and chat in order', async () => {
  const h = setup();
  const result = await h.scheduler.tick();
  assert.equal(result.kind, 'ok');
  assert.deepEqual(h.events.slice(0, 3), ['heartbeat', 'approvals']);
  assert.equal(h.events[2], 'chat:0');
});

test('filters unsupported tasks and dispatches only eligible tasks', async () => {
  const h = setup();
  h.tasks.push(
    { id: 1, body: '[M4A] ignore', status: 'open' },
    { id: 2, body: 'unassigned', status: 'open', assignee: null },
    { id: 3, body: 'mine', status: 'open', assignee: 'pack-agent' },
    { id: 4, body: 'other', status: 'open', assignee: 'someone' },
  );
  h.scheduler = createSchedulerForTest(h);
  await h.scheduler.tick();
  await Promise.resolve();
  assert.ok(h.events.includes('task:3'));
  assert.ok(!h.events.includes('task:1'));
  assert.ok(!h.events.includes('task:2'));
  assert.ok(!h.events.includes('task:4'));
});

function createSchedulerForTest(h) {
  return createScheduler({
    heartbeat: async () => {}, checkApprovals: async () => {}, listActiveTasks: async () => h.tasks,
    memberName: 'pack-agent', hasReceipt: () => false, processTask: async task => h.events.push('task:' + task.id),
    checkChat: async () => {}, quotaDue: () => false, collectQuota: async () => {}, recover: async () => {},
    sleep: async () => {}, pollMs: 1, log: { log() {}, error() {} },
  });
}

test('recovers polling failures without terminating the scheduler', async () => {
  const h = setup({ heartbeat: async () => { throw new Error('hub offline'); } });
  const result = await h.scheduler.tick();
  assert.equal(result.kind, 'recovered');
  assert.ok(h.events.includes('recover'));
});

test('runs quota collection only when due and releases its busy marker', async () => {
  const h = setup({ quotaDue: () => true, collectQuota: async () => h.events.push('quota') });
  await h.scheduler.tick();
  await Promise.resolve();
  assert.ok(h.events.includes('quota'));
  assert.ok(!h.scheduler.getBusy().has('quota'));
});
