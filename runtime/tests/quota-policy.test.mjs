import {test} from 'node:test';import assert from 'node:assert/strict';
import {parseQuotaSnapshot,quotaDue} from '../quota-policy.mjs';
test('quota accepts explicit values only and does not guess missing values',()=>{
 assert.deepEqual(parseQuotaSnapshot({week_used_pct:87.4,week_reset_at:1800000000000,extra_tokens_left:1234}),{week_used_pct:87.4,week_reset_at:1800000000,extra_tokens_left:1234});
 assert.deepEqual(parseQuotaSnapshot({week_used_pct:'30',week_reset_at:null,extra_tokens_left:'2M'}),{});
 assert.deepEqual(parseQuotaSnapshot({week_used_pct:101,extra_tokens_left:-1}),{});
});
test('quota is due after 11 minutes or a completion but never while busy',()=>{
 const t=1800000000000;
 assert.equal(quotaDue(t,t-10*60000,t-60001,false,false),false);
 assert.equal(quotaDue(t,t-11*60000,t-60001,false,false),true);
 assert.equal(quotaDue(t,t-1000,t-60001,true,false),true);
 assert.equal(quotaDue(t,t-12*60000,t-60001,false,true),false);
 assert.equal(quotaDue(t,t-12*60000,t-1000,false,false),false);
});
