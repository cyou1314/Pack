// CI-only child: actual native Host/Runner/Worker, synthetic browser and
// identity observer. Not shipped in the developer browser payload.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createNativeTaskPipe} from '../../runtime/browser-driver/native-task-pipe.mjs';
import {createNativeTaskWorker} from '../../runtime/browser-driver/native-task-worker.mjs';
import {enrollNativeIdentity,verifyNativeIdentity} from '../../runtime/browser-driver/native-identity.mjs';
import {BrowserSessions} from '../../runtime/browser-driver/sessions.mjs';
import {AccountExecutionLeases} from '../../runtime/account-execution-leases.mjs';
const parent=await fs.mkdtemp(path.join(os.tmpdir(),'pack-native-worker-fixture-'));
const directory=path.join(parent,'agents-v2');
const accountRef='12345678-1234-4234-8234-123456789abc',provider='chatgpt';
const binding={account_ref:accountRef,provider,access_kind:'native',model_id:null,memory_boundary:'providerManaged'};
const profile={id:accountRef,providerID:provider,mode:'nativeAccount',
  credentialReference:{kind:'nativeSession',vaultKey:accountRef},contextBoundary:'providerAccount',
  memoryBoundary:'providerManaged',enabledCapabilities:['chat']};
const root=path.join(directory,'native-sessions'),leaseRoot=path.join(directory,'execution-leases');
const origin='https://chatgpt.com';
let url='about:blank',sends=0,closed=0;
const observe=async()=>({url,state:'ready',busy:false,accountID:'fixture-account',
  workspaceID:'fixture-workspace',modelID:null,memoryBoundary:'providerManaged',
  modelVerified:true,memoryVerified:true});
const page={url:()=>url,isClosed:()=>false,goto:async value=>{url=value;}};
const sessions=new BrowserSessions({root,launch:async()=>({pages:()=>[page],close:async()=>{closed++;}})});
try{
  await fs.mkdir(directory,{mode:0o700});
  await fs.mkdir(root,{mode:0o700});
  await fs.mkdir(leaseRoot,{mode:0o700});
  await fs.writeFile(path.join(directory,'agent-registry.json'),JSON.stringify({schemaVersion:1,profiles:[profile]}),{mode:0o600});
  const lease=await new AccountExecutionLeases({root:leaseRoot}).acquire(accountRef);
  try{
    await sessions.open(profile);url=origin+'/';
    await enrollNativeIdentity({root:directory,binding,observe,confirm:async()=>true});
  }finally{await sessions.close(profile);await lease.release();}
  url='about:blank';
  const worker=createNativeTaskWorker({directory,accountRef,provider,taskId:1,
    pipe:createNativeTaskPipe(),createSessions:async()=>sessions,
    createPageDriver:async()=>({
      inspect:({signal}={})=>verifyNativeIdentity({root:directory,binding,observe,signal}),
      prepareConversation:async()=>({url:origin+'/',conversationID:'fresh',empty:true}),
      sendOnce:async ({text})=>{
        if(!text.includes('"text":"fixture"'))throw Error('unexpected text');
        sends++;return {conversationID:'fresh',turnID:'turn1',accepted:true};
      },
      waitForReply:async()=>({url:origin+'/',conversationID:'fresh',turnID:'turn1',
        role:'assistant',complete:true,text:'fixture pipe answer'})
    })
  });
  const result=await worker.run();
  const expected=process.argv[2]==='deny'?'blocked':'review';
  if(result.status!==expected||!result.cleanupConfirmed||result.exitCode!==0||
     sends!==(expected==='review'?1:0)||closed!==2)
    throw Error('fixture worker result invalid');
}catch{process.exitCode=1;}
finally{await fs.rm(parent,{recursive:true,force:true});}
