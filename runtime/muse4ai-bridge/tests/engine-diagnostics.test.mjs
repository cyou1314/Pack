import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,writeFileSync,symlinkSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {summarizeMuseStatus,diagnoseMuseEngine,readMuseDiagnosticKey} from '../muse-engine-diagnostics.mjs';
import {createMuseEngineClient} from '../muse-engine-client.mjs';

const now=Date.parse('2026-11-27T00:00:00Z');
const key='msk_fixture_only_not_a_real_secret';
const status=()=>({
  key:{scope:'generate',secret:'do-not-export'},
  accounts:{items:[{alias:'absent',group:'ready',account_id:'private-id',
    quota:{stale:false,weekly_used_percent:51,observed_at:new Date(now-1000).toISOString(),
      weekly_resets_at:new Date(now+3600000).toISOString(),
      additional_remaining_label:'5B tokens left',plan:'private-label'}}]},
  raw:'private-body'
});
test('cached ready alias and scope are never identity or capability proof',()=>{
  const report=summarizeMuseStatus(status(),{now});
  assert.equal(report.credential,'accepted');
  assert.equal(report.account,'alias_reported');
  assert.equal(report.reportedReadiness,'ready_reported');
  assert.equal(report.quota.week_used_pct,51);
  assert.equal(report.quota.week_reset_at,(now+3600000)/1000);
  assert.equal(report.identityVerified,false);
  assert.equal(report.capabilitiesVerified,false);
  assert.equal(report.executionEnabled,false);
  const serialized=JSON.stringify(report);
  for(const secret of ['do-not-export','private-id','private-label','private-body','5B tokens left','absent'])
    assert.ok(!serialized.includes(secret));
  assert.ok(!('extra_tokens_left' in report.quota));
});
test('missing duplicate or non-ready alias never becomes verified',()=>{
  const missing=status();missing.accounts.items=[];
  assert.equal(summarizeMuseStatus(missing,{now}).account,'not_reported');
  const duplicate=status();duplicate.accounts.items.push({...duplicate.accounts.items[0]});
  assert.equal(summarizeMuseStatus(duplicate,{now}).account,'ambiguous');
  const paused=status();paused.accounts.items[0].group='paused';
  const report=summarizeMuseStatus(paused,{now});
  assert.equal(report.reportedReadiness,'other_group_reported');
  assert.equal(report.executionEnabled,false);
  assert.equal(summarizeMuseStatus({key:{scope:'unexpected'}},{now}).credential,'unknown');
});
test('stale future ambiguous and malformed quota never becomes usable cached value',()=>{
  for(const [patch,expected] of [
    [{stale:true},'stale'],
    [{observed_at:new Date(now-7*3600000).toISOString()},'stale'],
    [{observed_at:new Date(now+1000).toISOString()},'unavailable'],
    [{stale:undefined},'unavailable'],
    [{weekly_used_percent:101},'unavailable'],
    [{weekly_used_percent:'51'},'unavailable']
  ]){
    const input=status();Object.assign(input.accounts.items[0].quota,patch);
    const report=summarizeMuseStatus(input,{now});
    assert.equal(report.quota.status,expected);
    assert.ok(!('week_used_pct' in report.quota));
  }
});
test('diagnostic invokes only local status and bounds response size',async()=>{
  const calls=[];
  const client=createMuseEngineClient({key,directory:tmpdir(),fetchImpl:async(url,options)=>{
    calls.push([url,options.method,options.redirect]);
    return Response.json(status());
  }});
  const result=await diagnoseMuseEngine({client,now});
  assert.equal(result.credential,'accepted');
  assert.deepEqual(calls,[['http://127.0.0.1:47821/v1/status','GET','error']]);
  const oversized=createMuseEngineClient({key,directory:tmpdir(),fetchImpl:async()=>
    new Response('x'.repeat(1024*1024+1))});
  await assert.rejects(()=>oversized.status(),/muse_status_too_large/);
  assert.equal((await diagnoseMuseEngine({client:oversized,now})).executionEnabled,false);
});
test('credential rejection and transport error are distinct but never disclose raw error',async()=>{
  for(const [failure,credential,service] of [
    ['muse_http_401','rejected','responded'],
    ['muse_http_403','rejected','responded'],
    ['private-response-content','unknown','unconfirmed']
  ]){
    const report=await diagnoseMuseEngine({client:{status:async()=>{throw Error(failure);}},now});
    assert.equal(report.credential,credential);
    assert.equal(report.service,service);
    assert.equal(report.executionEnabled,false);
    assert.ok(!JSON.stringify(report).includes(failure));
  }
});
test('diagnostic key read is bounded private and non-symlink',async()=>{
  const root=mkdtempSync(path.join(tmpdir(),'pack-muse-diagnostic-'));
  const file=path.join(root,'.api-key'),alias=path.join(root,'alias');
  try{
    writeFileSync(file,key,{mode:0o600});
    assert.equal(await readMuseDiagnosticKey(file),key);
    symlinkSync(file,alias);
    await assert.rejects(()=>readMuseDiagnosticKey(alias));
    chmodSync(file,0o644);
    await assert.rejects(()=>readMuseDiagnosticKey(file),/muse_diagnostic_unavailable/);
    chmodSync(file,0o600);writeFileSync(file,'x'.repeat(513));
    await assert.rejects(()=>readMuseDiagnosticKey(file),/muse_diagnostic_unavailable/);
  }finally{rmSync(root,{recursive:true,force:true});}
});
