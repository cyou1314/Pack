import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createNativeAccountAdapter,nativeProviderOrigin} from '../browser-driver/native-account-adapter.mjs';
import {createCollaborationRunner} from '../collaboration-runner.mjs';

const id='12345678-1234-4234-8234-123456789abc',proof='a'.repeat(64);
function fixture(overrides={}) {
  const destination={account_ref:id,provider:'chatgpt',access_kind:'native',model_id:null,memory_boundary:'providerManaged'};
  const profile={id,providerID:'chatgpt',mode:'nativeAccount',credentialReference:{kind:'nativeSession',vaultKey:id},
    contextBoundary:'providerAccount',memoryBoundary:'providerManaged',enabledCapabilities:['chat']};
  const request={version:1,task_id:1,run_id:'run',step_key:'answer',destination,deadline:100,
    items:[{kind:'owner_message',source_id:1,sha256:'b'.repeat(64),text:'approved goal'}]};
  const origin=nativeProviderOrigin('chatgpt');
  const calls=[];
  const ready={url:origin+'/',state:'ready',accountProof:proof,modelID:null,memoryBoundary:'providerManaged',busy:false};
  const driver={
    inspect:async()=>({...ready}),
    prepareConversation:async()=>({url:origin+'/',conversationID:'fresh',empty:true}),
    sendOnce:async args=>{calls.push(['send',args]);return {conversationID:'fresh',turnID:'turn1',accepted:true}},
    waitForReply:async args=>{calls.push(['wait',args]);return {url:origin+'/c/fresh',conversationID:'fresh',
      turnID:'turn1',role:'assistant',complete:true,text:'answer'}},
    ...overrides.driver
  };
  const options={profile,destination,expectedAccountProof:proof,now:()=>1000,
    sessions:{open:async p=>{calls.push(['open',p]);return {}},close:async p=>calls.push(['close',p])},
    createPageDriver:async()=>driver,authorizeNativeSend:async args=>{calls.push(['authorize',args]);return true},
    ...overrides.options};
  return {adapter:createNativeAccountAdapter(options),options,request,calls,ready,destination,profile};
}
test('single send in fresh conversation, native usage unknown, no credential export',async()=>{
  const f=fixture();
  assert.equal(await f.adapter.verifyAccount(f.destination),true);
  assert.deepEqual(await f.adapter.execute(f.request),{status:'succeeded',text:'answer',usage:null});
  await assert.rejects(f.adapter.execute(f.request),/not_ready/);
  await f.adapter.close();
  assert.deepEqual(f.calls.map(x=>x[0]),['open','authorize','send','wait','close']);
  const approved=f.calls[1][1];assert.match(approved.textSHA256,/^[a-f0-9]{64}$/);
  assert.equal(approved.conversationID,'fresh');
  assert.equal(f.calls[2][1].text.includes('approved goal'),true);
});
test('account mismatch, login/challenge, cross-origin or unknown memory stops before send',async()=>{
  for(const patch of [{accountProof:'c'.repeat(64)},{state:'loginRequired'},{state:'challenge'},
    {url:'https://chatgpt.com.example.org/'},{memoryBoundary:'disabled'},{busy:true}]){
    const f=fixture();const g=fixture({driver:{inspect:async()=>({...f.ready,...patch})}});
    assert.equal(await g.adapter.verifyAccount(g.destination),false);
    await assert.rejects(g.adapter.execute(g.request));
    await g.adapter.close();
    assert.deepEqual(g.calls.map(x=>x[0]),['open','close']);
  }
});
test('deny exact send authorization or stale conversation',async()=>{
  for(const overrides of [{options:{authorizeNativeSend:async()=>false}},
    {driver:{prepareConversation:async()=>({url:'https://chatgpt.com/',conversationID:'old',empty:false})}}]){
    const f=fixture(overrides);
    assert.equal(await f.adapter.verifyAccount(f.destination),true);
    assert.equal((await f.adapter.execute(f.request)).status,'failed');
    assert.equal(f.calls.some(x=>x[0]==='send'),false);
    await f.adapter.close();
  }
});
test('unconfirmed send, old reply and partial reply are ambiguous, never resent',async()=>{
  for(const overrides of [{driver:{sendOnce:async()=>({accepted:false})}},
    {driver:{waitForReply:async()=>({url:'https://chatgpt.com/',conversationID:'old',turnID:'turn1',complete:true,role:'assistant',text:'old'})}},
    {driver:{waitForReply:async()=>({url:'https://chatgpt.com/',conversationID:'fresh',turnID:'turn1',complete:false,role:'assistant',text:'partial'})}}]){
    const f=fixture(overrides);
    assert.equal(await f.adapter.verifyAccount(f.destination),true);
    await assert.rejects(f.adapter.execute(f.request),/unknown/);
    await assert.rejects(f.adapter.execute(f.request),/not_ready/);
    assert.ok(f.calls.filter(x=>x[0]==='send').length<=1);
    await f.adapter.close();
  }
});
test('runner closes verified browser on egress denial before releasing account lease',async()=>{
  const f=fixture();
  const order=[];
  const runner=createCollaborationRunner({
    hubCall:async(tool,args)=>{
      if(tool==='task_claim')return {task_id:1,status:'claimed',claim_token:'token',lease_expires:90};
      if(tool==='collaboration_context_get')return {run_id:'run',step_key:'answer',provider:'chatgpt',
        access_kind:'native',account_ref:id,deadline:100,max_bytes:16384,items:f.request.items};
      if(tool==='task_block')return {task_id:1,status:'cancelled'};
      throw Error('unexpected tool');
    },
    resolveProfile:async()=>f.profile,resolveAdapter:async()=>({
      ...f.adapter,close:async()=>{await f.adapter.close();order.push('close')}
    }),
    accountLeases:{acquire:async()=>({release:async()=>order.push('release')})},
    authorizeEgress:async()=>false,now:()=>1000
  });
  assert.equal((await runner.execute(1)).code,'egress_not_authorized');
  assert.deepEqual(order,['close','release']);
  assert.deepEqual(f.calls.map(x=>x[0]),['open','close']);
});
test('runner retains account lease on browser close failure',async()=>{
  const f=fixture({options:{sessions:{open:async()=>({}),close:async()=>{throw Error('close failed')}}}});
  let releases=0;
  const runner=createCollaborationRunner({
    hubCall:async(tool)=>{
      if(tool==='task_claim')return {task_id:1,status:'claimed',claim_token:'token',lease_expires:90};
      if(tool==='collaboration_context_get')return {run_id:'run',step_key:'answer',provider:'chatgpt',
        access_kind:'native',account_ref:id,deadline:100,max_bytes:16384,items:f.request.items};
      if(tool==='task_block')return {task_id:1,status:'cancelled'};
      throw Error('unexpected tool');
    },
    resolveProfile:async()=>f.profile,resolveAdapter:async()=>f.adapter,
    accountLeases:{acquire:async()=>({release:async()=>{releases++}})},
    authorizeEgress:async()=>false,now:()=>1000
  });
  assert.equal((await runner.execute(1)).status,'blocked');
  assert.equal(releases,0);
});
