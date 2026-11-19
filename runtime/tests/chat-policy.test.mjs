import {test} from 'node:test';
import assert from 'node:assert/strict';
import {classifyChat, resolveMention, stripReplyMetadata, quotaAllowsChat} from '../chat-policy.mjs';
const names=['dispatcher','owner','absent','alice','alex'];
const msg=(author,body,kind='chat',id=5)=>({id,author,body,kind});
test('owner ordinary, member mentions and watcher matching boundaries',()=>{
 assert.equal(classifyChat(msg('owner','有谁在'),0,names),true);
 for (const s of ['@absent 你好','＠ABSENT','@abs','@al 你好']) assert.equal(classifyChat(msg('owner',s),0,names),false);
 assert.equal(classifyChat(msg('owner','邮件 me@absent.com 或网址 x.com/@absent'),0,names),true);
 assert.deepEqual(resolveMention('@nobody @absent',names),{kind:'hit',name:'absent'});
});
test('dispatcher summons, self, kinds, deleted and watermark',()=>{
 assert.equal(classifyChat(msg('dispatcher','@absent 回话'),0,names),true);
 assert.equal(classifyChat(msg('dispatcher','@alice @absent'),0,names),true);
 assert.equal(classifyChat(msg('dispatcher','@absent-other'),0,names),false);
 assert.equal(classifyChat(msg('dispatcher','你好'),0,names),false);
 assert.equal(classifyChat(msg('absent','@absent'),0,names),false);
 for(const kind of ['task_card','decision','approval_request','report']) assert.equal(classifyChat(msg('owner','有谁在',kind),0,names),false);
 assert.equal(classifyChat(msg('owner','有谁在','chat',5),5,names),false);
 assert.equal(classifyChat({...msg('owner','有谁在'),deleted:true},0,names),false);
});
test('App quote metadata is excluded from mention matching',()=>{
 const data=Buffer.from(JSON.stringify({author:'x',text:'@absent'})).toString('base64url');
 const body=`你好\n\n[pack-reply-v1:${data}]`;
 assert.equal(stripReplyMetadata(body),'你好'); assert.equal(classifyChat(msg('owner',body),0,names),true);
});
test('quota cap fails closed for unknown data',()=>{
 for(const value of [90,100,null,undefined,NaN]) assert.equal(quotaAllowsChat({quota_week_used_pct:value}),false);
 assert.equal(quotaAllowsChat({quota_week_used_pct:89.99}),true);
});
