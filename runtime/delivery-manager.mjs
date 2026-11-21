import path from 'node:path';

// Owns receipt persistence, replay matching and idempotent task completion.
export function createDeliveryManager({
  stateStore, call, settleTaskMemory, completeWithEnvelope, listActiveTasks, sleep,
  memberName, resultsDir, mkdir, writeFile, pollMs, onDelivered = () => {},
  onRecoveryError = async () => {}, log = console,
}) {
  const receipts = stateStore.loadDeliveryReceipts();
  const save = () => stateStore.saveDeliveryReceipts(receipts);

  function storeReceipt(task, reply, chatUrl) {
    receipts[task.id] = { taskId: task.id, title: task.title, body: task.body, reply, chatUrl, state: 'pending' };
    save();
  }

  function matchingReceipt(task) {
    const receipt = receipts[task.id];
    return receipt?.state === 'pending' && receipt.title === task.title && receipt.body === task.body ? receipt : null;
  }

  async function completeSafely(task, claimToken, receipt, expectedAttempt = task.attempts + 1) {
    const artifacts = [];
    if (!receipt.memorySettlement) {
      receipt.memorySettlement = await settleTaskMemory({ call }, task, receipt.reply);
      save();
      if (receipt.memorySettlement.written) log.log(`task ${task.id} memory version=${receipt.memorySettlement.version_id} written`);
    }
    if (Array.from(receipt.reply).length > 500) {
      mkdir(resultsDir, { recursive: true, mode: 0o700 });
      const target = path.join(resultsDir, `task-${task.id}-attempt-${expectedAttempt}.txt`);
      writeFile(target, receipt.reply, { mode: 0o600 });
      artifacts.push({ kind: 'text', path: target });
    }
    for (;;) {
      try {
        await completeWithEnvelope({ call }, {
          taskId: task.id, claimToken, result: receipt.reply,
          envelope: {
            verdict: 'done',
            summary: Array.from(receipt.reply).slice(0, 500).join(''),
            artifacts,
            self_check: { ran: true, evidence: '驾驶层返回经核对的助手回复' },
            known_issues: ['Muse 会话自主浏览无法逐次执行出口检查', ...(receipt.memorySettlement?.issue ? [receipt.memorySettlement.issue] : [])],
            followups: [],
          },
        });
        receipt.state = 'completed';
        save();
        onDelivered(task.id);
        log.log(`task ${task.id} delivered from saved receipt`);
        return 'completed';
      } catch (error) {
        log.error(`task ${task.id} completion retry:`, error.message);
        await sleep(pollMs);
        try {
          const tasks = await listActiveTasks(call, { status: 'open,claimed,working,review,done', assignee_name: memberName });
          const current = tasks.find(item => item.id === task.id);
          const summary = Array.from(receipt.reply).slice(0, 500).join('').trim();
          if (current && ['review', 'done'].includes(current.status) && current.result === summary) {
            receipt.state = 'completed';
            save();
            log.log(`task ${task.id} delivery already confirmed`);
            return 'completed';
          }
          if (!current || !['claimed', 'working'].includes(current.status) || current.assignee !== memberName || current.attempts !== expectedAttempt) {
            log.error(`task ${task.id} no longer held; saved receipt retained`);
            return 'pending';
          }
        } catch (checkError) {
          log.error(`task ${task.id} delivery status check failed:`, checkError.message);
          await onRecoveryError(checkError);
        }
      }
    }
  }

  return { storeReceipt, matchingReceipt, completeSafely, getReceipt(taskId) { return receipts[taskId] || null; } };
}
