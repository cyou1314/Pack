import { createHash } from 'node:crypto';

// Opt-in collaboration execution contract. All callbacks are trusted local
// host dependencies; none is selected by model output. No secret export,
// Provider transport, browser automation or startup side effect lives here.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const LIMIT = 16 * 1024;
const PROVIDERS = new Map([
  ['muse','native'], ['chatgpt','native'], ['openai','api'],
  ['gemini','native'], ['gemini-api','api'], ['doubao','native'], ['volcengine','api']
]);
const FAILURES = new Set(['account_unavailable','quota','provider_rejected','provider_unavailable']);
const bytes = value => Buffer.byteLength(value,'utf8');
const positiveID = value => Number.isSafeInteger(value) && value > 0;
const freeze = value => {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    for (const v of Object.values(value)) freeze(v);
    Object.freeze(value);
  }
  return value;
};
function fail(code) { throw Error(code); }

export function validateContext(context) {
  if (!context || typeof context.run_id !== 'string' || !context.run_id ||
      typeof context.step_key !== 'string' || !/^[a-z][a-z0-9-]{0,63}$/.test(context.step_key) ||
      !UUID.test(context.account_ref || '') ||
      PROVIDERS.get(context.provider) !== context.access_kind ||
      !['native','api'].includes(context.access_kind) ||
      !Number.isFinite(context.deadline) || context.max_bytes !== LIMIT ||
      !Array.isArray(context.items) || context.items.length < 1 || context.items.length > 8)
    fail('invalid_context');
  const seen = new Set();
  const items = context.items.map(item => {
    if (!item || !['owner_message','dependency_result'].includes(item.kind) ||
        !positiveID(item.source_id) || !DIGEST.test(item.sha256 || '') ||
        typeof item.text !== 'string' || !item.text.trim())
      fail('invalid_context');
    const key = item.kind + ':' + item.source_id;
    if (seen.has(key)) fail('invalid_context');
    seen.add(key);
    return {kind:item.kind,source_id:item.source_id,sha256:item.sha256,text:item.text};
  });
  if (!items.some(x=>x.kind==='owner_message') || bytes(JSON.stringify(items)) > LIMIT)
    fail('invalid_context_budget');
  return freeze({run_id:context.run_id,step_key:context.step_key,provider:context.provider,
    access_kind:context.access_kind,account_ref:context.account_ref,deadline:context.deadline,items});
}

// Match the existing Swift AgentProfile / AgentCredentialReference.forProfile.
// Only opaque IDs and model metadata leave this resolver, not keys or cookies.
export function destinationFor(context, profile) {
  const id = typeof profile?.id === 'string' ? profile.id.toLowerCase() : '';
  const mode = context.access_kind === 'api' ? 'officialAPI' : 'nativeAccount';
  const kind = mode === 'officialAPI' ? 'apiKey' : 'nativeSession';
  const contextBoundary = mode === 'officialAPI' ? 'packWorkspace' : 'providerAccount';
  const memories = mode === 'officialAPI' ? ['packManaged','disabled'] : ['providerManaged','disabled'];
  if (!UUID.test(id) || id !== context.account_ref || profile.providerID !== context.provider ||
      profile.mode !== mode || profile.credentialReference?.kind !== kind ||
      profile.credentialReference?.vaultKey !== id || profile.contextBoundary !== contextBoundary ||
      !memories.includes(profile.memoryBoundary) ||
      !Array.isArray(profile.enabledCapabilities) || !profile.enabledCapabilities.includes('chat'))
    fail('profile_binding_mismatch');
  if (profile.modelID != null && (typeof profile.modelID !== 'string' ||
      !profile.modelID.trim() || profile.modelID.length > 128)) fail('invalid_model');
  return freeze({account_ref:id,provider:context.provider,access_kind:context.access_kind,
    model_id:profile.modelID ?? null,memory_boundary:profile.memoryBoundary});
}

function usageOf(usage) {
  // Native providers may not expose exact usage. Unknown is not zero.
  if (usage == null) return null;
  if (!Number.isSafeInteger(usage.input_tokens) || usage.input_tokens < 0 ||
      !Number.isSafeInteger(usage.output_tokens) || usage.output_tokens < 0)
    fail('invalid_provider_usage');
  return {input_tokens:usage.input_tokens,output_tokens:usage.output_tokens};
}
function outcomeOf(value) {
  if (value?.status === 'failed' && FAILURES.has(value.code))
    return {status:'failed',code:value.code,usage:usageOf(value.usage)};
  if (value?.status !== 'succeeded' || typeof value.text !== 'string' ||
      !value.text.trim() || bytes(value.text) > LIMIT) fail('invalid_provider_result');
  return {status:'succeeded',text:value.text,usage:usageOf(value.usage)};
}
const fingerprint = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

