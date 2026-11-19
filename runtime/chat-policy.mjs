// Mirror room_watch.resolve_mention's ordering, boundaries and member targets.
export function resolveMention(body, names) {
  const targets = names.filter(n => n !== 'owner' && n !== 'dispatcher');
  const lowered = new Map(targets.map(n => [n.toLowerCase(), n]));
  for (const match of body.matchAll(/(?<![A-Za-z0-9_.\-/])[@＠]([A-Za-z0-9_-]+)/g)) {
    const token = match[1].toLowerCase();
    if (lowered.has(token)) return {kind:'hit', name:lowered.get(token)};
    const candidates = targets.filter(n => n.toLowerCase().startsWith(token));
    if (candidates.length === 1) return {kind:'hit', name:candidates[0]};
    if (candidates.length > 1) return {kind:'ambiguous'};
  }
  return {kind:'none'};
}
export function stripReplyMetadata(body) {
  const m = body.match(/\n\n\[pack-reply-v1:([A-Za-z0-9_-]+)\]$/);
  if (!m) return body;
  try {
    const data = JSON.parse(Buffer.from(m[1], 'base64url').toString('utf8'));
    if (data && typeof data === 'object' && !Array.isArray(data) && typeof data.author === 'string' && data.author && typeof data.text === 'string') return body.slice(0, m.index);
  } catch {}
  return body;
}
export function classifyChat(message, lastId, members, self='absent') {
  if (!Number.isSafeInteger(message.id) || message.id <= lastId || message.deleted || message.kind !== 'chat' || message.author === self || typeof message.body !== 'string') return false;
  const body = stripReplyMetadata(message.body);
  const mention = resolveMention(body, members);
  if (message.author === 'owner') return mention.kind === 'none';
  if (message.author === 'dispatcher') return [...body.matchAll(/(?<![A-Za-z0-9_.\-/])[@＠]([A-Za-z0-9_-]+)/g)].some(m => m[1].toLowerCase() === self.toLowerCase());
  return false;
}
export function quotaAllowsChat(member) {
  return typeof member?.quota_week_used_pct === 'number' && Number.isFinite(member.quota_week_used_pct) && member.quota_week_used_pct < 90;
}
