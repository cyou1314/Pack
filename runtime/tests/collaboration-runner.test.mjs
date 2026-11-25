import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createCollaborationRunner,validateContext,destinationFor} from '../collaboration-runner.mjs';

const id='12345678-1234-4234-8234-123456789abc';
const now=1000;
function fixture(mode='api',overrides={}) {
  const native=mode==='native';
  const provider=native?'chatgpt':'openai';
  const context={run_id:'fixture-run',step_key:'answer',provider,access_kind:mode,
    account_ref:id,deadline:100,max_bytes:16384,items:[
      {kind:'owner_message',source_id:1,sha256:'a'.repeat(64),text:'Do the task'}
    ]};
  const profile={id:id.toUpperCase(),providerID:provider,mode:native?'nativeAccount':'officialAPI',
    credentialReference:{kind:native?'nativeSession':'apiKey',vaultKey:id},
    contextBoundary:native?'providerAccount':'packWorkspace',
    memoryBoundary:native?'providerManaged':'disabled',enabledCapabilities:['chat']};
  const calls=[];let invocations=0,reads=0;
  const options={
    now:()=>now,timeoutMs:50,
    hubCall:async (tool,args)=>{
      calls.push({tool,args});
      switch(tool){
        case 'task_claim':return {task_id:args.task_id,status:'claimed',claim_token:'fixture',lease_expires:90};
        case 'collaboration_context_get':reads++;return structuredClone(context);
        case 'run_start':return {task_id:args.task_id,status:'running',fence:1};
        case 'task_complete':return {task_id:args.task_id,status:'review'};
        case 'task_block':return {task_id:args.task_id,status:'cancelled'};
        default:throw Error('unexpected tool');
      }
    },
    resolveProfile:async()=>profile,
    resolveAdapter:async()=>({
      verifyAccount:async()=>true,
      execute:async request=>{invocations++;assert.equal(request.destination.account_ref,id);
        assert.ok(Object.isFrozen(request.items[0]));
        return {status:'succeeded',text:'result',usage:native?null:{input_tokens:10,output_tokens:4}};}
    }),
    authorizeEgress:async ({request,sha256})=>{
      assert.equal(request.destination.provider,provider);assert.match(sha256,/^[a-f0-9]{64}$/);
      return true;
    },
    ...overrides
  };
  return {runner:createCollaborationRunner(options),options,context,profile,calls,
    count:()=>invocations,reads:()=>reads};
}

test('API and native share the contract without importing account memory or inventing usage',async()=>{
  for (const mode of ['api','native']){
    const f=fixture(mode);
    const result=await f.runner.execute(10);
    assert.deepEqual(result,{status:'review',usage:mode==='api'?{input_tokens:10,output_tokens:4}:null});
    assert.equal(f.count(),1);
    assert.deepEqual(f.calls.map(x=>x.tool),['task_claim','collaboration_context_get','run_start',
      'collaboration_context_get','task_complete']);
    assert.deepEqual(f.calls.at(-1).args,{task_id:10,claim_token:'fixture',result:'result'});
  }
});
test('reject foreign account, mode, secret reference and invalid context',async()=>{
  const f=fixture();
  const c=validateContext(f.context);
  assert.throws(()=>destinationFor(c,{...f.profile,id:'00000000-0000-0000-0000-000000000000'}));
  assert.throws(()=>destinationFor(c,{...f.profile,credentialReference:{kind:'apiKey',vaultKey:'secret'}}));
  assert.throws(()=>destinationFor(c,{...f.profile,mode:'nativeAccount'}));
  assert.throws(()=>validateContext({...f.context,items:[{...f.context.items[0],kind:'private'}]}));
  assert.throws(()=>validateContext({...f.context,items:[{...f.context.items[0],text:'中'.repeat(6000)}]}));
  f.profile.credentialReference.vaultKey='wrong';
  assert.equal((await f.runner.execute(10)).status,'blocked');
  assert.equal(f.count(),0);
});
test('egress denial and missing account never start a Provider',async()=>{
  for(const overrides of [{authorizeEgress:async()=>false},{resolveAdapter:async()=>null}]){
    const f=fixture('api',overrides);
    assert.equal((await f.runner.execute(10)).status,'blocked');
    assert.equal(f.count(),0);
    assert.ok(!f.calls.some(x=>x.tool==='run_start'));
  }
});
test('source change after run registration blocks delivery',async()=>{
  const f=fixture();
  const call=f.options.hubCall;
  const runner=createCollaborationRunner({...f.options,hubCall:async (tool,args)=>{
    const value=await call(tool,args);
    if(tool==='collaboration_context_get' && f.reads()===2) value.items[0].text='edited';
    return value;
  }});
  assert.equal((await runner.execute(10)).code,'context_changed');
  assert.equal(f.count(),0);
});
test('timeout aborts and quarantines the account without retry',async()=>{
  const f=fixture();let count=0,aborted=false;
  const runner=createCollaborationRunner({...f.options,timeoutMs:5,resolveAdapter:async()=>({
    verifyAccount:async()=>true,
    execute:async (_,{signal})=>{count++;signal.addEventListener('abort',()=>{aborted=true;});
      return new Promise(()=>{});}
  })});
  assert.equal((await runner.execute(10)).code,'provider_outcome_unknown');
  assert.equal(aborted,true);
  assert.equal((await runner.execute(11)).code,'account_unavailable');
  assert.equal(count,1);
});
test('uncertain delivery is not retried or followed by block',async()=>{
  const f=fixture(),call=f.options.hubCall;
  const runner=createCollaborationRunner({...f.options,hubCall:async (tool,args)=>{
    const value=await call(tool,args);
    if(tool==='task_complete')throw Error('transport lost after commit');
    return value;
  }});
  assert.equal((await runner.execute(10)).status,'deliveryUnknown');
  assert.equal(f.count(),1);
  assert.equal(f.calls.filter(x=>x.tool==='task_complete').length,1);
  assert.equal(f.calls.filter(x=>x.tool==='task_block').length,0);
});
test('structured Provider failure preserves metering and never retries',async()=>{
  const f=fixture('api',{resolveAdapter:async()=>({verifyAccount:async()=>true,
    execute:async()=>({status:'failed',code:'quota',usage:{input_tokens:0,output_tokens:0}})})});
  assert.deepEqual(await f.runner.execute(10),{status:'blocked',code:'quota',
    usage:{input_tokens:0,output_tokens:0}});
  assert.equal(f.calls.at(-1).args.fail_class,'quota');
});
