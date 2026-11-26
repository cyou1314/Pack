import fs from 'node:fs/promises';
import path from 'node:path';
import {createHmac,randomBytes,timingSafeEqual} from 'node:crypto';
import {nativeProviderOrigin} from './native-account-adapter.mjs';

const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH=/^[0-9a-f]{64}$/;
const LIMIT=4096;
const invalid=()=>{throw Error('native_identity_unavailable');};
const validID=value=>typeof value==='string'&&value.trim()===value&&value.length>0&&
    Buffer.byteLength(value)<=512&&!/[\x00-\x1f\x7f]/.test(value);
function same(a,b) {
  return typeof a==='string'&&typeof b==='string'&&HASH.test(a)&&HASH.test(b)&&
    timingSafeEqual(Buffer.from(a,'hex'),Buffer.from(b,'hex'));
}
function validateBinding(binding) {
  if(!UUID.test(binding?.account_ref||'')||binding.access_kind!=='native'||
     !nativeProviderOrigin(binding.provider)||
     !['providerManaged','disabled'].includes(binding.memory_boundary)||
     !(binding.model_id===null||validID(binding.model_id))) invalid();
}
function safeOrigin(raw,provider) {
  try {
    const u=new URL(raw);
    return u.origin===nativeProviderOrigin(provider)&&!u.username&&!u.password;
  } catch {return false;}
}
function observed(binding,state) {
  // Trusted driver must obtain stable account AND workspace IDs from the
  // actual logged-in UI. A display name, cookie, avatar, guessed ID, or
  // user-entered string alone is not acceptable evidence.
  if(state?.state!=='ready'||state.busy!==false||
     !safeOrigin(state.url,binding.provider)||!validID(state.accountID)||
     !validID(state.workspaceID)||state.modelVerified!==true||state.memoryVerified!==true||
     state.modelID!==binding.model_id||state.memoryBoundary!==binding.memory_boundary) invalid();
  return [state.accountID,state.workspaceID,state.modelID,state.memoryBoundary];
}
function proof(binding,key,observation) {
  return createHmac('sha256',Buffer.from(key,'hex')).update(JSON.stringify([
    'pack-native-identity-v1',binding.account_ref,binding.provider,...observation
  ])).digest('hex');
}
async function privateDirectory(directory,create=false) {
  if(create) {
    try {await fs.mkdir(directory,{mode:0o700});}
    catch(e) {if(e.code!=='EEXIST')throw e;}
  }
  const st=await fs.lstat(directory);
  if(!st.isDirectory()||st.isSymbolicLink()||st.uid!==process.getuid()||(st.mode&0o077)!==0)invalid();
}
async function readRecord(file,binding) {
  const handle=await fs.open(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
  try {
    const st=await handle.stat();
    if(!st.isFile()||st.uid!==process.getuid()||st.nlink!==1||(st.mode&0o077)!==0||
       st.size<1||st.size>LIMIT) invalid();
    const buffer=Buffer.alloc(LIMIT+1);
    const {bytesRead}=await handle.read(buffer,0,buffer.length,0);
    if(bytesRead!==st.size||bytesRead>LIMIT)invalid();
    const value=JSON.parse(buffer.subarray(0,bytesRead).toString('utf8'));
    if(Object.keys(value).sort().join(',')!=='account_ref,key,memory_boundary,model_id,proof,provider,version'||
       value.version!==1||value.account_ref!==binding.account_ref||value.provider!==binding.provider||
       value.model_id!==binding.model_id||value.memory_boundary!==binding.memory_boundary||
       !HASH.test(value.key||'')||!HASH.test(value.proof||'')) invalid();
    return value;
  } finally {await handle.close();}
}
async function recordPath(root,binding,create) {
  validateBinding(binding);
  if(typeof root!=='string'||!path.isAbsolute(root))invalid();
  await privateDirectory(root);
  const directory=path.join(root,'native-identities');
  await privateDirectory(directory,create);
  return path.join(directory,binding.account_ref+'.json');
}
function live(signal) {if(signal?.aborted)throw Error('native_identity_cancelled');}

/**
 * Local, insert-only identity enrollment. Caller MUST hold the shared account
 * execution lease and isolated BrowserSessions lease for the entire operation.
 * No actual provider driver is included here. The observation callback is
 * trusted code, not an MCP tool, arbitrary page script, or user input.
 * The HMAC key and proof are local private metadata, not login credentials;
 * this protects against accidental account swaps, not a compromised local host.
 */
export async function enrollNativeIdentity({root,binding,observe,confirm,signal}) {
  if(typeof observe!=='function'||typeof confirm!=='function')invalid();
  binding=Object.freeze({...binding});
  const file=await recordPath(root,binding,true);
  // Existing record is immutable: a different account needs a new UUID.
  try {await fs.lstat(file);throw Error('native_identity_already_enrolled');}
  catch(e){if(e.code!=='ENOENT')throw e;}
  live(signal);
  const first=observed(binding,await observe({signal}));
  const key=randomBytes(32).toString('hex'), fingerprint=proof(binding,key,first);
  // Stable IDs may be shown to the owner for explicit local confirmation.
  // They are never logged, persisted or sent to Hub.
  live(signal);
  if(await confirm(Object.freeze({accountRef:binding.account_ref,provider:binding.provider,
    accountID:first[0],workspaceID:first[1],modelID:first[2],memoryBoundary:first[3]}))!==true)
    throw Error('native_identity_not_confirmed');
  live(signal);
  const second=observed(binding,await observe({signal}));
  if(!same(fingerprint,proof(binding,key,second)))invalid();
  live(signal);
  const record={version:1,account_ref:binding.account_ref,provider:binding.provider,
    model_id:binding.model_id,memory_boundary:binding.memory_boundary,key,proof:fingerprint};
  // Publish a complete record without replacing any existing identity.
  const temp=file+'.'+randomBytes(16).toString('hex')+'.tmp';
  const handle=await fs.open(temp,'wx',0o600);
  try {
    try {await handle.writeFile(JSON.stringify(record));await handle.sync();}
    finally {await handle.close();}
    live(signal);
    await fs.link(temp,file);
  } finally {await fs.unlink(temp);}
  return {accountRef:binding.account_ref,provider:binding.provider,enrolled:true};
}

/** Read-only verification. Missing/unsafe/corrupt records fail closed;
 * a different logged-in account/workspace/model/memory setting never
 * generates a replacement fingerprint. */
export async function verifyNativeIdentity({root,binding,observe,signal}) {
  if(typeof observe!=='function')invalid();
  binding=Object.freeze({...binding});
  const file=await recordPath(root,binding,false);
  const record=await readRecord(file,binding);
  live(signal);
  const state=await observe({signal});
  const actual=proof(binding,record.key,observed(binding,state));
  live(signal);
  if(!same(actual,record.proof))invalid();
  return Object.freeze({url:state.url,state:'ready',accountProof:actual,
    modelID:state.modelID,memoryBoundary:state.memoryBoundary,busy:false});
}

/** Load the expected opaque proof without asserting the live session is ready.
 * The driver must call verifyNativeIdentity on each inspect. */
export async function expectedNativeAccountProof({root,binding}) {
  binding=Object.freeze({...binding});
  return (await readRecord(await recordPath(root,binding,false),binding)).proof;
}
