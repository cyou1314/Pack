import test from 'node:test';
import assert from 'node:assert/strict';
import { createTaskLifecycle } from '../task-lifecycle.mjs';

const task = (overrides = {}) => ({ id: 7, status: 'open', attempts: 2, ...overrides });
const quietLog = { error() {} };

test('claims an open task and passes a stable execution context', async () => {
  const calls = [], executions = [];
  const processTask = createTaskLifecycle({
    call: async (name, args) => { calls.push({ name, args }); return { claim_token: 'claim-7' }; },
    processClaimedTask: async (...args) => executions.push(args),
    sleep: async () => {}, log: quietLog,
  });
  await processTask(task());
  assert.deepEqual(calls, [{ name: 'task_claim', args: { task_id: 7 } }]);
  assert.equal(executions.length, 1);
  assert.equal(executions[0][1], 'claim-7');
  assert.equal(executions[0][2], false);
  assert.equal(executions[0][3], 3);
  assert.equal(executions[0][4].runStarted, false);
});

test('accepts transferred tasks without incrementing the expected attempt', async () => {
  const calls = [], executions = [];
  const processTask = createTaskLifecycle({
    call: async (name, args) => { calls.push(name); return { claim_token: 'transfer-token' }; },
    processClaimedTask: async (...args) => executions.push(args),
    sleep: async () => {}, log: quietLog,
  });
  await processTask(task({ status: 'claimed', attempts: 4 }));
  assert.deepEqual(calls, ['task_accept_transfer']);
  assert.equal(executions[0][2], true);
  assert.equal(executions[0][3], 4);
});

test('retries only tagged Hub network failures and blocks after the third failure', async () => {
  const calls = [], waits = [], stops = [];
  const networkError = Object.assign(new Error('socket reset'), { hubNetworkFailure: true });
  const processTask = createTaskLifecycle({
    call: async (name, args) => { calls.push({ name, args }); return { claim_token: 'claim-7' }; },
    processClaimedTask: async () => { throw networkError; },
    sleep: async ms => waits.push(ms), onStopped: id => stops.push(id), log: quietLog,
  });
  await processTask(task());
  assert.equal(waits.length, 2);
  assert.deepEqual(waits, [20000, 20000]);
  assert.equal(calls.filter(x => x.name === 'task_block').length, 1);
  assert.equal(calls.at(-1).args.fail_class, 'transient');
  assert.deepEqual(stops, [7]);
});

test('blocks non-network execution failures without retrying', async () => {
  const calls = [], waits = [], stops = [];
  const processTask = createTaskLifecycle({
    call: async (name, args) => { calls.push({ name, args }); return { claim_token: 'claim-7' }; },
    processClaimedTask: async () => { throw new Error('invalid task contract'); },
    sleep: async ms => waits.push(ms), onStopped: id => stops.push(id), log: quietLog,
  });
  await processTask(task());
  assert.equal(calls.filter(x => x.name === 'task_block').length, 1);
  assert.equal(calls.at(-1).args.fail_class, 'unknown');
  assert.deepEqual(waits, []);
  assert.deepEqual(stops, [7]);
});

test('rejects a claim response without a token before running the task', async () => {
  let executed = false;
  const processTask = createTaskLifecycle({
    call: async () => ({}),
    processClaimedTask: async () => { executed = true; },
    sleep: async () => {}, log: quietLog,
  });
  await assert.rejects(processTask(task()), /missing_claim_token/);
  assert.equal(executed, false);
});
