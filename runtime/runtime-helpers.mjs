export const ACTIVE_STATUSES = 'open,claimed,working,review';
export async function listActiveTasks(call, extra = {}) {
  const tasks = [];
  for (let offset = 0; ; offset += 100) {
    const page = await call('task_list', {status: ACTIVE_STATUSES, ...extra, offset});
    if (!Array.isArray(page.tasks)) throw Error('invalid task_list response');
    tasks.push(...page.tasks);
    if (page.tasks.length < 100) return tasks;
  }
}
export function approvalDecision(body) {
  const match = /^(批准|拒绝)\s*任务#(\d+)(?!\d)/.exec(body || '');
  return match ? {decision:match[1], taskId:Number(match[2])} : null;
}
export function validHost(value) {
  try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) ? u.host.toLowerCase() : null; } catch { return null; }
}
export function isHubNetworkFailure(error) {
  for (let e=error, i=0; e && i<5; e=e.cause, i++) {
    if (e instanceof TypeError || ['AbortError','TimeoutError'].includes(e.name) || /^(ECONNRESET|ECONNREFUSED|EPIPE|ETIMEDOUT|UND_ERR_SOCKET|UND_ERR_CONNECT_TIMEOUT)$/.test(e.code || '') || /socket hang up/i.test(e.message || '')) return true;
  }
  return false;
}
