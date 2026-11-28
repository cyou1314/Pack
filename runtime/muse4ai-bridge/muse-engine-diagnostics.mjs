import {constants} from 'node:fs';
import {open} from 'node:fs/promises';

const MAX_KEY=512;
const MAX_ACCOUNTS=1000;
const MAX_QUOTA_AGE=6*60*60*1000;
const invalid=()=>Error('muse_diagnostic_unavailable');

/** The local alias is routing metadata, not proof of the remote identity. */
export function summarizeMuseStatus(status,{alias='absent',now=Date.now()}={}){
  const report={
    version:1,provider:'muse',connection:'legacy_local_engine',
    service:'responded',credential:'unknown',scope:'unknown',
    account:'unknown',reportedReadiness:'unknown',
    identityVerified:false,capabilitiesVerified:false,executionEnabled:false,
    quota:{status:'unavailable'}
  };
  if(!Number.isFinite(now)||typeof alias!=='string'||!alias.trim())
    throw invalid();
  if(status?.key?.scope==='generate'){
    report.credential='accepted';report.scope='generate';
  }else if(status?.key?.scope==='read'){
    report.credential='accepted';report.scope='read';
  }
  const items=status?.accounts?.items;
  if(!Array.isArray(items)||items.length>MAX_ACCOUNTS)return report;
  const matches=items.filter(x=>x?.alias===alias);
  if(matches.length!==1){
    report.account=matches.length===0?'not_reported':'ambiguous';
    return report;
  }
  report.account='alias_reported';
  const entry=matches[0];
  // Only documented "ready" group is a positive cached scheduling signal.
  // Do not guess other group meanings or equate ready with verified login.
  if(entry.group==='ready')report.reportedReadiness='ready_reported';
  else if(typeof entry.group==='string'&&entry.group.length>0)
    report.reportedReadiness='other_group_reported';
  const q=entry.quota;
  if(!q||typeof q!=='object')return report;
  if(q.stale===true){report.quota={status:'stale'};return report;}
  const observed=typeof q.observed_at==='string'?Date.parse(q.observed_at):NaN;
  if(!Number.isFinite(observed)||observed>now||q.stale!==false)return report;
  if(now-observed>MAX_QUOTA_AGE){report.quota={status:'stale'};return report;}
  const used=q.weekly_used_percent;
  if(typeof used!=='number'||!Number.isFinite(used)||used<0||used>100)return report;
  report.quota={status:'cached',week_used_pct:used,observed_at:new Date(observed).toISOString()};
  const reset=typeof q.weekly_resets_at==='string'?Date.parse(q.weekly_resets_at):NaN;
  if(Number.isFinite(reset)&&reset>now)report.quota.week_reset_at=reset/1000;
  // Never estimate token counts from labels or report arbitrary plan text.
  return report;
}

export async function diagnoseMuseEngine({client,alias='absent',now=Date.now()}={}){
  if(typeof client?.status!=='function')throw invalid();
  try{return summarizeMuseStatus(await client.status(),{alias,now});}
  catch(e){
    const denied=['muse_http_401','muse_http_403'].includes(e?.message);
    return {
      version:1,provider:'muse',connection:'legacy_local_engine',
      service:denied?'responded':'unconfirmed',
      credential:denied?'rejected':'unknown',scope:'unknown',account:'unknown',
      reportedReadiness:'unknown',identityVerified:false,
      capabilitiesVerified:false,executionEnabled:false,quota:{status:'unavailable'}
    };
  }
}

/** Diagnostics never take a secret in command arguments or print the key. */
export async function readMuseDiagnosticKey(filename){
  const handle=await open(filename,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
  try{
    const stat=await handle.stat();
    if(!stat.isFile()||stat.uid!==process.getuid()||stat.nlink!==1||
       (stat.mode&0o077)!==0||stat.size<1||stat.size>MAX_KEY)throw invalid();
    const buffer=Buffer.alloc(MAX_KEY+1);
    const {bytesRead}=await handle.read(buffer,0,buffer.length,0);
    if(bytesRead!==stat.size||bytesRead>MAX_KEY)throw invalid();
    const key=buffer.subarray(0,bytesRead).toString('utf8').trim();
    if(!/^msk_[\x21-\x7e]{20,}$/.test(key))throw invalid();
    return key;
  }finally{await handle.close();}
}
