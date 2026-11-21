import test from 'node:test';
import assert from 'node:assert/strict';
import { createApprovalManager } from '../approval-manager.mjs';

const quietLog = { log() {}, error() {} };
const task = { id: 12, title: 'Sensitive task', assignee: 'pack-agent', room: 'pack-main' };
const parseDecision = body => { const match = /^(批准|拒绝)\s*任务#(\d+)(?!\d)/.exec(body || ''); return match ? { decision: match[1], taskId: Number(match[2]) } : null; };

test('posts an approval request, waits for dispatcher approval and releases pending state', async () => {
  const calls = [], waits = [];
  let reads = 0;
  const manager = createApprovalManager({
    call: async (name, args) => {
      calls.push({ name, args });
      if (name === 'room_post') return { message_id: 40 };
      if (name === 'room_read_new') return { messages: ++reads === 1 ? [{ id: 41, author: 'dispatcher', body: '批准 任务#12' }] : [] };
      return {};
    },
    sleep: async ms => waits.push(ms),
    listActiveTasks: async () => [{ ...task, status: 'working' }],
    approvalDecision: parseDecision, log: quietLog,
  });
  const wait = { decision: null, room: 'pack-main', cursor: 0 };
  assert.equal(await manager.waitForTaskApproval({ task, claimToken: 'token-12', wait }), 'approved');
  assert.equal(calls[0].name, 'room_post');
  assert.equal(calls[1].name, 'task_update');
  assert.deepEqual(waits, [20000]);
  assert.equal(manager.getPendingCount(), 0);
});

test('rejects a dispatcher decision and reports timeout distinctly', async () => {
  const manager = createApprovalManager({
    call: async name => name === 'room_read_new' ? { messages: [{ id: 51, author: 'dispatcher', body: '拒绝 任务#12' }] } : { message_id: 50 },
    sleep: async () => {},
    listActiveTasks: async () => [{ ...task, status: 'claimed' }],
    approvalDecision: parseDecision, log: quietLog,
  });
  assert.equal(await manager.waitForTaskApproval({ task, claimToken: 'token', wait: { decision: null, room: 'pack-main', cursor: 0 } }), 'rejected');
  const timeoutManager = createApprovalManager({
    call: async name => name === 'room_post' ? { message_id: 60 } : { messages: [] },
    sleep: async () => {},
    listActiveTasks: async () => [{ ...task, status: 'working' }],
    approvalDecision: parseDecision, log: quietLog,
  });
  assert.equal(await timeoutManager.waitForTaskApproval({ task, claimToken: 'token', wait: { decision: null, room: 'pack-main', cursor: 0 } }), 'timeout');
});

test('aborts when the task is no longer active', async () => {
  const manager = createApprovalManager({
    call: async name => name === 'room_post' ? { message_id: 70 } : { messages: [] },
    sleep: async () => {},
    listActiveTasks: async () => [{ ...task, status: 'done' }],
    approvalDecision: parseDecision, log: quietLog,
  });
  assert.equal(await manager.waitForTaskApproval({ task, claimToken: 'token', wait: { decision: null, room: 'pack-main', cursor: 0 } }), 'cancelled');
  assert.equal(manager.getPendingCount(), 0);
});

test('only dispatcher messages for the exact task can decide a pending approval', async () => {
  const manager = createApprovalManager({
    call: async () => ({ messages: [
      { id: 81, author: 'member', body: '批准 任务#12' },
      { id: 82, author: 'dispatcher', body: '批准 任务#123' },
      { id: 83, author: 'dispatcher', body: '批准 任务#12' },
    ] }),
    sleep: async () => {}, listActiveTasks: async () => [{ ...task, status: 'working' }],
    approvalDecision: parseDecision, log: quietLog,
  });
  const wait = { decision: null, room: 'pack-main', cursor: 80 };
  // Register through the public wait method; the first poll receives the messages above.
  const result = await manager.waitForTaskApproval({ task, claimToken: 'token', wait });
  assert.equal(result, 'approved');
});
