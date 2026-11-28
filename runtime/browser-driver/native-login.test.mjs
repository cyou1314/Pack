import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {EventEmitter} from 'node:events';
import {BrowserSessions} from './sessions.mjs';
import {resolveNativeLogin,runNativeLogin} from './native-login.mjs';
import {AccountExecutionLeases} from '../account-execution-leases.mjs';

const id='12345678-1234-4234-8234-123456789abc';
async function fixture(t,changes={}) {
  const directory=await fs.mkdtemp(path.join(os.tmpdir(),'pack-native-login-'));
  t.after(()=>fs.rm(directory,{recursive:true,force:true}));
  // Use a private immediate parent (macOS temp parent may differ).
  const root=path.join(directory,'agents-v2');
  await fs.mkdir(root,{mode:0o700});
  const profile={id:id.toUpperCase(),providerID:'chatgpt',mode:'nativeAccount',
    credentialReference:{kind:'nativeSession',vaultKey:id},
    contextBoundary:'providerAccount',memoryBoundary:'providerManaged',enabledCapabilities:['chat'],...changes};
  await fs.writeFile(path.join(root,'agent-registry.json'),JSON.stringify({schemaVersion:1,profiles:[profile]}),{mode:0o600});
  return root;
}
function fakeContext(gotos,{closeFails=false}={}) {
  const context=new EventEmitter();
  context.pages=()=>[{goto:async url=>gotos.push(url)}];
  context.close=async()=>{if(closeFails)throw Error('close failed');context.emit('close');};
  return context;
}
test('login uses canonical provider URL, shared lease and persistent browser profile',async t=>{
  const directory=await fixture(t), binding=await resolveNativeLogin({directory,accountRef:id});
  const gotos=[],paths=[];
  const createSessions=async options=>new BrowserSessions({...options,launch:async data=>{
    paths.push(data);return fakeContext(gotos);
  }});
  const leaseManager=new AccountExecutionLeases({root:binding.leaseRoot});
  for(let i=0;i<2;i++) {
    const result=await runNativeLogin({binding,createSessions,waitForExit:async()=>{
      await assert.rejects(leaseManager.acquire(id),/account_lease_exists/);
    }});
    assert.equal(result.status,'session_closed_unverified');
  }
  assert.deepEqual(gotos,['https://chatgpt.com','https://chatgpt.com']);
  assert.equal(paths[0],paths[1]);
  await (await leaseManager.acquire(id)).release();
});
test('refuses invalid modes, providers and reference substitution',async t=>{
  for(const changes of [{mode:'officialAPI'},{providerID:'evil'},{credentialReference:{kind:'nativeSession',vaultKey:'bad'}}]){
    const directory=await fixture(t,changes);
    await assert.rejects(resolveNativeLogin({directory,accountRef:id}),/invalid_native_profile/);
  }
});
test('failed close retains both leases, no automatic retry',async t=>{
  const directory=await fixture(t),binding=await resolveNativeLogin({directory,accountRef:id});
  const createSessions=async options=>new BrowserSessions({...options,launch:async()=>fakeContext([],{closeFails:true})});
  await assert.rejects(runNativeLogin({binding,createSessions,waitForExit:async()=>{}}),/close failed/);
  await assert.rejects(new AccountExecutionLeases({root:binding.leaseRoot}).acquire(id),/account_lease_exists/);
  assert.ok((await fs.stat(path.join(binding.browserRoot,id,'.pack-session-lease'))).isDirectory());
});
test('existing account lease prevents opening any browser',async t=>{
  const directory=await fixture(t),binding=await resolveNativeLogin({directory,accountRef:id});
  const held=await new AccountExecutionLeases({root:binding.leaseRoot}).acquire(id);
  try {
    await assert.rejects(runNativeLogin({binding,createSessions:async options=>new BrowserSessions({
      ...options,launch:async()=>assert.fail('must not open')
    }),waitForExit:async()=>{}}),/account_lease_exists/);
  } finally {await held.release();}
});
