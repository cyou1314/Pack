export function createScheduler({
  heartbeat, checkApprovals, listActiveTasks, memberName, hasReceipt,
  processTask, checkChat, quotaDue, collectQuota, recover, sleep, pollMs, log = console,
}) {
  const busy = new Set();
  let heartbeatLogged = false;
  let stopped = false;

  async function tick() {
    try {
      await heartbeat();
      if (!heartbeatLogged) { log.log('hub heartbeat ok'); heartbeatLogged = true; }
      await checkApprovals();
      const tasks = await listActiveTasks();
      for (const task of tasks) {
        if (/\[(?:M4A|ENG)(?:-image)?\]/.test(task.body || '')) continue;
        if (task.status !== 'open' && !(['claimed', 'working'].includes(task.status) && task.assignee === memberName && task.lease_expires == null)) continue;
        if (busy.size) break;
        if (task.assignee !== memberName && !(task.assignee == null && hasReceipt(task))) continue;
        if (busy.has(task.id)) continue;
        busy.add(task.id);
        Promise.resolve().then(() => processTask(task))
          .catch(error => log.error('task', task.id, error.message))
          .finally(() => busy.delete(task.id));
      }
      await checkChat(busy);
      if (quotaDue(busy.size > 0)) {
        busy.add('quota');
        Promise.resolve().then(() => collectQuota())
          .catch(error => log.error('quota collection failed:', error.message))
          .finally(() => busy.delete('quota'));
      }
      return { kind: 'ok', busy };
    } catch (error) {
      log.error('poll error:', error.message);
      heartbeatLogged = false;
      await recover(error);
      return { kind: 'recovered', error, busy };
    }
  }

  async function run({ shouldStop = () => stopped } = {}) {
    while (!shouldStop()) {
      await tick();
      if (!shouldStop()) await sleep(pollMs);
    }
  }

  return { tick, run, stop() { stopped = true; }, getBusy() { return new Set(busy); } };
}
