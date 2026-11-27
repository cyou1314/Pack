import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {createBrowserSessions} from './playwright.mjs';
import {createChatGPTPageDriver} from './chatgpt-page-driver.mjs';
import {enrollNativeIdentity,expectedNativeAccountProof} from './native-identity.mjs';
import {createNativeAccountAdapter} from './native-account-adapter.mjs';
import {AccountExecutionLeases} from '../account-execution-leases.mjs';
import {createChatGPTCollaborationHost} from './chatgpt-collaboration-host.mjs';

// Synthetic DOM contract, NOT live ChatGPT selectors. Every request is
// fulfilled or blocked locally. No real provider credentials or network.
const surface=Object.freeze(Object.fromEntries([
  ...['ready','composer','send','turn','user','assistant','text','busy','complete','failure']
    .map(k=>[k,'[data-pack-fixture-'+k+'="yes"]']),
  ['messageIdAttribute','data-pack-fixture-id']
]));
const html=({partial=false,duplicate=false,old=false}={})=>String.raw`<!doctype html><meta charset="utf-8">
<div data-pack-fixture-ready="yes">Fixture ready</div>
<div id="turns">${old?'<article data-pack-fixture-turn="yes" data-pack-fixture-user="yes" data-pack-fixture-id="old"><span data-pack-fixture-text="yes">old</span></article>':''}</div>
<textarea data-pack-fixture-composer="yes"></textarea>
<button data-pack-fixture-send="yes">Send</button>
${duplicate?'<button data-pack-fixture-send="yes">Other send</button>':''}
<script>
let clicks=0;
document.querySelector('[data-pack-fixture-send]').onclick=()=>{
  clicks++; document.documentElement.dataset.clicks=String(clicks);
  const composer=document.querySelector('textarea'), value=composer.value;
  const turns=document.getElementById('turns');
  const add=(role,id,text)=>{
    const node=document.createElement('article'); node.setAttribute('data-pack-fixture-turn','yes');
    node.setAttribute('data-pack-fixture-'+role,'yes'); node.setAttribute('data-pack-fixture-id',id);
    const body=document.createElement('div'); body.setAttribute('data-pack-fixture-text','yes');
    body.style.whiteSpace='pre-wrap'; body.textContent=text;node.append(body);turns.append(node);return node;
  };
  add('user','user-1',value);composer.value='';
  history.pushState({},'','/c/fixture-conversation');
  const busy=document.createElement('span');busy.textContent='Generating';busy.setAttribute('data-pack-fixture-busy','yes');document.body.append(busy);
  const assistant=add('assistant','assistant-1','Partial answer');
  setTimeout(()=>{
    busy.remove();assistant.firstChild.textContent='Final answer';
    if(!${partial}){const done=document.createElement('span');done.setAttribute('data-pack-fixture-complete','yes');done.textContent='Complete';assistant.append(done);}
  },100);
};
</script>`;

const ref='12345678-1234-4234-8234-123456789abc';
const binding={account_ref:ref,provider:'chatgpt',access_kind:'native',model_id:null,memory_boundary:'providerManaged'};
const profile={id:ref,mode:'nativeAccount',providerID:'chatgpt',credentialReference:{kind:'nativeSession',vaultKey:ref},
  contextBoundary:'providerAccount',memoryBoundary:'providerManaged',enabledCapabilities:['chat']};

