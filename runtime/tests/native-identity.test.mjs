import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {enrollNativeIdentity,verifyNativeIdentity,expectedNativeAccountProof} from '../browser-driver/native-identity.mjs';

const binding=Object.freeze({account_ref:'12345678-1234-4234-8234-123456789abc',provider:'chatgpt',
  access_kind:'native',model_id:null,memory_boundary:'providerManaged'});
const ready=Object.freeze({url:'https://chatgpt.com/',state:'ready',busy:false,
  accountID:'private-account-id',workspaceID:'private-workspace-id',modelID:null,
  memoryBoundary:'providerManaged',modelVerified:true,memoryVerified:true});
async function fixture(t) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'pack-native-id-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  return {root,binding,observe:async()=>ready,confirm:async()=>true};
}
test('explicit two-observation enrollment persists only opaque proof; read-only verification',async t=>{
  const args=await fixture(t);
  let reads=0;
  assert.deepEqual(await enrollNativeIdentity({...args,observe:async()=>{reads++;return ready},
    confirm:async value=>value.accountID===ready.accountID&&value.workspaceID===ready.workspaceID}),
    {accountRef:binding.account_ref,provider:binding.provider,enrolled:true});
  assert.equal(reads,2);
  const expected=await expectedNativeAccountProof(args);
  assert.match(expected,/^[a-f0-9]{64}$/);
  const file=path.join(args.root,'native-identities',binding.account_ref+'.json');
  const content=await fs.readFile(file,'utf8');
  assert.equal(content.includes(ready.accountID),false);
  assert.equal(content.includes(ready.workspaceID),false);
  assert.equal((await verifyNativeIdentity(args)).accountProof,expected);
  await assert.rejects(enrollNativeIdentity(args),/already_enrolled/);
  assert.equal(await fs.readFile(file,'utf8'),content);
});
test('denial or identity switch between observations creates no record',async t=>{
  const args=await fixture(t);
  await assert.rejects(enrollNativeIdentity({...args,confirm:async()=>false}),/not_confirmed/);
  let n=0;
  await assert.rejects(enrollNativeIdentity({...args,observe:async()=>++n===1?ready:{...ready,accountID:'changed'}}),/unavailable/);
  assert.deepEqual(await fs.readdir(path.join(args.root,'native-identities')),[]);
});
test('account, workspace, model, memory, origin, login or uncertainty mismatch fail closed',async t=>{
  const args=await fixture(t);
  await enrollNativeIdentity(args);
  for(const patch of [{accountID:'changed'},{workspaceID:'changed'},{modelID:'changed'},{memoryBoundary:'disabled'},
    {modelVerified:false},{memoryVerified:false},{state:'challenge'},{busy:true},
    {url:'https://chatgpt.com.example.org/'},{accountID:''}]){
    await assert.rejects(verifyNativeIdentity({...args,observe:async()=>({...ready,...patch})}),/unavailable/);
  }
});
test('missing record, wrong destination and unsafe permissions are rejected',async t=>{
  const args=await fixture(t);
  await assert.rejects(expectedNativeAccountProof(args));
  await enrollNativeIdentity(args);
  await assert.rejects(expectedNativeAccountProof({...args,binding:{...binding,provider:'gemini'}}),/unavailable/);
  const file=path.join(args.root,'native-identities',binding.account_ref+'.json');
  await fs.chmod(file,0o644);
  await assert.rejects(verifyNativeIdentity(args),/unavailable/);
});
