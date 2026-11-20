// A separate page in the verified member space; response bodies stay in memory.
async function packQuotaMain() {
 const {profileId,spaceName}=globalThis.PACK_TASK;
 const done=x=>console.log('DRIVER-RESULT '+JSON.stringify(x));
 let page, stage='space';
 try {
  const space=await egoOpenSpace(profileId,spaceName);stage='page';page=space.page('pack-quota');
  stage='navigation';
  await page.goto('https://muse.ai/');await page.waitForLoadState();
  await page.waitForSelector('loc=css:[data-testid="hatch-dock-more"]',{state:'visible',timeout:30000});
  stage='capture';await page.evaluate(()=>{
   window.__packQuota=[];
   const add=(v)=>{ if(v && typeof v==='object') window.__packQuota.push(v); };
   const original=window.fetch;window.fetch=async function(...args){const r=await original.apply(this,args);if(/quota|usage/i.test(String(args[0])))r.clone().json().then(add).catch(()=>{});return r;};
   const open=XMLHttpRequest.prototype.open,send=XMLHttpRequest.prototype.send;
   XMLHttpRequest.prototype.open=function(method,url,...rest){this.__packQuota=/quota|usage/i.test(String(url));return open.call(this,method,url,...rest);};
   XMLHttpRequest.prototype.send=function(...args){if(this.__packQuota)this.addEventListener('load',()=>{try{add(JSON.parse(this.responseText))}catch{}});return send.apply(this,args);};
  });
  stage='settings-menu';await page.click('loc=css:[data-testid="hatch-dock-more"]');
  await page.waitForSelector('loc=role:menuitem[name="设置"]',{state:'visible',timeout:10000});
  await page.click('loc=role:menuitem[name="设置"]');
  stage='usage-load';await page.waitForFunction(()=>!!document.querySelector('[role="dialog"]') && (/(?:\d+(?:\.\d+)?\s*%\s*(?:used|已使用|已用)|已(?:使)?用\s*\d+(?:\.\d+)?\s*%)/i.test(document.querySelector('[role="dialog"]').innerText) || (window.__packQuota||[]).length>0),undefined,{timeout:20000});
  const data=await page.evaluate(()=>{
   const queue=[...(window.__packQuota||[])]; let found={};
   while(queue.length){const o=queue.shift();if(!o||typeof o!=='object')continue;
    for(const [k,v] of Object.entries(o)){if(v && typeof v==='object')queue.push(v);else if(['week_used_pct','week_reset_at','extra_tokens_left'].includes(k) && typeof v==='number')found[k]=v;}}
   if(typeof found.week_used_pct==='number')return found;
   const text=(document.querySelector('[role="dialog"]')?.innerText||'').normalize('NFKC');
   const beforeExtra=text.split(/(?:Additional|Extra)\s+tokens|(?:額外|额外)(?:的)?(?:令牌|代币|tokens)/i)[0];
   const percent=/(\d+(?:\.\d+)?)\s*%\s*(?:used\b|已(?:使)?用)|已(?:使)?用\s*(\d+(?:\.\d+)?)\s*%/i.exec(beforeExtra);
   const extra=/(\d[\d,]*)\s+tokens?\s+left/i.exec(text);
   return {...percent?{week_used_pct:Number(percent[1]||percent[2])}:{},...extra?{extra_tokens_left:Number(extra[1].replace(/,/g,''))}:{}};
  });
  if(typeof data.week_used_pct!=='number')throw Error('quota unavailable');
  done({ok:true,quota:data});
 } catch {done({ok:false,error:`quota read unavailable (${stage})`});}
}
packQuotaMain();
