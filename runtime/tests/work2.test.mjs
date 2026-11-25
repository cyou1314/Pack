import {test} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {listActiveTasks, approvalDecision, validHost, isHubNetworkFailure, isHubRetryableFailure} from '../runtime-helpers.mjs';
import {classifyChat} from '../chat-policy.mjs';

test('active tasks paginate beyond 100 and preserve room and ownership', async()=>{
 const tasks=Array.from({length:205},(_,i)=>({id:i+1,status:'working',assignee:'absent',room:'other'}));
 const offsets=[];
 const result=await listActiveTasks(async (_,{offset,status,assignee_name})=>{assert.equal(status,'open,claimed,working,review');assert.equal(assignee_name,'absent');offsets.push(offset);return {tasks:tasks.slice(offset,offset+100)};},{assignee_name:'absent'});
 assert.deepEqual(offsets,[0,100,200]);assert.equal(result.find(x=>x.id===205).room,'other');
});
test('approval decisions are anchored and handle both directions',()=>{
 assert.equal(approvalDecision('不要批准 任务#5'),null);
 assert.deepEqual(approvalDecision('拒绝 任务#5'),{decision:'拒绝',taskId:5});
 assert.deepEqual(approvalDecision('批准 任务#5'),{decision:'批准',taskId:5});
 assert.equal(approvalDecision('批准 任务#50').taskId,50);
});
test('templates and watcher receipts do not trigger chat',()=>{
 const names=['owner','dispatcher','absent'];
 for(const body of ['模板：图 你好','模板:图 你好']) assert.equal(classifyChat({id:1,kind:'chat',author:'owner',body},0,names),false);
 for(const body of ['已转任务 #4 → @absent','已按模板「图」开卡 #5 → @absent','「@a」对上了 2 个成员（@absent、@alice），写全名我再转任务。']) assert.equal(classifyChat({id:1,kind:'chat',author:'dispatcher',body},0,names),false);
});
test('malformed URLs are ignored',()=>{assert.equal(validHost('http://foo:bar/'),null);assert.equal(validHost('https://example.com/a'),'example.com')});
test('socket failure is retryable and HTTP business response is not', async()=>{
 let requests=0;const server=http.createServer((req,res)=>{requests++;if(requests===2){req.socket.destroy();return;}res.end('ok')});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 try {const url=`http://127.0.0.1:${server.address().port}`;assert.equal(await (await fetch(url)).text(),'ok');let retried=false;try {await fetch(url)} catch(e){assert.equal(isHubNetworkFailure(e),true);retried=true;}assert.equal(retried,true);assert.equal(await (await fetch(url)).text(),'ok');assert.equal(isHubNetworkFailure(Error('tool error 400')),false)} finally {server.closeAllConnections();await new Promise(r=>server.close(r))}
});

test('Hub retries transient HTTP failures but not business errors',()=> {
 assert.equal(isHubRetryableFailure(Error('hub HTTP 503')),true);
 assert.equal(isHubRetryableFailure(Error('hub HTTP 429')),true);
 assert.equal(isHubRetryableFailure(Error('hub HTTP 400')),false);
 assert.equal(isHubRetryableFailure(Error('invalid_envelope')),false);
 assert.equal(isHubRetryableFailure(new TypeError('fetch failed')),true);
});
