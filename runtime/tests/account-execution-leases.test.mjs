import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {AccountExecutionLeases} from '../account-execution-leases.mjs';
import {createCollaborationRunner} from '../collaboration-runner.mjs';

const id='12345678-1234-4234-8234-123456789abc';
const other='12345678-1234-4234-8234-123456789abd';
async function root(t) {
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'pack-account-lease-'));
  await fs.chmod(dir,0o700);
  t.after(()=>fs.rm(dir,{recursive:true,force:true}));
  return dir;
}
test('shared roots exclude same account across independent lease managers',async t=>{
  const dir=await root(t);
  const first=new AccountExecutionLeases({root:dir}),second=new AccountExecutionLeases({root:dir});
  const lease=await first.acquire(id);
  await assert.rejects(second.acquire(id),/account_lease_exists/);
  const independent=await second.acquire(other);
  await independent.release();
  await lease.release();
  const next=await second.acquire(id);await next.release();
});
test('unsafe roots, symlinks and unknown lease files fail closed',async t=>{
  const dir=await root(t);
  const manager=new AccountExecutionLeases({root:dir});
  await assert.rejects(manager.acquire('../escape'),/invalid_account_reference/);
  const link=path.join(dir,'alias');
  await fs.symlink(dir,link);
  await assert.rejects(new AccountExecutionLeases({root:link}).acquire(id),/unsafe_account_lease_directory/);
  const lease=await manager.acquire(id);
  await fs.writeFile(path.join(dir,id+'.lease','unknown'),'preserve');
  await assert.rejects(lease.release());
  await assert.rejects(new AccountExecutionLeases({root:dir}).acquire(id),/account_lease_exists/);
  assert.equal(await fs.readFile(path.join(dir,id+'.lease','unknown'),'utf8'),'preserve');
});
function runner(dir,execute,deliveryUnknown=false) {
  let count=0;
  const instance=createCollaborationRunner({
    accountLeases:new AccountExecutionLeases({root:dir}),
    now:()=>1000,timeoutMs:20,
    hubCall:async(tool,args)=>{
      switch(tool){
        case 'task_claim':return {task_id:args.task_id,status:'claimed',claim_token:'fixture',lease_expires:90};
        case 'collaboration_context_get':return {run_id:'run',step_key:'answer',provider:'openai',
          access_kind:'api',account_ref:id,deadline:100,max_bytes:16384,
          items:[{kind:'owner_message',source_id:1,sha256:'a'.repeat(64),text:'request'}]};
        case 'run_start':return {task_id:args.task_id,status:'running',fence:1};
        case 'task_complete':if(deliveryUnknown)throw Error('unknown commit');
          return {task_id:args.task_id,status:'review'};
        case 'task_block':return {task_id:args.task_id,status:'cancelled'};
        default:throw Error('unexpected tool');
      }
    },
    resolveProfile:async()=>({id,providerID:'openai',mode:'officialAPI',
      credentialReference:{kind:'apiKey',vaultKey:id},contextBoundary:'packWorkspace',
      memoryBoundary:'disabled',enabledCapabilities:['chat']}),
    resolveAdapter:async()=>({verifyAccount:async()=>true,execute:async(...args)=>{count++;return execute(...args)}}),
    authorizeEgress:async()=>true
  });
  return {instance,count:()=>count};
}
test('timeout retains lease after runner replacement; no second invocation',async t=>{
  const dir=await root(t);
  const first=runner(dir,async()=>new Promise(()=>{}));
  assert.equal((await first.instance.execute(1)).code,'provider_outcome_unknown');
  const second=runner(dir,async()=>({status:'succeeded',text:'never'}));
  assert.equal((await second.instance.execute(2)).code,'account_unavailable');
  assert.equal(second.count(),0);
  assert.equal(first.count(),1);
});
test('confirmed delivery releases lease but unknown delivery retains it',async t=>{
  const dir=await root(t);
  const output=async()=>({status:'succeeded',text:'result'});
  assert.equal((await runner(dir,output).instance.execute(1)).status,'review');
  assert.equal((await runner(dir,output,true).instance.execute(2)).status,'deliveryUnknown');
  const next=runner(dir,output);
  assert.equal((await next.instance.execute(3)).code,'account_unavailable');
  assert.equal(next.count(),0);
});