// Contract: hubCall MUST be an authenticated, no-automatic-retry transport.
// resolveProfile loads trusted local registry data. resolveAdapter returns an
// adapter with verifyAccount(destination) and execute(request,{signal}).
// verifyAccount must check the actual local session or Keychain binding, not
// echo untrusted input. execute must enforce the same account binding, avoid
// hidden fallback/retry, and honor AbortSignal. accountLeases is the shared
// device-local AccountExecutionLeases instance. authorizeEgress is an explicit
// local owner authorization for the exact fingerprint and destination.
export function createCollaborationRunner({
  hubCall, resolveProfile, resolveAdapter, authorizeEgress, accountLeases,
  timeoutMs = 60000, now = () => Date.now()
}) {
  if ([hubCall,resolveProfile,resolveAdapter,authorizeEgress,now].some(x=>typeof x!=='function') ||
      typeof accountLeases?.acquire!=='function' ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 120000)
    fail('invalid_runner_configuration');
  const busy = new Set();
  const uncertain = new Set();
  async function stop(task_id, claim_token, code, usage=null) {
    try {
      const result = await hubCall('task_block',{task_id,claim_token,reason:code,
        fail_class:code==='quota'?'quota':'task'});
      if (result?.task_id === task_id && result.status === 'cancelled')
        return {status:'blocked',code,usage};
    } catch {}
    return {status:'stopUnknown',code,usage};
  }
  return {
    async execute(task_id) {
      if (!positiveID(task_id)) fail('invalid_task_id');
      let claim;
      try { claim = await hubCall('task_claim',{task_id}); }
      catch { return {status:'claimUnknown',usage:null}; }
      if (claim?.task_id!==task_id || claim.status!=='claimed' ||
          typeof claim.claim_token!=='string' || !claim.claim_token ||
          !Number.isFinite(claim.lease_expires))
        return {status:'claimUnknown',usage:null};
      const claim_token=claim.claim_token;
      let account, lease, locked=false, invoked=false, retainLease=false, timer, controller;
      let usage=null;
      let phase='context_unavailable';
      try {
        const context=validateContext(await hubCall('collaboration_context_get',{task_id,claim_token}));
        phase='profile_binding_mismatch';
        const profile=await resolveProfile(context.account_ref);
        const destination=destinationFor(context,profile);
        account=destination.account_ref;
        phase='account_unavailable';
        if (busy.has(account) || uncertain.has(account)) return await stop(task_id,claim_token,'account_unavailable');
        busy.add(account); locked=true;
        lease=await accountLeases.acquire(account);
        if(typeof lease?.release!=='function') fail('invalid_account_lease');
        const adapter=await resolveAdapter(destination);
        if (!adapter || typeof adapter.verifyAccount!=='function' || typeof adapter.execute!=='function' ||
            await adapter.verifyAccount(destination)!==true)
          return await stop(task_id,claim_token,'account_unavailable');
        const request=freeze({version:1,task_id,run_id:context.run_id,step_key:context.step_key,
          destination,items:context.items,deadline:context.deadline});
        const digest=fingerprint(request);
        phase='egress_not_authorized';
        if (await authorizeEgress(freeze({request,sha256:digest}))!==true)
          return await stop(task_id,claim_token,phase);
        phase='run_registration_unknown';
        const run=await hubCall('run_start',{task_id,claim_token});
        if (run?.task_id!==task_id || run.status!=='running' || run.fence!==1)
          return await stop(task_id,claim_token,phase);
        // Re-read after registration; revoked/edited inputs must never fall
        // back to cached prompts or whole-room history.
        phase='context_changed';
        const current=validateContext(await hubCall('collaboration_context_get',{task_id,claim_token}));
        if (fingerprint(current)!==fingerprint(context)) return await stop(task_id,claim_token,phase);
        const remaining=Math.floor(Math.min(claim.lease_expires,context.deadline)*1000-now());
        if (remaining<=0) return await stop(task_id,claim_token,'deadline_expired');
        controller=new AbortController();
        phase='provider_outcome_unknown';
        // Exactly one adapter invocation. Timeout does not imply upstream
        // cancellation; retain the shared on-disk account lease on ambiguity.
        const deadline=new Promise((_,reject)=>{
          timer=setTimeout(()=>{controller.abort();reject(Error('provider_timeout'));},Math.min(remaining,timeoutMs));
        });
        invoked=true;
        retainLease=true;
        const pending=Promise.resolve().then(()=>adapter.execute(request,{signal:controller.signal}));
        let output;
        try { output=outcomeOf(await Promise.race([pending,deadline])); }
        catch { uncertain.add(account); return await stop(task_id,claim_token,phase); }
        finally { clearTimeout(timer); }
        usage=output.usage;
        if (output.status==='failed') {
          const stopped=await stop(task_id,claim_token,output.code,usage);
          retainLease=stopped.status!=='blocked';
          return stopped;
        }
        // Do not retry a submission whose remote commit is unknown.
        try {
          const delivered=await hubCall('task_complete',{task_id,claim_token,result:output.text});
          if (delivered?.task_id===task_id && delivered.status==='review') {
            retainLease=false;
            return {status:'review',usage};
          }
        } catch {}
        return {status:'deliveryUnknown',usage};
      } catch {
        if (invoked && account) uncertain.add(account);
        return await stop(task_id,claim_token,phase,usage);
      } finally {
        clearTimeout(timer);
        if (lease && !retainLease) {
          try { await lease.release(); }
          catch { uncertain.add(account); }
        }
        if (locked) busy.delete(account);
      }
    }
  };
}
