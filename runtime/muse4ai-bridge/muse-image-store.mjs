import {constants} from 'node:fs';
import {mkdir,lstat,open,link,unlink} from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import path from 'node:path';

const MAX_IMAGE=100*1024*1024;
const EXT=Object.freeze({'image/png':'png','image/jpeg':'jpg','image/webp':'webp'});
const sha=value=>createHash('sha256').update(value).digest('hex');
const error=code=>Error(code);

function metadata(out,taskId){
  if(!Number.isSafeInteger(taskId)||taskId<=0||
     typeof out?.asset_id!=='string'||!out.asset_id||out.asset_id.length>256||
     out.asset_id==='.'||out.asset_id==='..'||/[\x00-\x1f\x7f]/.test(out.asset_id)||
     !Object.hasOwn(EXT,out.mime)||
     (out.bytes!=null&&(!Number.isSafeInteger(out.bytes)||out.bytes<=0||out.bytes>MAX_IMAGE))||
     (out.sha256!=null&&(typeof out.sha256!=='string'||!/^[0-9a-f]{64}$/i.test(out.sha256))))
    throw error('image_output_invalid');
  // Preserve established filenames for normal asset IDs; hash noncanonical
  // or long IDs rather than collapsing different IDs to the same basename.
  const asset=/^[A-Za-z0-9_-]{1,128}$/.test(out.asset_id)
    ?out.asset_id:'~hashed-'+sha(out.asset_id);
  return {filename:'task-'+taskId+'-'+asset+'.'+EXT[out.mime],
          bytes:out.bytes??null,digest:out.sha256?.toLowerCase()??null};
}

async function privateDirectory(directory){
  try{await mkdir(directory,{mode:0o700});}
  catch(e){if(e.code!=='EEXIST')throw e;}
  const info=await lstat(directory);
  if(!info.isDirectory()||info.uid!==process.getuid()||(info.mode&0o077)!==0)
    throw error('image_storage_unsafe');
}

async function existingFile(filename){
  let handle;
  try{
    handle=await open(filename,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
  }catch(e){
    if(e.code==='ENOENT')return null;
    throw error('image_storage_unsafe');
  }
  try{
    const before=await handle.stat();
    if(!before.isFile()||before.uid!==process.getuid()||(before.mode&0o077)!==0)
      throw error('image_storage_unsafe');
    if(before.size<=0||before.size>MAX_IMAGE)throw error('image_asset_conflict');
    let size=0;const hash=createHash('sha256'),buffer=Buffer.allocUnsafe(65536);
    for(;;){
      const {bytesRead}=await handle.read(buffer,0,buffer.length,null);
      if(!bytesRead)break;
      size+=bytesRead;
      if(size>MAX_IMAGE)throw error('image_asset_conflict');
      hash.update(buffer.subarray(0,bytesRead));
    }
    const after=await handle.stat();
    // Removing a publisher's temporary hard link changes ctime, not bytes.
    // mtime, size and the streamed digest are the content checks here.
    if(before.size!==size||after.size!==size||before.mtimeMs!==after.mtimeMs)
      throw error('image_asset_conflict');
    return {size,digest:hash.digest('hex')};
  }finally{await handle.close();}
}

function same(actual,expected){
  return actual&&actual.size===expected.size&&actual.digest===expected.digest;
}

/**
 * Save a single completed asset without overwriting existing files.
 * Trusted app-owned parent directory; no guarantee against a malicious
 * process with the same UID changing directory entries concurrently.
 * link() is the atomic no-replace publication operation on a local FS.
 */
export async function saveMuseImage({directory,out,taskId,download}){
  const meta=metadata(out,taskId);
  const dir=path.join(directory,'images');
  await privateDirectory(dir);
  const dest=path.join(dir,meta.filename);
  const existing=await existingFile(dest);
  if(existing){
    if(meta.bytes!==null&&existing.size!==meta.bytes)throw error('image_asset_conflict');
    if(meta.digest!==null){
      if(existing.digest!==meta.digest)throw error('image_asset_conflict');
      return dest;
    }
    // Size alone cannot authenticate a previous file. Re-download once
    // to compare actual contents when Engine omitted its digest.
  }
  const temporary=path.join(dir,'.pack-image-'+randomUUID()+'.part');
  let handle,created=false;
  try{
    handle=await open(temporary,'wx',0o600);created=true;
    const res=await download();
    if(!res?.ok||!res.body)throw error('image_download_failed');
    const hash=createHash('sha256');let size=0;
    for await(const chunk of res.body){
      size+=chunk.length;
      if(size>MAX_IMAGE)throw error('image_too_large');
      if(meta.bytes!==null&&size>meta.bytes)throw error('image_integrity_failed');
      hash.update(chunk);
      await handle.writeFile(chunk);
    }
    const actual={size,digest:hash.digest('hex')};
    if(!size||(meta.bytes!==null&&size!==meta.bytes)||(meta.digest!==null&&actual.digest!==meta.digest))
      throw error('image_integrity_failed');
    await handle.sync();
    await handle.close();handle=null;
    // If another cooperating download won the race, use its file only
    // when it matches the validated bytes. Never rename over it.
    try{await link(temporary,dest);}
    catch(e){
      if(e.code!=='EEXIST')throw e;
      if(!same(await existingFile(dest),actual))throw error('image_asset_conflict');
    }
    return dest;
  }finally{
    try{if(handle)await handle.close();}
    finally{if(created)await unlink(temporary);}
  }
}
