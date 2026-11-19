const interval=globalThis.setInterval;globalThis.setInterval=(fn,ms,...args)=>interval(fn,ms===10000?15:ms,...args);
const timer=globalThis.setTimeout;globalThis.setTimeout=(fn,ms,...args)=>timer(fn,ms===20000?5:ms,...args);
const scenario=process.env.SCENARIO||'chat';const outgoing=scenario==='outgoing';let updates=0;const lost=scenario==='lost';const transfer=scenario==='transfer',egress=scenario==='egress';
let task={id:9876,title:'mock task',body:outgoing?'【允许转交】 reply':egress?'check https://external.example/a':'reply',status:transfer?'working':'open',assignee:'absent',attempts:transfer?1:0,lease_expires:transfer?null:1},sent=false;
const j=(x,h={})=>new Response(JSON.stringify(x),{status:200,headers:{'content-type':'application/json',...h}});
globalThis.fetch=async(url,opt={})=>{
 const u=String(url);
 if(u.startsWith('https://api.github.com/repos/cyou1314/Pack/git/ref/heads/main'))return new Response(JSON.stringify({object:{sha:'a'.repeat(40)}}),{headers:{'content-type':'application/json'}});if(u.startsWith('https://raw.githubusercontent.com/'))return new Response('https://mock.invalid/mcp');
 if(u!=='https://mock.invalid/mcp')throw Error('unexpected_url');
 const req=JSON.parse(opt.body);if(req.method==='notifications/initialized')return new Response(null,{status:202});let r={};
 if(req.method==='initialize')r={};else if(req.method==='tools/call'){
  const n=req.params.name,a=req.params.arguments;
  if(n==='heartbeat'){}else if(n==='room_read_new')r={messages:[]};else if(n==='room_list')r={rooms:[{name:'pack-main',last_message_id:0}]};else if(n==='task_list')r={tasks:[task]};
  else if(n==='task_accept_transfer'){if(!transfer||task.lease_expires!==null)throw Error('wrong_accept');task.lease_expires=1234567890;r={claim_token:'synthetic-claim'};}
  else if(n==='egress_check'){if(!egress)throw Error('unexpected_egress');r={decision:'allow'};}
  else if(n==='task_claim'){if(task.status!=='open')throw Error('wrong_claim');task={...task,status:'claimed',attempts:1};r={claim_token:'synthetic-claim'};}
  else if(n==='task_update'){if(a.claim_token!=='synthetic-claim')throw Error('wrong_holder');if(lost&&++updates>1){sent=true;console.log('MOCK_PASS holder loss reported');timer(()=>process.exit(0),60);return j({jsonrpc:'2.0',id:req.id,result:{isError:true,content:[{text:'not the current holder (claim_token mismatch)'}]}},{'mcp-session-id':'synthetic'});}task.status='working';}
  else if(n==='member_list' && outgoing)r={members:[{name:'other',role:'member'}]};else if(n==='task_transfer' && outgoing){if(a.to_member!=='other'||a.claim_token!=='synthetic-claim')throw Error('bad_transfer');sent=true;console.log('MOCK_PASS outgoing transfer');timer(()=>process.exit(0),30);r={transfer_status:'pending',to_member:'other'};}else if(n==='run_start')r={fence:1};else if(n==='memory_read')r={scope:a.scope,text:'',version_id:null,origin:null};
  else if(n==='task_complete'){if(sent||a.claim_token!=='synthetic-claim'||a.result!=='mock reply'||a.envelope?.verdict!=='done')throw Error('wrong_complete');sent=true;task.status='review';console.log('MOCK_PASS ego claim fence memory and envelope');timer(()=>process.exit(0),30);}
  else throw Error('unexpected_tool_'+n);r={structuredContent:r};
 }else throw Error('unexpected_rpc');return j({jsonrpc:'2.0',id:req.id,result:r},{'mcp-session-id':'synthetic'});
};
timer(()=>{if(!sent){console.log('MOCK_FAIL timeout');process.exit(1)}},8000);
