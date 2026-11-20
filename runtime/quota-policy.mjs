// Only parse explicit numeric values; missing/ambiguous fields remain absent.
export function parseQuotaSnapshot(input) {
 const out={};
 if (!input || typeof input!=='object') return out;
 const n=input.week_used_pct;
 if (typeof n==='number' && Number.isFinite(n) && n>=0 && n<=100) out.week_used_pct=n;
 const t=input.week_reset_at;
 if (typeof t==='number' && Number.isFinite(t) && t>0) out.week_reset_at=t>1e12 ? t/1000:t;
 const extra=input.extra_tokens_left;
 if (Number.isSafeInteger(extra) && extra>=0) out.extra_tokens_left=extra;
 return out;
}
export function quotaDue(now,lastSuccess,lastAttempt,requested,busy) {
 return !busy && (requested || now-lastSuccess>=11*60_000) && now-lastAttempt>=60_000;
}
