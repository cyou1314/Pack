import {discoverPublishedHub,createGuardedFetch} from './pack-contracts.mjs';
import {readFileSync,writeFileSync,existsSync} from 'node:fs';
import {randomUUID} from 'node:crypto';
const home=process.env.HOME, path=home+'/pack-runtime/contract-acceptance-state.json';
const disp=JSON.parse(readFileSync(home+'/.pack/config.json','utf8')).token;
const mem=JSON.parse(readFileSync(home+'/pack-runtime/config.json','utf8')).memberToken;
const [action,arg]=process.argv.slice(2);
let state=existsSync(path)?JSON.parse(readFileSync(path,'utf8')):{tests:[]};
function save(){writeFileSync(path,JSON.stringify(state,null,2)+'\n',{mode:0o600});}
async function connect(token){const url=await discoverPublishedHub();let sid,id=0;async function rpc(method,params,notification=false){const r=await fetch(url,{method:'POST',signal:AbortSignal.timeout(20000),headers:{'content-type':'application/json',accept:'application/json, text/event-stream',authorization:'Bearer '+token,...sid?{'mcp-session-id':sid}:{}},body:JSON.stringify({jsonrpc:'2.0',method,params,...notification?{}:{id:++id}})});sid=r.headers.get('mcp-session-id')||sid;if(!r.ok)throw Error('HTTP '+r.status);if(notification)return;let t=await r.text(),raw=t.trim().startsWith('{')?t:t.split('\n').find(s=>s.startsWith('data:'))?.slice(5),v=JSON.parse(raw);if(v.error)throw Error(v.error.message||'rpc error');return v.result;}await rpc('initialize',{protocolVersion:'2025-03-26',capabilities:{},clientInfo:{name:'pack-acceptance',version:'1'}});await rpc('notifications/initialized',{},true);return async(name,args={})=>{let r=await rpc('tools/call',{name,arguments:args});if(r.isError)throw Error((r.content||[]).map(x=>x.text).join(' '));return r.structuredContent??JSON.parse(r.content[0].text);};}
try{
 const d=await connect(disp);
 if(action==='create'){
  const isUrl=arg?.endsWith('-url'), isMemory=arg?.endsWith('-memory'), route=isUrl?arg.slice(0,-4):isMemory?arg.slice(0,-7):arg;
  if(!['ego','engine','bridge'].includes(route))throw Error('unknown route');
  const tag=route==='engine'?'[ENG] ':route==='bridge'?'[M4A] ':'';
  const memoryKey=isMemory?'contract-test-'+route+'-'+randomUUID().slice(0,8):null;
  const item={route:arg,title:'契约验收-'+arg+'-'+randomUUID().slice(0,8),body:tag+(isUrl?'验收网址 https://example.com/；请不要打开网址，':'')+(memoryKey?`【沉淀记忆:${memoryKey}】 `:'')+'请只回复：契约验收完成',memoryKey,id:null};state.tests.push(item);save();
  try{const r=await d('task_create',{room:'pack-main',title:item.title,body:item.body,assignee_name:'absent'});item.id=r.task_id;save();console.log(JSON.stringify({route:arg,id:item.id,status:r.status}));}
  catch(e){console.log('create uncertain; use reconcile; reason='+e.message);process.exitCode=1;}
 } else if(action==='transfer-prepare'){
  const route=arg||'engine';if(!['ego','engine','bridge'].includes(route))throw Error('unknown transfer route');
  const tag=route==='engine'?'[ENG] ':route==='bridge'?'[M4A] ':'';
  const item={route:route+'-transfer',title:'契约转交验收-'+route+'-'+randomUUID().slice(0,8),body:tag+'请只回复：转交验收完成',id:null};state.tests.push(item);save();let handed=false;
  try {
    const created=await d('task_create',{room:'pack-main',title:item.title,body:item.body,assignee_name:'dispatcher'});item.id=created.task_id;save();
    await d('task_claim',{task_id:item.id});
    const transfer=await d('task_transfer',{task_id:item.id,claim_token:'',to_member:'absent',reason:'契约测试'});handed=true;
    console.log('transfer prepared:',JSON.stringify({task_id:item.id,transfer_status:transfer.transfer_status,attempts:transfer.attempts}));
  } finally {if(!handed&&item.id){try{await d('task_cancel',{task_id:item.id});console.log('transfer test cancelled');}catch{console.log('transfer cleanup unconfirmed');}}}
 } else if(action==='transfer-status'){
  const item=state.tests.findLast(x=>x.route.endsWith('-transfer') && x.id);if(!item)throw Error('no transfer test');
  const transfers=await d('task_transfers_list',{task_id:item.id});
  const tasks=await d('task_list');const task=(tasks.tasks||[]).find(t=>t.id===item.id);
  const runs=await d('run_list',{task_id:item.id});
  console.log('transfer state:',JSON.stringify({task_id:item.id,status:task?.status,attempts:task?.attempts,result:task?.result,transfers:transfers.transfers?.map(x=>({from:x.from_member,to:x.to_member,status:x.status})),runs:runs.runs?.map(x=>({fence:x.fence,status:x.status,outcome:x.outcome}))}));
 } else if(action==='memory-test'){
  const m=await connect(mem), key='contract-test-'+randomUUID().slice(0,8); let written=false;
  try {
    const result=await m('memory_write',{scope:'private',key,text:'测试：契约验收完成',origin:'external',source_refs:[{type:'task',id:13}]});written=true;
    const versions=await m('memory_versions',{scope:'private',key});
    console.log('memory write/version:',JSON.stringify({key,version_id:result.version_id,versions:versions.versions?.length}));
  } finally {if(written){const removed=await m('memory_write',{scope:'private',key,text:null,origin:'external'});console.log('memory cleanup:',JSON.stringify({key,deleted:removed.deleted,version_id:removed.version_id}));}}
 } else if(action==='memory-verify'){
  const m=await connect(mem);
  for(const item of state.tests.filter(x=>x.memoryKey)){
    const versions=await m('memory_versions',{scope:'private',key:item.memoryKey});
    console.log(JSON.stringify({route:item.route,versions:(versions.versions||[]).map(v=>({version_id:v.version_id??v.id,origin:v.origin}))}));
  }
 } else if(action==='memory-cleanup'){
  const m=await connect(mem);
  for(const item of state.tests.filter(x=>x.memoryKey && !x.memoryCleaned)){
    const versions=await m('memory_versions',{scope:'private',key:item.memoryKey});
    console.log('memory versions:',JSON.stringify({route:item.route,key:item.memoryKey,count:versions.versions?.length}));
    const current=await m('memory_read',{scope:'private',key:item.memoryKey});
    if(current.text){const removed=await m('memory_write',{scope:'private',key:item.memoryKey,text:null,origin:'external'});console.log('memory tombstone:',JSON.stringify({route:item.route,version_id:removed.version_id,deleted:removed.deleted}));}
    item.memoryCleaned=true;save();
  }
 } else if(action==='egress-finish'){
  const item=state.tests.findLast(x=>x.route==='egress' && x.claimToken && x.requestId);
  if(!item)throw Error('no pending egress test');
  const m=await connect(mem), messages=await d('room_read_new',{room:'pack-main',since_id:item.requestId,limit:200});
  const decisions=(messages.messages||[]).filter(x=>x.author==='dispatcher' && new RegExp('^(批准|拒绝)\\s*任务#'+item.id+'(?!\\d)').test(x.body||''));
  const decision=decisions.at(-1);if(!decision)throw Error('approval not yet recorded');
  try {
    if(decision.body.startsWith('拒绝')){console.log('egress test refused; no fetch');}
    else {
      const guard=createGuardedFetch({hub:{call:m},taskId:item.id,requestApproval:async url=>new URL(url).host===new URL(item.testUrl).host});
      const response=await guard(item.testUrl,{signal:AbortSignal.timeout(15000)});
      console.log('guarded fetch:',JSON.stringify({status:response.status,url:response.url}));
    }
  } finally {
    const cleaned=await d('task_cancel',{task_id:item.id});delete item.claimToken;save();console.log('egress cleanup:',cleaned.status);
  }
 } else if(action==='egress-prepare'){
  const item={route:'egress',title:'契约出口校验-'+randomUUID().slice(0,8),body:'验证未点名站点出口批准',id:null};state.tests.push(item);save();
  const m=await connect(mem);let ready=false;
  try {
    const created=await d('task_create',{room:'pack-main',title:item.title,body:item.body,assignee_name:''});item.id=created.task_id;save();
    const claim=await m('task_claim',{task_id:item.id});item.claimToken=claim.claim_token;save();
    await m('run_start',{task_id:item.id,claim_token:claim.claim_token});
    item.testUrl='https://example.com/';const decision=await m('egress_check',{url:item.testUrl,task_id:item.id});
    console.log('egress decision:',JSON.stringify(decision));
    if(decision.decision!=='ask')throw Error('expected ask');
    const request=await m('room_post',{room:'pack-main',kind:'approval_request',body:`任务#${item.id}「${item.title}」需要群主批准：抓取 ${item.testUrl}`});
    item.requestId=request.message_id;save();ready=true;console.log('approval request:',JSON.stringify({task_id:item.id,message_id:request.message_id}));
  } finally {if(!ready&&item.id){try{await d('task_cancel',{task_id:item.id});delete item.claimToken;save();}catch{console.log('cleanup unconfirmed');}}}
 } else if(action==='manual'){
  const item={route:'manual',title:'契约校验-手动-'+randomUUID().slice(0,8),body:'手动验证信封和 fence',id:null};state.tests.push(item);save();
  const m=await connect(mem);
  try {
    const created=await d('task_create',{room:'pack-main',title:item.title,body:item.body,assignee_name:''}); item.id=created.task_id;save();
    const claim=await m('task_claim',{task_id:item.id});const run=await m('run_start',{task_id:item.id,claim_token:claim.claim_token});console.log('manual task',item.id,'fence',run.fence);
    try {await m('run_start',{task_id:item.id,claim_token:claim.claim_token});console.log('ERROR duplicate run accepted');}
    catch(e){console.log('duplicate run error:',e.message);}
    try {await m('task_complete',{task_id:item.id,claim_token:claim.claim_token,result:'验收',envelope:{summary:'验收'}});console.log('ERROR invalid envelope accepted');}
    catch(e){console.log('invalid envelope error:',e.message);}
    const delivered=await m('task_complete',{task_id:item.id,claim_token:claim.claim_token,result:'验收',envelope:{verdict:'done',summary:'验收',artifacts:[],self_check:{ran:true,evidence:'manual acceptance'},known_issues:[],followups:[]}});
    console.log('corrected envelope:',JSON.stringify(delivered));
  } finally {if(item.id){try{const cleaned=await d('task_cancel',{task_id:item.id});console.log('manual cleanup:',cleaned.status);}catch{console.log('manual cleanup: unconfirmed');}}}
 } else if(action==='reconcile'){
  const r=await d('task_list');for(const x of state.tests.filter(t=>!t.id)){const found=r.tasks.filter(t=>t.title===x.title);if(found.length===1)x.id=found[0].id;}save();console.log(JSON.stringify(state.tests.map(({route,id,title})=>({route,id,title}))));
 } else if(action==='status'){
  const r=await d('task_list');for(const x of state.tests){const t=r.tasks.find(t=>t.id===x.id);if(t){let runs=await d('run_list',{task_id:t.id});console.log(JSON.stringify({route:x.route,id:t.id,status:t.status,attempts:t.attempts,result:t.result,envelope:t.envelope,runs:runs.runs?.map(v=>({fence:v.fence,status:v.status,outcome:v.outcome}))}));}}
 } else if(action==='cancel-id'){
  const item=state.tests.find(x=>x.id===Number(arg));if(!item)throw Error('test task not found');const r=await d('task_cancel',{task_id:item.id});delete item.claimToken;save();console.log(JSON.stringify({id:item.id,status:r.status}));
 } else if(action==='cancel'){
  for(const x of state.tests.filter(t=>t.id)){const r=await d('task_cancel',{task_id:x.id});delete x.claimToken;save();console.log(JSON.stringify({id:x.id,status:r.status}));}
 } else if(action==='active'){const r=await d('task_list',{assignee_name:'absent'});console.log(JSON.stringify((r.tasks||[]).filter(x=>['claimed','working'].includes(x.status)).map(x=>({id:x.id,status:x.status}))));
 } else if(action==='rules'){const r=await d('egress_rule_list');console.log(JSON.stringify(r));
 } else if(action==='list'){console.log(JSON.stringify(state.tests.map(({route,id,title})=>({route,id,title}))));}
 else throw Error('unknown action');
}catch(e){console.error('acceptance error:',e.message);process.exitCode=1;}
