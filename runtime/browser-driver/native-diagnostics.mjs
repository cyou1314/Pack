import {nativeProviderOrigin} from './native-account-adapter.mjs';

// Fixed candidate probes, not a validated send/identity contract. No generic
// attribute enumeration, text/HTML capture, account fields or private endpoints.
const PROBES=Object.freeze({
  'chatgpt':Object.freeze({
    composerId:'#prompt-textarea',
    composerTestId:'[data-testid="prompt-textarea"]',
    sendTestId:'[data-testid="send-button"]',
    stopTestId:'[data-testid="stop-button"]',
    userRole:'[data-message-author-role="user"]',
    assistantRole:'[data-message-author-role="assistant"]',
    messageId:'[data-message-id]',
    turnTestId:'[data-testid^="conversation-turn-"]'
  }),
  'gemini':Object.freeze({}),
  'doubao':Object.freeze({})
});
const cap=n=>Math.min(n,100);
function kind(raw,origin) {
  try {
    const u=new URL(raw);
    if(u.origin!==origin||u.username||u.password)return 'off_origin';
    if(u.pathname==='/')return 'root';
    if(/^\/c\/[A-Za-z0-9-]{1,128}\/?$/.test(u.pathname))return 'conversation';
    return 'other';
  } catch{return 'unknown';}
}
const unavailable=(provider,status)=>({version:1,provider,status,identityVerified:false,executionEnabled:false});

/** User-invoked read-only diagnostic inside the leased isolated browser.
 * Only fixed labels and capped counts leave the DOM. No URL, query, DOM
 * strings, screenshots, cookies, storage or chat contents are returned.
 * The report never authorizes a send or marks a session logged in. */
export async function diagnoseNativeSurface({context,provider,signal}) {
  const origin=nativeProviderOrigin(provider),probes=PROBES[provider];
  if(!origin||!probes)throw Error('unsupported_diagnostic_provider');
  if(signal?.aborted)return unavailable(provider,'cancelled');
  const pages=context.pages();
  if(pages.length!==1||pages[0].isClosed())return unavailable(provider,'single_page_required');
  const page=pages[0],url=page.url(),route=kind(url,origin);
  if(route==='off_origin'||route==='unknown')return unavailable(provider,'provider_page_required');
  let raw;
  try{
    raw=await page.evaluate(probes=>{
      const visible=e=>e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden'&&
        getComputedStyle(e).display!=='none';
      return Object.fromEntries(Object.entries(probes).map(([key,selector])=>{
        const nodes=Array.from(document.querySelectorAll(selector));
        return [key,{total:Math.min(nodes.length,100),visible:Math.min(nodes.filter(visible).length,100)}];
      }));
    },probes);
  }catch{return unavailable(provider,'inspection_unavailable');}
  if(signal?.aborted)return unavailable(provider,'cancelled');
  if(context.pages().length!==1||context.pages()[0]!==page||page.isClosed()||page.url()!==url)
    return unavailable(provider,'page_changed');
  const counts={};
  for(const key of Object.keys(probes)){
    const value=raw?.[key];
    if(!Number.isSafeInteger(value?.total)||!Number.isSafeInteger(value?.visible)||
       value.total<0||value.visible<0||value.visible>value.total||value.total>100)
      return unavailable(provider,'inspection_unavailable');
    counts[key]={total:cap(value.total),visible:cap(value.visible)};
  }
  return {...unavailable(provider,Object.keys(probes).length?'candidate_counts_only':'origin_only'),
    route,counts};
}