async function fixture(t,options={}) {
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'pack-chatgpt-driver-'));
  const browserRoot=path.join(root,'browser'),leaseRoot=path.join(root,'leases');
  await fs.mkdir(browserRoot,{mode:0o700});await fs.mkdir(leaseRoot,{mode:0o700});
  const sessions=await createBrowserSessions({root:browserRoot,headless:true});
  const lease=await new AccountExecutionLeases({root:leaseRoot}).acquire(ref);
  t.after(async()=>{
    if(sessions.status(profile)==='open')await sessions.close(profile);
    await lease.release();
    await fs.rm(root,{recursive:true,force:true});
  });
  const context=await sessions.open(profile);
  await context.route('**/*',async route=>{
    if(route.request().isNavigationRequest()&&route.request().url()==='https://chatgpt.com/')
      return route.fulfill({status:200,contentType:'text/html',body:html(options)});
    return route.abort();
  });
  const page=context.pages()[0]||await context.newPage();
  await page.goto('https://chatgpt.com/');
  const account={value:'fixture-account'};
  const observeIdentity=async({page})=>({url:page.url(),state:'ready',busy:false,
    accountID:account.value,workspaceID:'fixture-workspace',modelID:null,memoryBoundary:'providerManaged',
    modelVerified:true,memoryVerified:true});
  await enrollNativeIdentity({root,binding,observe:()=>observeIdentity({page}),confirm:async()=>true});
  const driver=createChatGPTPageDriver({context,page,root,binding,surface,observeIdentity,
    timeoutMs:options.partial?1800:8000,pollMs:50});
  return {root,context,page,driver,account};
}

test('Chromium fixture: complete native adapter path, exact text and one send', {timeout:60000},async t=>{
  const f=await fixture(t);
  let approved;
  const adapter=createNativeAccountAdapter({profile,destination:binding,
    sessions:{open:async()=>f.context,close:async()=>{}},
    createPageDriver:async()=>f.driver,expectedAccountProof:await expectedNativeAccountProof({root:f.root,binding}),
    authorizeNativeSend:async value=>{approved=value;return true;}});
  assert.equal(await adapter.verifyAccount(binding),true);
  const result=await adapter.execute({version:1,task_id:1,run_id:'run',step_key:'answer',role:'answer',
    max_output_tokens:4096,destination:binding,deadline:Date.now()/1000+30,
    items:[{kind:'owner_message',source_id:1,sha256:'a'.repeat(64),text:'approved fixture task'}]});
  assert.deepEqual(result,{status:'succeeded',text:'Final answer',usage:null});
  assert.equal(await f.page.locator('[data-pack-fixture-user]').innerText(),approved.text);
  assert.equal(await f.page.locator('html').getAttribute('data-clicks'),'1');
  await adapter.close();
});

test('Chromium fixture: old turns, duplicate send and account switch fail closed', {timeout:90000},async t=>{
  const f=await fixture(t,{duplicate:true});
  const draft=await f.driver.prepareConversation();
  await assert.rejects(f.driver.sendOnce({conversationID:draft.conversationID,text:'fixture'}),/composer_changed/);
  assert.equal(await f.page.locator('html').getAttribute('data-clicks'),null);
  f.account.value='changed';
  await assert.rejects(f.driver.inspect(),/native_identity_unavailable/);
});
test('Chromium fixture: existing conversation cannot be treated as fresh', {timeout:60000},async t=>{
  const f=await fixture(t,{old:true});
  await assert.rejects(f.driver.prepareConversation(),/not_fresh/);
  assert.equal(await f.page.locator('html').getAttribute('data-clicks'),null);
});
test('Chromium fixture: stable partial reply without terminal marker is never delivered', {timeout:60000},async t=>{
  const f=await fixture(t,{partial:true});
  const draft=await f.driver.prepareConversation();
  const sent=await f.driver.sendOnce({conversationID:draft.conversationID,text:'fixture'});
  await assert.rejects(f.driver.waitForReply(sent),/timeout/);
  await assert.rejects(f.driver.sendOnce({conversationID:draft.conversationID,text:'fixture'}),/invalid_send/);
  assert.equal(await f.page.locator('html').getAttribute('data-clicks'),'1');
});

