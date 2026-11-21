import test from 'node:test';
import assert from 'node:assert/strict';
import { executeTaskDriver } from '../task-driver-execution.mjs';

const task = { id: 31 };
const quietLog = { log() {} };
function harness(overrides = {}) {
  const calls = [], intervals = [], cleared = [];
  const options = {
    task, claimToken: 'claim-31', prompt: 'do work', state: {},
    call: async (name, args) => { calls.push({ name, args }); return {}; },
    runDriver: async () => ({ ok: true, threadVerified: true, reply: 'done' }),
    setIntervalImpl: (fn, ms) => { const timer = { fn, ms }; intervals.push(timer); return timer; },
    clearIntervalImpl: timer => cleared.push(timer), log: quietLog,
    ...overrides,
  };
  return { options, calls, intervals, cleared };
}

test('runs the driver, persists its result in lifecycle state and clears heartbeat', async () => {
  const h = harness();
  const result = await executeTaskDriver(h.options);
  assert.equal(result.kind, 'completed');
  assert.equal(h.options.state.driverResult.reply, 'done');
  assert.equal(h.intervals[0].ms, 10000);
  assert.deepEqual(h.cleared, [h.intervals[0]]);
});

test('aborts the driver when the claim holder is lost', async () => {
  let signal;
  const h = harness({
    call: async () => { throw new Error('not the current holder'); },
    runDriver: async (_prompt, abortSignal) => {
      signal = abortSignal;
      await h.intervals[0].fn();
      return { ok: true };
    },
  });
  const result = await executeTaskDriver(h.options);
  assert.equal(result.kind, 'lost_holder');
  assert.equal(signal.aborted, true);
  assert.equal(h.options.state.driverResult, undefined);
});

test('records driver errors for the caller to block the task', async () => {
  const h = harness({ runDriver: async () => { throw new Error('driver crashed'); } });
  const result = await executeTaskDriver(h.options);
  assert.deepEqual(result, { kind: 'error', error: 'driver crashed' });
  assert.equal(h.options.state.driverError, 'driver crashed');
  assert.deepEqual(h.cleared, [h.intervals[0]]);
});

test('reuses a previously completed driver result without starting another process', async () => {
  let starts = 0;
  const state = { driverResult: { ok: true, reply: 'cached' } };
  const h = harness({ state, runDriver: async () => { starts++; return {}; } });
  const result = await executeTaskDriver(h.options);
  assert.equal(result.result.reply, 'cached');
  assert.equal(starts, 0);
  assert.equal(h.intervals.length, 0);
});
