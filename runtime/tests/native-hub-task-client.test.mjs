import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createNativeHubTaskClient} from '../browser-driver/native-hub-task-client.mjs';
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
