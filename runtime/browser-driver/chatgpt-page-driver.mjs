import {randomUUID} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {verifyNativeIdentity} from './native-identity.mjs';

const ORIGIN='https://chatgpt.com';
const CONVERSATION=/^\/c\/([A-Za-z0-9-]{1,128})\/?$/;
const SELECTOR=/^\[data-[a-z0-9-]+="[A-Za-z0-9_:-]{1,100}"\]$/;
const ATTR=/^data-[a-z0-9-]+$/;
const LIMIT=16*1024;
const KEYS=['ready','composer','send','turn','user','assistant','text','busy','complete','failure'];
const fail=code=>{throw Error(code);};

function contract(value) {
  // No arbitrary script, broad selector, automatic fallback or network route.
  // All selectors are supplied by a trusted, reviewed provider integration.
  if(!value||Object.keys(value).sort().join(',')!==[...KEYS,'messageIdAttribute'].sort().join(',')||
     !KEYS.every(k=>typeof value[k]==='string'&&SELECTOR.test(value[k]))||
     new Set(KEYS.map(k=>value[k])).size!==KEYS.length||
     typeof value.messageIdAttribute!=='string'||!ATTR.test(value.messageIdAttribute))
    fail('chatgpt_surface_unverified');
  return Object.freeze({...value});
}
function location(raw) {
  try {
    const url=new URL(raw);
    if(url.origin!==ORIGIN||url.username||url.password||url.search||url.hash)return null;
    if(url.pathname==='/')return {draft:true,id:null};
    const match=CONVERSATION.exec(url.pathname);
    return match?{draft:false,id:match[1]}:null;
  } catch {return null;}
}
function live(signal,end) {
  if(signal?.aborted||Date.now()>=end)fail('chatgpt_operation_timeout_or_cancelled');
}
const validID=id=>typeof id==='string'&&/^[A-Za-z0-9_-]{1,128}$/.test(id);

/**
 * ChatGPT-specific DOM transport, deliberately without a default live-site
 * selector contract. Only trusted local host code may supply reviewed surface
 * selectors and a real identity observer. Both leases must already be held.
 * The local draft ID correlates the request before the provider assigns /c/id.
 */
