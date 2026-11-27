import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createChatGPTCollaborationHost} from '../browser-driver/chatgpt-collaboration-host.mjs';
const id='12345678-1234-4234-8234-123456789abc';
async function fixture(t,providerID='chatgpt'){
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'pack-chatgpt-host-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const directory=path.join(root,'agents-v2');await fs.mkdir(directory,{mode:0o700});
  await fs.writeFile(path.join(directory,'agent-registry.json'),JSON.stringify({schemaVersion:1,profiles:[{
    id,providerID,mode:'nativeAccount',credentialReference:{kind:'nativeSession',vaultKey:id},
    contextBoundary:'providerAccount',memoryBoundary:'providerManaged',enabledCapabilities:['chat']
  }]}),{mode:0o600});
  return {directory,accountRef:id,hubCall:async()=>assert.fail('no claim before preflight'),
    surface:{},observeIdentity:async()=>assert.fail('no observation before enrollment'),
    authorizeEgress:async()=>assert.fail('no approval'),authorizeNativeSend:async()=>assert.fail('no send'),
    createSessions:async()=>assert.fail('no launch before preflight')};
}
test('candidate host rejects foreign provider and missing proof before claim or browser launch',async t=>{
  await assert.rejects(createChatGPTCollaborationHost(await fixture(t,'gemini')),/chatgpt_profile_required/);
  await assert.rejects(createChatGPTCollaborationHost(await fixture(t)));
});
test('candidate host requires both trusted explicit approval callbacks',async t=>{
  const options=await fixture(t);
  await assert.rejects(createChatGPTCollaborationHost({...options,authorizeNativeSend:undefined}),/invalid_chatgpt_host/);
  await assert.rejects(createChatGPTCollaborationHost({...options,authorizeEgress:undefined}),/invalid_chatgpt_host/);
});
