import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createNativeCollaborationHost} from '../browser-driver/native-collaboration-host.mjs';
import {enrollNativeIdentity,verifyNativeIdentity} from '../browser-driver/native-identity.mjs';
import {nativeProviderOrigin} from '../browser-driver/native-account-adapter.mjs';
import {AccountExecutionLeases} from '../account-execution-leases.mjs';
const id='12345678-1234-4234-8234-123456789abc';
async function fixture(t,provider){
  const parent=await fs.mkdtemp(path.join(os.tmpdir(),'pack-native-host-'));
  t.after(()=>fs.rm(parent,{recursive:true,force:true}));
  const directory=path.join(parent,'agents-v2');
  await fs.mkdir(directory,{mode:0o700});
  const profile={id,providerID:provider,mode:'nativeAccount',
    credentialReference:{kind:'nativeSession',vaultKey:id},
    contextBoundary:'providerAccount',memoryBoundary:'providerManaged',enabledCapabilities:['chat']};
  const save=()=>fs.writeFile(path.join(directory,'agent-registry.json'),
    JSON.stringify({schemaVersion:1,profiles:[profile]}),{mode:0o600});
  await save();
  const binding={account_ref:id,provider,access_kind:'native',model_id:null,memory_boundary:'providerManaged'};
  const origin=nativeProviderOrigin(provider);
  const observation={url:origin+'/',state:'ready',busy:false,accountID:'fixture-account',
    workspaceID:'fixture-workspace',modelID:null,memoryBoundary:'providerManaged',
    modelVerified:true,memoryVerified:true};
  // Isolated metadata-only fixture; no browser or actual Provider access.
  await enrollNativeIdentity({root:directory,binding,observe:async()=>observation,confirm:async()=>true});
  const calls=[],order=[],deadline=Date.now()/1000+90;
  const context={run_id:'fixture-run',step_key:'answer',role:'answer',max_output_tokens:4096,
    provider,access_kind:'native',account_ref:id,deadline,max_bytes:16384,
    items:[{kind:'owner_message',source_id:1,sha256:'a'.repeat(64),text:'fixture task'}]};
  let url='about:blank',sends=0,approved=null,opened=false;
  const page={url:()=>url,isClosed:()=>false,goto:async value=>{url=value;order.push('navigate');}};
  const sessions={
    open:async()=>{opened=true;order.push('open');return {pages:()=>[page]};},
    close:async()=>{opened=false;order.push('close');},
    status:()=>opened?'open':'closed'
  };
  const options={directory,accountRef:id,provider,
    createSessions:async()=>sessions,
    createPageDriver:async args=>{
      assert.equal(args.origin,origin);assert.deepEqual(args.binding,binding);
      assert.equal(args.page.url(),origin+'/');
      const manager=new AccountExecutionLeases({root:path.join(directory,'execution-leases')});
      await assert.rejects(manager.acquire(id),/account_lease_exists/);
      return {
        inspect:({signal}={})=>verifyNativeIdentity({root:directory,binding,signal,
          observe:async()=>({...observation,url:page.url()})}),
        prepareConversation:async()=>({url:origin+'/',conversationID:'fresh',empty:true}),
        sendOnce:async ({text})=>{
          assert.equal(text,approved.text);sends++;order.push('send');
          return {conversationID:'fresh',turnID:'turn1',accepted:true};
        },
        waitForReply:async()=>({url:origin+'/',conversationID:'fresh',turnID:'turn1',
          role:'assistant',complete:true,text:'fixture answer'})
      };
    },
    hubCall:async(tool,args)=>{
      calls.push(tool);
      if(tool==='task_claim')return {task_id:args.task_id,status:'claimed',claim_token:'fixture',lease_expires:deadline};
      if(tool==='collaboration_context_get')return structuredClone(context);
      if(tool==='run_start')return {task_id:args.task_id,status:'running',fence:1};
      if(tool==='task_block')return {task_id:args.task_id,status:'cancelled'};
      if(tool==='task_complete'){
        assert.equal(args.result,'fixture answer');assert.ok(!('provider_usage' in args));
        return {task_id:args.task_id,status:'review'};
      }
      throw Error('unexpected fixture tool');
    },
    authorizeEgress:async()=>{order.push('egress');return true;},
    authorizeNativeSend:async value=>{approved=value;order.push('approve');return true;}
  };
  return {directory,profile,save,context,options,calls,order,observation,sends:()=>sends};
}
for(const provider of ['gemini','doubao']){
  test(provider+' uses shared host with injected fixture driver and exact approvals',async t=>{
    const f=await fixture(t,provider);
    const host=await createNativeCollaborationHost(f.options);
    assert.deepEqual(f.calls,[]);
    assert.deepEqual(await host.execute(1),{status:'review',usage:null});
    assert.equal(f.sends(),1);
    assert.deepEqual(f.order,['open','navigate','egress','approve','send','close']);
    assert.deepEqual(f.calls,['task_claim','collaboration_context_get','run_start','collaboration_context_get','task_complete']);
    assert.equal((await host.execute(1)).code,'task_already_attempted');
    await (await new AccountExecutionLeases({root:path.join(f.directory,'execution-leases')}).acquire(id)).release();
  });
}
test('missing driver, wrong provider and legacy Muse fail before task claim',async t=>{
  const f=await fixture(t,'gemini');
  await assert.rejects(createNativeCollaborationHost({...f.options,createPageDriver:undefined}),/invalid_native_host/);
  await assert.rejects(createNativeCollaborationHost({...f.options,provider:'muse'}),/invalid_native_host/);
  await assert.rejects(createNativeCollaborationHost({...f.options,provider:'doubao'}),/profile_provider_mismatch/);
  assert.deepEqual(f.calls,[]);
});
test('changed model or foreign Hub account cannot send',async t=>{
  const f=await fixture(t,'gemini');
  const host=await createNativeCollaborationHost(f.options);
  f.profile.modelID='different';await f.save();
  assert.equal((await host.execute(1)).status,'blocked');
  f.profile.modelID=null;await f.save();
  f.context.account_ref='00000000-0000-4000-8000-000000000001';
  assert.equal((await host.execute(2)).status,'blocked');
  assert.equal(f.sends(),0);
  assert.ok(!f.calls.includes('run_start'));assert.deepEqual(f.order,[]);
});
test('changed account observation blocks before approval or send',async t=>{
  const f=await fixture(t,'doubao');
  const host=await createNativeCollaborationHost(f.options);
  f.observation.accountID='another-account';
  assert.equal((await host.execute(1)).status,'blocked');
  assert.equal(f.sends(),0);
  assert.ok(!f.calls.includes('run_start'));
  assert.deepEqual(f.order,['open','navigate','close']);
});
