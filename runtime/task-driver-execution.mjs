// Owns the long-running driver call and claim-holder heartbeat.
export async function executeTaskDriver({
  task, claimToken, prompt, state, call, runDriver,
  setIntervalImpl = setInterval, clearIntervalImpl = clearInterval,
  log = console,
}) {
  if (!task || !claimToken || !state || typeof call !== 'function' || typeof runDriver !== 'function') {
    throw new TypeError('task driver dependencies are required');
  }
  if (state.driverResult) return { kind: 'completed', result: state.driverResult };
  if (state.driverError) return { kind: 'error', error: state.driverError };
  const abort = new AbortController();
  let checkingHolder = false;
  let lostHolder = false;
  const holderTimer = setIntervalImpl(async () => {
    if (checkingHolder || lostHolder) return;
    checkingHolder = true;
    try {
      await call('task_update', { task_id: task.id, claim_token: claimToken, note: '' });
    } catch (error) {
      if (/not the current holder|claim_token mismatch/i.test(error.message)) {
        lostHolder = true;
        abort.abort();
        log.log(`task ${task.id} no longer held; driver stopped`);
      }
    } finally {
      checkingHolder = false;
    }
  }, 10000);
  try {
    const result = await runDriver(prompt, abort.signal);
    if (lostHolder) return { kind: 'lost_holder' };
    state.driverResult = result;
    return { kind: 'completed', result };
  } catch (error) {
    if (lostHolder) return { kind: 'lost_holder' };
    state.driverError = error.message;
    return { kind: 'error', error: error.message };
  } finally {
    clearIntervalImpl(holderTimer);
  }
}
