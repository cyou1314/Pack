import fs from 'node:fs/promises';
import path from 'node:path';
import { AccountExecutionLeases } from '../account-execution-leases.mjs';
import { nativeProviderOrigin } from './native-account-adapter.mjs';

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const MAX_REGISTRY=1024*1024;

async function checkedDirectory(directory,create=false,privateOnly=true) {
  if(create) {
    try { await fs.mkdir(directory,{mode:0o700}); }
    catch(e) { if(e.code!=='EEXIST') throw e; }
  }
  const st=await fs.lstat(directory);
  if(!st.isDirectory() || st.isSymbolicLink() || st.uid!==process.getuid() ||
      (st.mode & (privateOnly?0o077:0o022))!==0)
    throw Error('unsafe_native_storage');
}

/** Resolve only an existing device-local profile; no browser cookie or API key
 * is loaded. UUID, not user-supplied provider or URL, selects the destination. */
export async function resolveNativeLogin({directory,accountRef}) {
  if(typeof directory!=='string'||!path.isAbsolute(directory)||!UUID.test(accountRef||''))
    throw Error('invalid_native_profile');
  await checkedDirectory(path.dirname(directory),false,false);
  await checkedDirectory(directory);
  const target=path.join(directory,'agent-registry.json');
  // O_NOFOLLOW plus descriptor-level metadata validation. Bounded read.
  const file=await fs.open(target,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
  let data;
  try {
    const st=await file.stat();
    if(!st.isFile() || st.uid!==process.getuid() || st.nlink!==1 ||
       (st.mode&0o022)!==0 || st.size>MAX_REGISTRY) throw Error('unsafe_native_registry');
    const buffer=Buffer.alloc(MAX_REGISTRY+1);
    const {bytesRead}=await file.read(buffer,0,buffer.length,0);
    if(bytesRead!==st.size || bytesRead>MAX_REGISTRY) throw Error('unsafe_native_registry');
    data=JSON.parse(buffer.subarray(0,bytesRead).toString('utf8'));
  } finally { await file.close(); }
  if(data?.schemaVersion!==1 || !Array.isArray(data.profiles)) throw Error('invalid_native_registry');
  const matches=data.profiles.filter(p=>typeof p?.id==='string'&&p.id.toLowerCase()===accountRef);
  if(matches.length!==1) throw Error('invalid_native_profile');
  const p=matches[0],origin=nativeProviderOrigin(p.providerID);
  if(!origin||p.mode!=='nativeAccount'||p.credentialReference?.kind!=='nativeSession'||
      p.credentialReference?.vaultKey!==accountRef||p.contextBoundary!=='providerAccount'||
      !['providerManaged','disabled'].includes(p.memoryBoundary)||
      !Array.isArray(p.enabledCapabilities)||!p.enabledCapabilities.includes('chat')||
      data.profiles.some(other=>other!==p&&other?.credentialReference?.vaultKey===accountRef))
    throw Error('invalid_native_profile');
  const browserRoot=path.join(directory,'native-sessions');
  const leaseRoot=path.join(directory,'execution-leases');
  await checkedDirectory(browserRoot,true);
  await checkedDirectory(leaseRoot,true);
  return Object.freeze({profile:Object.freeze({id:accountRef,mode:'nativeAccount'}),
    provider:p.providerID,origin,browserRoot,leaseRoot,
    executionProfile:Object.freeze({id:accountRef,providerID:p.providerID,mode:'nativeAccount',
      credentialReference:Object.freeze({kind:'nativeSession',vaultKey:accountRef}),
      contextBoundary:p.contextBoundary,memoryBoundary:p.memoryBoundary,modelID:p.modelID??null,
      enabledCapabilities:Object.freeze(['chat'])})});
}

/** User-operated login only. Do not inspect page contents or automate login.
 * Both the login and future execution host use the same account lease.
 * Any failed close or uncertain launch retains the account marker. */
export async function runNativeLogin({binding,createSessions,waitForExit,signal}) {
  if(!binding || typeof createSessions!=='function'||typeof waitForExit!=='function')
    throw Error('invalid_login_host');
  // Resolve browser dependency before acquiring the execution lease.
  const sessions=await createSessions({root:binding.browserRoot,headless:false});
  if(signal?.aborted) throw Error('login_cancelled');
  const lease=await new AccountExecutionLeases({root:binding.leaseRoot}).acquire(binding.profile.id);
  let opened=false,clean=false,closedByUser=false;
  try {
    if(signal?.aborted) { clean=true; throw Error('login_cancelled'); }
    const context=await sessions.open(binding.profile);
    opened=true;
    // Do not create repeated pages, export storage or inspect identity.
    const page=context.pages()[0] || await context.newPage();
    const onClose=()=>{closedByUser=true;};
    context.once('close',onClose);
    try {
      if(!signal?.aborted) {
        // Normal provider/SSO redirects remain user-controlled. No automation
        // after the initial navigation, no password/captcha handling.
        await page.goto(binding.origin,{timeout:15000,waitUntil:'domcontentloaded'});
        if(!closedByUser&&!signal?.aborted) await waitForExit({context,signal});
      }
    } finally { context.off('close',onClose); }
  } finally {
    if(opened) {
      await sessions.close(binding.profile);
      clean=true;
    } else if(sessions.status(binding.profile)==='closed') {
      // Known pre-launch failure does not require account quarantine.
      clean=true;
    }
    if(clean) await lease.release();
  }
  return {status:'session_closed_unverified',provider:binding.provider};
}
