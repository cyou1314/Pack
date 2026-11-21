// Owns dispatcher approval waits for tasks explicitly marked as requiring approval.
export function createApprovalManager({ call, sleep, listActiveTasks, approvalDecision, log = console }) {
  if (![call, sleep, listActiveTasks, approvalDecision].every(fn => typeof fn === 'function')) {
    throw new TypeError('approval manager dependencies are required');
  }
  const pending = new Map();

  async function checkPendingApprovals() {
    for (const [taskId, wait] of pending) {
      if (wait.decision) continue;
      const result = await call('room_read_new', { room: wait.room, since_id: wait.cursor, limit: 50 });
      for (const message of result.messages || []) {
        wait.cursor = Math.max(wait.cursor, message.id);
        const decision = approvalDecision(message.body);
        if (message.author === 'dispatcher' && decision?.taskId === taskId) wait.decision = decision.decision;
      }
    }
  }

  async function waitForTaskApproval({ task, claimToken, wait, requested = false }) {
    if (!wait || typeof wait !== 'object') throw new TypeError('approval wait state is required');
    if (!requested) {
      const request = await call('room_post', {
        room: wait.room,
        kind: 'approval_request',
        body: `任务#${task.id}「${task.title}」需要群主批准才能执行，请调度员确认。`,
      });
      wait.cursor = request.message_id;
    }
    pending.set(task.id, wait);
    await call('task_update', { task_id: task.id, claim_token: claimToken, note: '已提交批准，等待中' });
    for (let index = 0; index < 360 && !wait.decision; index++) {
      await sleep(20000);
      try {
        await checkPendingApprovals();
        const tasks = await listActiveTasks(call, { assignee_name: task.assignee });
        const current = tasks.find(item => item.id === task.id);
        if (!current || !['claimed', 'working'].includes(current.status)) {
          log.log(`task ${task.id} left active state (${current && current.status}); approval wait aborted`);
          pending.delete(task.id);
          return 'cancelled';
        }
        if (index % 2 === 0) await call('task_update', { task_id: task.id, claim_token: claimToken, note: '' });
      } catch (error) {
        log.error('approval wait hiccup (continuing):', error.message);
      }
    }
    pending.delete(task.id);
    return wait.decision === '批准' ? 'approved' : wait.decision === '拒绝' ? 'rejected' : 'timeout';
  }

  return {
    checkPendingApprovals,
    waitForTaskApproval,
    clear(taskId) { pending.delete(taskId); },
    getPendingCount() { return pending.size; },
  };
}
