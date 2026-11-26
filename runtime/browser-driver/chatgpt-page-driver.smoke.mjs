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
