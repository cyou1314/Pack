import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createNativeHubTaskClient,nativeTaskApprovals} from '../browser-driver/native-hub-task-client.mjs';
const reply=(frame,result)=>Buffer.from(JSON.stringify({version:1,sequence:JSON.parse(frame).sequence,result}));
test('private client sends only bounded correlated task frames and never credentials',async()=>{
  const calls=[];
  const client=createNativeHubTaskClient({taskId:1,exchange:async frame=>{
    calls.push(JSON.parse(frame));return reply(frame,{task_id:1,status:'claimed'});
  }});
  await assert.rejects(client('room_post',{task_id:1}),/unavailable/);
  await assert.rejects(client('task_claim',{task_id:2}),/unavailable/);
  assert.deepEqual(await client('task_claim',{task_id:1}),{task_id:1,status:'claimed'});
  assert.deepEqual(calls,[{version:1,sequence:1,tool:'task_claim',arguments:{task_id:1}}]);
});
test('unknown claim and terminal response are never retried',async()=>{
  for(const tool of ['task_claim','task_complete','task_block']){
    let calls=0;
    const client=createNativeHubTaskClient({taskId:1,exchange:async()=>{calls++;throw Error('private error');}});
    await assert.rejects(client(tool,{task_id:1}),/outcome_unconfirmed/);
    await assert.rejects(client(tool,{task_id:1}),/unavailable/);
    assert.equal(calls,1);
  }
});
test('mismatched response sequence fails closed',async()=>{
  const client=createNativeHubTaskClient({taskId:1,exchange:async()=>Buffer.from('{"version":1,"sequence":2,"result":{}}')});
  await assert.rejects(client('task_claim',{task_id:1}),/outcome_unconfirmed/);
});

test('approval helper binds selected task and exact send text without forwarding secrets',async()=>{
  const frames=[];
  const hub=createNativeHubTaskClient({taskId:1,exchange:async frame=>{
    frames.push(JSON.parse(frame));return reply(frame,{approved:true});
  }});
  const approval=nativeTaskApprovals(hub,1);
  assert.equal(await approval.authorizeEgress({request:{task_id:2}}),false);
  assert.equal(await approval.authorizeEgress({request:{task_id:1}}),true);
  assert.equal(await approval.authorizeNativeSend({taskID:1,origin:'https://chatgpt.com',conversationID:'fresh',
    text:'fixture',textSHA256:'a'.repeat(64),deadline:99}),true);
  assert.deepEqual(frames.map(f=>f.tool),['native_egress_authorize','native_send_authorize']);
  assert.equal(frames[1].arguments.text,'fixture');
});
