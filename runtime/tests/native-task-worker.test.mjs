import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createNativeTaskWorker} from '../browser-driver/native-task-worker.mjs';
import {enrollNativeIdentity,verifyNativeIdentity} from '../browser-driver/native-identity.mjs';

const id='12345678-1234-4234-8234-123456789abc';
const provider='chatgpt',origin='https://chatgpt.com';
const binding={account_ref:id,provider,access_kind:'native',model_id:null,memory_boundary:'providerManaged'};
async function fixture(t,{closeFails=false,approval=true}={}){
  const parent=await fs.mkdtemp(path.join(os.tmpdir(),'pack-worker-'));
  t.after(()=>fs.rm(parent,{recursive:true,force:true}));
  const directory=path.join(parent,'agents-v2');
  await fs.mkdir(directory,{mode:0o700});
  const profile={id,providerID:provider,mode:'nativeAccount',
    credentialReference:{kind:'nativeSession',vaultKey:id},contextBoundary:'providerAccount',
    memoryBoundary:'providerManaged',enabledCapabilities:['chat']};
  await fs.writeFile(path.join(directory,'agent-registry.json'),JSON.stringify({schemaVersion:1,profiles:[profile]}),{mode:0o600});
  const observe=async()=>({url:origin+'/',state:'ready',busy:false,
    accountID:'fixture-account',workspaceID:'fixture-workspace',modelID:null,
    memoryBoundary:'providerManaged',modelVerified:true,memoryVerified:true});
  await enrollNativeIdentity({root:directory,binding,observe,confirm:async()=>true});
  const calls=[];let sends=0,closed=0;
  const deadline=Date.now()/1000+60;
  const context={run_id:'run',step_key:'answer',role:'answer',max_output_tokens:4096,
    account_ref:id,provider,access_kind:'native',deadline,max_bytes:16384,
    items:[{kind:'owner_message',source_id:1,sha256:'a'.repeat(64),text:'fixture'}]};
  const options={directory,accountRef:id,provider,taskId:1,
    pipe:{
      exchange:async frame=>{
        const r=JSON.parse(frame);calls.push(r.tool);let result;
        switch(r.tool){
          case 'task_claim':result={task_id:1,status:'claimed',claim_token:'fixture',lease_expires:deadline};break;
          case 'collaboration_context_get':result=context;break;
          case 'run_start':result={task_id:1,status:'running',fence:1};break;
          case 'native_egress_authorize':case 'native_send_authorize':result={approved:approval};break;
          case 'task_complete':result={task_id:1,status:'review'};break;
          case 'task_block':result={task_id:1,status:'cancelled'};break;
          default:assert.fail('unexpected tool');
        }
        return Buffer.from(JSON.stringify({version:1,sequence:r.sequence,result}));
      },
      close:async()=>{closed++;}
    },
    createSessions:async()=>({
      open:async()=>({pages:()=>[{url:()=>origin+'/',isClosed:()=>false}]}),
      close:async()=>{if(closeFails)throw Error('fixture close failed');}
    }),
    createPageDriver:async()=>({
      inspect:({signal}={})=>verifyNativeIdentity({root:directory,binding,observe,signal}),
      prepareConversation:async()=>({url:origin+'/',conversationID:'fresh',empty:true}),
      sendOnce:async()=>{sends++;return {conversationID:'fresh',turnID:'turn1',accepted:true};},
      waitForReply:async()=>({url:origin+'/',conversationID:'fresh',turnID:'turn1',
        role:'assistant',complete:true,text:'fixture answer'})
    })
  };
  return {options,calls,sends:()=>sends,closed:()=>closed,directory};
}

test('worker is single use and closes its pipe after confirmed receipt',async t=>{
  const f=await fixture(t);
  const worker=createNativeTaskWorker(f.options);
  assert.deepEqual(await worker.run(),{status:'review',cleanupConfirmed:true,exitCode:0});
  await assert.rejects(worker.run(),/already_used/);
  assert.equal(f.sends(),1);assert.equal(f.closed(),1);
  assert.deepEqual(f.calls,['task_claim','collaboration_context_get','native_egress_authorize',
    'run_start','collaboration_context_get','native_send_authorize','task_complete']);
});
test('review receipt does not mask retained lease after failed browser close',async t=>{
  const f=await fixture(t,{closeFails:true});
  const worker=createNativeTaskWorker(f.options);
  assert.deepEqual(await worker.run(),{status:'review',cleanupConfirmed:false,exitCode:1});
  assert.equal(f.sends(),1);assert.equal(f.closed(),1);
  assert.ok((await fs.stat(path.join(f.directory,'execution-leases',id+'.lease'))).isDirectory());
});
test('known account lease prevents claim, leaves marker and closes pipe',async t=>{
  const f=await fixture(t);
  const dir=path.join(f.directory,'execution-leases');
  await fs.mkdir(dir,{mode:0o700});
  await fs.mkdir(path.join(dir,id+'.lease'),{mode:0o700});
  assert.deepEqual(await createNativeTaskWorker(f.options).run(),
    {status:'unavailable',cleanupConfirmed:false,exitCode:1});
  assert.deepEqual(f.calls,[]);assert.equal(f.sends(),0);assert.equal(f.closed(),1);
});
test('fixed developer entry refuses all unreviewed providers without a frame',()=>{
  const cli=fileURLToPath(new URL('../browser-driver/native-task-worker-cli.mjs',import.meta.url));
  for(const provider of ['chatgpt','gemini','doubao','muse']){
    const result=spawnSync(process.execPath,[cli,provider,id,'1'],{input:'',timeout:5000});
    assert.equal(result.status,2);assert.equal(result.stdout.length,0);assert.equal(result.stderr.length,0);
  }
});