export function createChatGPTPageDriver({context,page,root,binding,surface,observeIdentity,
    timeoutMs=45000,pollMs=200}) {
  const s=contract(surface);
  if(binding?.provider!=='chatgpt'||binding.access_kind!=='native'||
     typeof observeIdentity!=='function'||typeof root!=='string'||
     !Number.isSafeInteger(timeoutMs)||timeoutMs<100||timeoutMs>120000||
     !Number.isSafeInteger(pollMs)||pollMs<20||pollMs>1000)
    fail('chatgpt_invalid_driver');
  let draft=null,sendEntered=false,receipt=null,waiting=false,finished=false,preparing=false;
  function current() {
    const pages=context.pages();
    const loc=location(page.url());
    if(pages.length!==1||pages[0]!==page||page.isClosed()||!loc)
      fail('chatgpt_page_changed');
    return loc;
  }
  async function snapshot() {
    const before=current(), url=page.url();
    // Read only bounded state from this visible DOM, not storage, cookies,
    // provider internals, network responses or unrelated historical messages.
    const state=await page.evaluate((s)=>{
      const all=(selector,base=document)=>Array.from(base.querySelectorAll(selector));
      const visible=element=>!!element&&element.getClientRects().length>0&&
        getComputedStyle(element).visibility!=='hidden'&&getComputedStyle(element).display!=='none';
      const matching=selector=>all(selector).filter(visible);
      const composers=all(s.composer),buttons=all(s.send),turns=all(s.turn);
      const c=composers.length===1?composers[0]:null;
      const b=buttons.length===1?buttons[0]:null;
      const content=c&&(c instanceof HTMLTextAreaElement||c instanceof HTMLInputElement)?c.value:
        c?.isContentEditable?c.innerText:null;
      const readTurn=t=>{
        const user=t.matches(s.user),assistant=t.matches(s.assistant),parts=all(s.text,t);
        const text=parts.length===1&&visible(parts[0])?parts[0].innerText:null;
        return {id:t.getAttribute(s.messageIdAttribute),role:user===assistant?null:user?'user':'assistant',
          text:typeof text==='string'&&text.length<=65536?text:null,
          complete:all(s.complete,t).filter(visible).length===1,visible:visible(t)};
      };
      return {ready:matching(s.ready).length===1,failed:matching(s.failure).length!==0,
        busy:matching(s.busy).length!==0,
        composer:!!c&&visible(c)&&(c instanceof HTMLTextAreaElement||c.isContentEditable)&&
          !c.hasAttribute('disabled')&&!c.hasAttribute('readonly')&&c.getAttribute('aria-disabled')!=='true',
        value:typeof content==='string'&&content.length<=65536?content:null,
        send:!!b&&visible(b)&&b instanceof HTMLButtonElement&&!b.disabled&&b.getAttribute('aria-disabled')!=='true',
        count:turns.length,turns:turns.length<=2?turns.map(readTurn):[]};
    },s);
    if(page.url()!==url||JSON.stringify(current())!==JSON.stringify(before))fail('chatgpt_page_changed');
    return {...state,loc:before,url};
  }
  async function inspect({signal}={}) {
    const before=current(),url=page.url();
    const verified=await verifyNativeIdentity({root,binding,signal,
      observe:async args=>observeIdentity({page,...args})});
    if(page.url()!==url||JSON.stringify(current())!==JSON.stringify(before)||
       verified.url!==url)fail('chatgpt_page_changed');
    const state=await snapshot();
    if(!state.ready||state.failed||state.busy||!state.composer)
      fail('chatgpt_surface_unavailable');
    return verified;
  }
  function empty(state,value='') {
    return state.loc.draft&&state.ready&&!state.failed&&!state.busy&&state.composer&&
      state.count===0&&state.value===value;
  }
  async function poll(check,signal,end) {
    while(true) {
      live(signal,end);
      const result=await check();
      live(signal,end);
      if(result)return result;
      await delay(Math.min(pollMs,Math.max(1,end-Date.now())),undefined,{signal});
    }
  }
  return {
    inspect,
    async prepareConversation({signal}={}) {
      if(draft||sendEntered||preparing)fail('chatgpt_draft_already_prepared');
      preparing=true;
      try {
        const end=Date.now()+timeoutMs;
        live(signal,end);
        await inspect({signal});
        live(signal,end);
        // Only the fixed fresh-chat URL. Never select or import old chats.
        await page.goto(ORIGIN+'/',{waitUntil:'domcontentloaded',timeout:Math.min(15000,timeoutMs)});
        await poll(async()=>{
          const state=await snapshot();
          if(!state.loc.draft||state.count!==0||state.failed)fail('chatgpt_not_fresh');
          return empty(state);
        },signal,end);
        await inspect({signal});
        if(!empty(await snapshot()))fail('chatgpt_not_fresh');
        live(signal,end);
        draft='draft-'+randomUUID();
        return {url:ORIGIN+'/',conversationID:draft,empty:true};
      } finally {preparing=false;}
    },
    async sendOnce({conversationID,text,signal}) {
      if(sendEntered||!draft||conversationID!==draft||typeof text!=='string'||
         !text.trim()||Buffer.byteLength(text)>LIMIT+2048)fail('chatgpt_invalid_send');
      sendEntered=true;
      const end=Date.now()+timeoutMs;
      live(signal,end);
      await inspect({signal});
      if(!empty(await snapshot()))fail('chatgpt_not_fresh');
      // Fill only after the adapter has obtained explicit exact-text approval.
      await page.locator(s.composer).fill(text,{timeout:Math.min(5000,timeoutMs)});
      await inspect({signal});
      const before=await snapshot();
      if(!empty(before,text)||!before.send)fail('chatgpt_composer_changed');
      live(signal,end);
      // A single click attempt, never Enter fallback or a second click. Once
      // entered, errors are ambiguous and the caller retains the account lease.
      await page.locator(s.send).click({timeout:Math.min(5000,timeoutMs)});
      receipt=await poll(async()=>{
        const state=await snapshot();
        if(state.failed||!state.ready||state.count>2)fail('chatgpt_send_unknown');
        if(state.count===0)return null;
        const user=state.turns[0];
        if(user?.role!=='user'||!user.visible||!validID(user.id)||user.text!==text)
          fail('chatgpt_send_unknown');
        if(state.loc.draft)return null;
        if(state.count===2&&(state.turns[1]?.role!=='assistant'||state.turns[1].id===user.id))
          fail('chatgpt_send_unknown');
        return Object.freeze({providerConversationId:state.loc.id,userId:user.id,text});
      },signal,end);
      return {conversationID:draft,turnID:receipt.userId,accepted:true};
    },
    async waitForReply({conversationID,turnID,signal}) {
      if(!receipt||conversationID!==draft||turnID!==receipt.userId||waiting||finished)
        fail('chatgpt_reply_unknown');
      waiting=true;
      try {
        const end=Date.now()+timeoutMs;
        let previous=null;
        const answer=await poll(async()=>{
          const state=await snapshot(),user=state.turns[0],assistant=state.turns[1];
          if(state.loc.id!==receipt.providerConversationId||state.failed||!state.ready||
             state.count<1||state.count>2||user?.role!=='user'||!user.visible||
             user.id!==receipt.userId||user.text!==receipt.text)fail('chatgpt_reply_unknown');
          if(state.count===1){previous=null;return null;}
          if(assistant?.role!=='assistant'||!assistant.visible||!validID(assistant.id)||assistant.id===user.id)
            fail('chatgpt_reply_unknown');
          // A stable text alone is not proof of completion: explicit positive
          // terminal marker plus no busy marker and two identical observations.
          if(state.busy||!assistant.complete){previous=null;return null;}
          if(typeof assistant.text!=='string'||!assistant.text.trim()||
             Buffer.byteLength(assistant.text)>LIMIT)fail('chatgpt_reply_unknown');
          const candidate={id:assistant.id,text:assistant.text,url:state.url};
          if(previous&&JSON.stringify(previous)===JSON.stringify(candidate))return candidate;
          previous=candidate;
          return null;
        },signal,end);
        await inspect({signal});
        const final=await snapshot();
        if(final.loc.id!==receipt.providerConversationId||final.failed||final.busy||final.count!==2||
           final.turns[0]?.id!==receipt.userId||final.turns[0]?.text!==receipt.text||
           final.turns[1]?.id!==answer.id||final.turns[1]?.text!==answer.text||!final.turns[1]?.complete)
          fail('chatgpt_reply_unknown');
        live(signal,end);
        finished=true;
        return {url:answer.url,conversationID:draft,turnID,role:'assistant',complete:true,text:answer.text};
      } finally {waiting=false;}
    }
  };
}