test('Chromium fixture: dedicated host claims, approves, sends once, delivers and releases both leases', {timeout:90000},async t=>{
  const parent=await fs.mkdtemp(path.join(os.tmpdir(),'pack-chatgpt-host-smoke-'));
  const directory=path.join(parent,'agents-v2');
  await fs.mkdir(directory,{mode:0o700});
  const browserRoot=path.join(directory,'native-sessions'),leaseRoot=path.join(directory,'execution-leases');
  await fs.mkdir(browserRoot,{mode:0o700});await fs.mkdir(leaseRoot,{mode:0o700});
  await fs.writeFile(path.join(directory,'agent-registry.json'),JSON.stringify({schemaVersion:1,profiles:[profile]}),{mode:0o600});
  const sessions=await createBrowserSessions({root:browserRoot,headless:true});
  const manager=new AccountExecutionLeases({root:leaseRoot});
  t.after(async()=>{
    if(sessions.status(profile)==='open')await sessions.close(profile);
    await fs.rm(parent,{recursive:true,force:true});
  });
  const observeIdentity=async({page})=>({url:page.url(),state:'ready',busy:false,
    accountID:'fixture-account',workspaceID:'fixture-workspace',modelID:null,
    memoryBoundary:'providerManaged',modelVerified:true,memoryVerified:true});
  async function open(profile){
    const context=await sessions.open(profile);
    await context.route('**/*',route=>{
      if(route.request().isNavigationRequest()&&route.request().url()==='https://chatgpt.com/')
        return route.fulfill({status:200,contentType:'text/html',body:html()});
      return route.abort();
    });
    const page=context.pages()[0]||await context.newPage();
    await page.goto('https://chatgpt.com/');
    return context;
  }
  const enrollmentLease=await manager.acquire(ref);
  try{
    const context=await open(profile),page=context.pages()[0];
    await enrollNativeIdentity({root:directory,binding,observe:()=>observeIdentity({page}),confirm:async()=>true});
  }finally{await sessions.close(profile);await enrollmentLease.release();}
  let browser,approved,clicks,egress=0;
  const calls=[],deadline=Date.now()/1000+75;
  const scoped={run_id:'fixture-run',step_key:'answer',role:'answer',max_output_tokens:4096,
    provider:'chatgpt',access_kind:'native',account_ref:ref,deadline,max_bytes:16384,
    items:[{kind:'owner_message',source_id:1,sha256:'a'.repeat(64),text:'approved fixture task'}]};
  const host=await createChatGPTCollaborationHost({directory,accountRef:ref,surface,observeIdentity,
    timeoutMs:60000,
    createSessions:async()=>({
      open:async p=>{browser=await open(p);return browser;},
      status:p=>sessions.status(p),
      close:async p=>{
        clicks=await browser.pages()[0].locator('html').getAttribute('data-clicks');
        await sessions.close(p);
      }
    }),
    hubCall:async(tool,args)=>{
      calls.push(tool);
      if(tool==='task_claim')return {task_id:1,status:'claimed',claim_token:'fixture',lease_expires:deadline};
      if(tool==='collaboration_context_get')return structuredClone(scoped);
      if(tool==='run_start')return {task_id:1,status:'running',fence:1};
      if(tool==='task_complete'){
        assert.equal(args.result,'Final answer');assert.ok(!('provider_usage' in args));
        assert.equal(await browser.pages()[0].locator('[data-pack-fixture-text]').first().innerText(),approved.text);
        return {task_id:1,status:'review'};
      }
      throw Error('unexpected tool');
    },
    authorizeEgress:async()=>{egress++;await assert.rejects(manager.acquire(ref),/account_lease_exists/);return true;},
    authorizeNativeSend:async value=>{approved=value;return true;}
  });
  assert.deepEqual(await host.execute(1),{status:'review',usage:null});
  assert.equal(egress,1);assert.equal(clicks,'1');
  assert.deepEqual(calls,['task_claim','collaboration_context_get','run_start','collaboration_context_get','task_complete']);
  assert.equal((await host.execute(1)).code,'task_already_attempted');
  assert.equal(sessions.status(profile),'closed');
  await (await manager.acquire(ref)).release();
});
