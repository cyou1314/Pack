// Owns task claim/retry lifecycle. Task execution policy is injected by the Runtime entrypoint.
export function createTaskLifecycle({ call, processClaimedTask, sleep, onStopped = () => {}, log = console }) {
  if (typeof call !== 'function' || typeof processClaimedTask !== 'function' || typeof sleep !== 'function') {
    throw new TypeError('task lifecycle dependencies are required');
  }

  return async function processTask(task) {
    const transferred = task.status !== 'open';
    const expectedAttempt = task.attempts + (transferred ? 0 : 1);
    const claim = await call(transferred ? 'task_accept_transfer' : 'task_claim', { task_id: task.id });
    const claimToken = claim.claim_token;
    if (!claimToken) throw new Error('missing_claim_token');
    const state = {
      runStarted: false, memories: null, transferChecked: false,
      approvalRequested: false, approvalDone: false, approvalWait: null,
      approvedHosts: new Set(), driverResult: null, driverError: null,
    };

    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        await processClaimedTask(task, claimToken, transferred, expectedAttempt, state);
        return;
      } catch (error) {
        log.error(`task ${task.id} post-claim attempt ${attempt}/3 failed:`, error.message);
        if (!error.hubNetworkFailure) {
          try {
            await call('task_block', { task_id: task.id, claim_token: claimToken, fail_class: 'unknown', reason: `中枢调用或运行处理失败: ${error.message}` });
          } catch (blockError) {
            log.error(`task ${task.id} block failed; lease expiry remains fallback:`, blockError.message);
          }
          onStopped(task.id);
          return;
        }
        if (attempt < 3) {
          await sleep(20000);
          continue;
        }
        try {
          await call('task_block', { task_id: task.id, claim_token: claimToken, fail_class: 'transient', reason: '中枢调用失败，重试三次后仍未恢复' });
        } catch (blockError) {
          log.error(`task ${task.id} transient block failed; lease expiry remains fallback:`, blockError.message);
        }
        onStopped(task.id);
      }
    }
  };
}
