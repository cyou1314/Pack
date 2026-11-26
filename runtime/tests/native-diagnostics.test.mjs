import {test} from 'node:test';
import assert from 'node:assert/strict';
import {verifyBrowserCandidateLock} from '../browser-driver/verify-candidate-lock.mjs';
import {diagnoseNativeSurface} from '../browser-driver/native-diagnostics.mjs';

const manifest={name:'pack-browser-driver',version:'0.1.0',dependencies:{'playwright-core':'1.65.1'}};
function fixture(){
  return {name:manifest.name,version:manifest.version,lockfileVersion:3,packages:{
    '':{...manifest},'node_modules/playwright-core':{
      version:'1.65.1',resolved:'https://registry.npmjs.org/playwright-core/-/playwright-core-1.65.1.tgz',
      integrity:'sha512-'+Buffer.alloc(64,42).toString('base64')
    }
  }};
}
test('candidate lock must pin exactly one public-registry package with canonical SHA-512',()=>{
  assert.equal(verifyBrowserCandidateLock(fixture(),manifest).releaseApproved,false);
  for(const mutate of [
    l=>l.packages['node_modules/playwright-core'].version='other',
    l=>l.packages['node_modules/playwright-core'].resolved='https://example.org/x',
    l=>l.packages['node_modules/playwright-core'].integrity='sha512-fake',
    l=>l.packages['node_modules/playwright-core'].hasInstallScript=true,
    l=>l.packages['node_modules/playwright-core'].dependencies={other:'1'},
    l=>l.packages['node_modules/other']={version:'1'},
    l=>l.packages[''].dependencies={'playwright-core':'^1.65.1'}
  ]){
    const value=fixture();mutate(value);
    assert.throws(()=>verifyBrowserCandidateLock(value,manifest),/invalid_browser_candidate_lock/);
  }
});
function pageFixture(url='https://chatgpt.com/c/private-conversation?secret=private-token'){
  const page={url:()=>url,isClosed:()=>false,evaluate:async(_fn,probes)=>({
    ...Object.fromEntries(Object.keys(probes).map(k=>[k,{total:1,visible:1,secret:'private-chat'}])),
    arbitrary:'private-account'
  })};
  return {page,context:{pages:()=>[page]}};
}
test('diagnostics emits only fixed counts and route category, not URL or page data',async()=>{
  const f=pageFixture(),report=await diagnoseNativeSurface({context:f.context,provider:'chatgpt'});
  assert.equal(report.status,'candidate_counts_only');
  assert.equal(report.route,'conversation');
  assert.equal(report.identityVerified,false);
  assert.equal(report.executionEnabled,false);
  assert.equal(JSON.stringify(report).includes('private-'),false);
  assert.equal(report.counts.sendTestId.visible,1);
});
test('off-origin, multiple pages and navigation change fail without inspecting content',async()=>{
  const f=pageFixture('https://chatgpt.com.example.org/');
  f.page.evaluate=async()=>assert.fail('must not inspect');
  assert.equal((await diagnoseNativeSurface({context:f.context,provider:'chatgpt'})).status,'provider_page_required');
  const g=pageFixture();
  assert.equal((await diagnoseNativeSurface({context:{pages:()=>[g.page,g.page]},provider:'chatgpt'})).status,'single_page_required');
  const h=pageFixture();
  const original=h.page.evaluate;
  h.page.evaluate=async(...args)=>{h.page.url=()=> 'https://chatgpt.com/';return original(...args);};
  assert.equal((await diagnoseNativeSurface({context:h.context,provider:'chatgpt'})).status,'page_changed');
});
