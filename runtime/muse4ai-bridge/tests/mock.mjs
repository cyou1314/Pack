import { createHash } from 'node:crypto';
const realTimeout = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms, ...args) => realTimeout(fn, ms === 20000 ? 2 : ms, ...args);
const image = process.env.SCENARIO === 'image';
const approval = process.env.SCENARIO === 'approval';
const recovery = process.env.SCENARIO === 'recovery';
const fakeBytes = Buffer.from('mock-image-bytes');
const t = {id:9876,title:'mock task',body:image?'[M4A-image] draw':approval?'[M4A] 【需批准】 answer':'[M4A] answer',status:'open',assignee:'absent',attempts:0};
const reply = 'mock assistant reply';
let claimed = false, posted = false, delivered = false, jobCalls = 0, approvalReads = 0, creates = 0, idem = null;
const j = (o,headers={}) => new Response(JSON.stringify(o),{status:200,headers:{'content-type':'application/json',...headers}});
globalThis.fetch = async (url, opt={}) => {
  const u = String(url);
  if (u.startsWith('https://raw.githubusercontent.com/')) return new Response('https://mock.invalid/mcp');
  if (u === 'https://mock.invalid/mcp') {
    const req=JSON.parse(opt.body); if (req.method==='notifications/initialized') return new Response(null,{status:202});
    let result={};
    if(req.method==='initialize') result={protocolVersion:'2025-03-26'};
    else if(req.method==='tools/call') {
      const name=req.params.name, args=req.params.arguments;
      if (name==='task_list') result={tasks:[{id:1,status:'open',assignee:'absent',body:'ordinary'},{id:2,status:'open',assignee:'another',body:'[M4A] other'},t]};
      else if(name==='task_claim') { if(args.task_id!==t.id || claimed) throw Error('wrong claim'); claimed=true; t.status='claimed'; t.attempts=1; result={claim_token:'fake-claim'}; }
      else if(name==='task_update') { if(args.claim_token!=='fake-claim') throw Error('invalid token'); t.status='working'; }
      else if(name==='run_start') { result={fence:1}; }
      else if(name==='memory_read') { result={scope:args.scope,text:'',version_id:null,origin:null}; }
      else if(name==='room_post') { result={message_id:10}; }
      else if(name==='room_read_new') { approvalReads++; result={messages:[{id:10+approvalReads,author:approvalReads === 1 ? 'other' : 'dispatcher',body:'批准 任务#9876'}]}; }
      else if(name==='task_complete') {
        if(!posted || delivered || (recovery && creates !== 2) || args.task_id!==t.id || args.claim_token!=='fake-claim') throw Error('invalid completion');
        if(image ? !args.result.includes('/images/task-9876-asset123.png') : args.result!==reply) throw Error('invalid result');
        delivered=true;t.status='review';console.log('MOCK_PASS '+(image?'image':approval?'approval':recovery?'recovery':'chat')+' exact completion and eligible task');realTimeout(()=>process.exit(0),20);
      } else if(name!=='heartbeat') throw Error('unexpected tool '+name);
      result={structuredContent:result};
    } else throw Error('unexpected rpc');
    return j({jsonrpc:'2.0',id:req.id,result},{'mcp-session-id':'fake-session'});
  }
  if(u==='http://127.0.0.1:47821/v1/status') return j({key:{scope:'generate'}});
  if(u==='http://127.0.0.1:47821/v1/jobs' && opt.method==='POST') {
    const body=JSON.parse(opt.body); if(approval && approvalReads < 2) throw Error('approval bypass'); if(body.kind!==(image?'image':'chat') || body.account!=='absent' || !opt.headers['idempotency-key']) throw Error('bad create');creates++; if(idem && idem!==opt.headers['idempotency-key']) throw Error('idempotency changed'); idem=opt.headers['idempotency-key']; posted=true; if(recovery && creates===1) throw Error('mock lost response'); return j({jobs:[{id:'fake-job'}]});
  }
  if(u==='http://127.0.0.1:47821/v1/jobs/fake-job') {
    jobCalls++;return j({state:'completed',result:{text:reply,text_truncated:false},outputs:[{kind:'image',available:true,asset_id:'asset123',mime:'image/png',bytes:fakeBytes.length,sha256:createHash('sha256').update(fakeBytes).digest('hex')}]});
  }
  if(u==='http://127.0.0.1:47821/v1/assets/asset123/content') return new Response(fakeBytes);
  throw Error('unexpected network request '+u);
};
realTimeout(()=>{if(!delivered){console.log('MOCK_FAIL timeout');process.exit(1)}},8000);
