import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,readFileSync,readdirSync,writeFileSync,mkdirSync,symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {createMuseEngineClient} from '../muse-engine-client.mjs';

const key='msk_fixture_not_a_real_secret_12345';
test('fixed local engine operations preserve request contract and reject redirect following',async()=>{
  const calls=[];
  const client=createMuseEngineClient({baseUrl:'http://localhost:47822',key,directory:tmpdir(),
    fetchImpl:async(url,options)=>{calls.push({url,options});return Response.json({ok:true});}});
  await client.status();
  const body={kind:'chat',prompt:'fixture',account:'absent'};
  await client.submitJob(body,'pack-task-1-attempt-1-fence-1');
  await client.getJob('job/one');
  await client.cancelJob('job/one');
  assert.deepEqual(calls.map(x=>[x.url,x.options.method]),[
    ['http://localhost:47822/v1/status','GET'],['http://localhost:47822/v1/jobs','POST'],
    ['http://localhost:47822/v1/jobs/job%2Fone','GET'],
    ['http://localhost:47822/v1/jobs/job%2Fone/cancel','POST']]);
  assert.deepEqual(JSON.parse(calls[1].options.body),body);
  assert.equal(calls[1].options.headers['idempotency-key'],'pack-task-1-attempt-1-fence-1');
  assert.equal(calls[3].options.body,'{}');
  for(const call of calls){
    assert.equal(call.options.redirect,'error');
    assert.equal(call.options.headers.authorization,'Bearer '+key);
    assert.ok(call.options.signal instanceof AbortSignal);
  }
  assert.throws(()=>client.getJob('..'),/invalid_muse_identifier/);
  assert.throws(()=>client.submitJob(body,'invalid'),/invalid_muse_idempotency_key/);
  assert.equal(calls.length,4);
  assert.throws(()=>createMuseEngineClient({baseUrl:'https://example.com',key,directory:tmpdir()}),/invalid_local_muse_api_url/);
});
test('engine request failure is not retried inside client',async()=>{
  let calls=0;
  const client=createMuseEngineClient({key,directory:tmpdir(),fetchImpl:async()=>{calls++;return new Response(null,{status:503});}});
  await assert.rejects(()=>client.submitJob({},'pack-task-1-attempt-1-fence-1'),/muse_http_503/);
  assert.equal(calls,1);
});
test('image asset download retains file format and verifies bytes and hash',async()=>{
  const directory=mkdtempSync(path.join(tmpdir(),'pack-engine-fixture-'));
  const data=Buffer.from('fixture-image');
  try{
    const client=createMuseEngineClient({key,directory,fetchImpl:async(url,opts)=>{
      assert.equal(url,'http://127.0.0.1:47821/v1/assets/asset1/content');
      assert.equal(opts.redirect,'error');
      return new Response(data);
    }});
    const job={outputs:[{kind:'image',available:true,asset_id:'asset1',mime:'image/png',bytes:data.length,
      sha256:createHash('sha256').update(data).digest('hex')}]};
    assert.equal(await client.downloadImage(job,1),'图片已生成并保存至本机：'+path.join(directory,'images/task-1-asset1.png'));
    assert.deepEqual(readFileSync(path.join(directory,'images/task-1-asset1.png')),data);
    await assert.rejects(()=>client.downloadImage({outputs:[{...job.outputs[0],sha256:'0'.repeat(64)}]},2),/image_integrity_failed/);
  }finally{rmSync(directory,{recursive:true,force:true});}
});

function output(data,asset_id='asset1',digest=true){
  return {outputs:[{kind:'image',available:true,asset_id,mime:'image/png',bytes:data.length,
    ...(digest?{sha256:createHash('sha256').update(data).digest('hex')}:{})}]};
}
test('verified duplicate reuses original without network and conflict never overwrites',async()=>{
  const directory=mkdtempSync(path.join(tmpdir(),'pack-engine-reuse-'));
  const data=Buffer.from('original');let calls=0;
  try{
    const client=createMuseEngineClient({key,directory,fetchImpl:async()=>{calls++;return new Response(data);}});
    const job=output(data);
    const first=await client.downloadImage(job,1);
    assert.equal(await client.downloadImage(job,1),first);
    assert.equal(calls,1);
    await assert.rejects(()=>client.downloadImage(output(Buffer.from('modified')),1),/image_asset_conflict/);
    assert.equal(calls,1);
    assert.deepEqual(readFileSync(path.join(directory,'images/task-1-asset1.png')),data);
    assert.deepEqual(readdirSync(path.join(directory,'images')),['task-1-asset1.png']);
  }finally{rmSync(directory,{recursive:true,force:true});}
});
test('missing digest requires comparison with downloaded bytes and preserves old file',async()=>{
  const directory=mkdtempSync(path.join(tmpdir(),'pack-engine-no-digest-'));
  const data=Buffer.from('original');let calls=0,payload=data;
  try{
    const client=createMuseEngineClient({key,directory,fetchImpl:async()=>{calls++;return new Response(payload);}});
    const job=output(data,'asset1',false);
    const first=await client.downloadImage(job,1);
    assert.equal(await client.downloadImage(job,1),first);
    assert.equal(calls,2);
    payload=Buffer.from('modified');
    await assert.rejects(()=>client.downloadImage(job,1),/image_asset_conflict/);
    assert.equal(calls,3);
    assert.deepEqual(readFileSync(path.join(directory,'images/task-1-asset1.png')),data);
    assert.deepEqual(readdirSync(path.join(directory,'images')),['task-1-asset1.png']);
  }finally{rmSync(directory,{recursive:true,force:true});}
});
test('failed integrity and interrupted stream clean only newly created partial files',async()=>{
  const directory=mkdtempSync(path.join(tmpdir(),'pack-engine-partial-'));
  const data=Buffer.from('original');
  try{
    const dir=path.join(directory,'images');mkdirSync(dir,{mode:0o700});
    const old=path.join(dir,'legacy.part');writeFileSync(old,'preserve',{mode:0o600});
    const client=createMuseEngineClient({key,directory,fetchImpl:async()=>new Response(Buffer.from('modified'))});
    await assert.rejects(()=>client.downloadImage(output(data),1),/image_integrity_failed/);
    const interrupted=createMuseEngineClient({key,directory,fetchImpl:async()=>new Response(new ReadableStream({
      pull(controller){controller.error(Error('fixture_interrupted'));}
    }))});
    await assert.rejects(()=>interrupted.downloadImage(output(data),1),/fixture_interrupted/);
    assert.deepEqual(readdirSync(dir),['legacy.part']);
    assert.equal(readFileSync(old,'utf8'),'preserve');
  }finally{rmSync(directory,{recursive:true,force:true});}
});
test('unsafe paths and malformed metadata fail closed before network',async()=>{
  const directory=mkdtempSync(path.join(tmpdir(),'pack-engine-path-'));
  const data=Buffer.from('original');let calls=0;
  try{
    const dir=path.join(directory,'images'),outside=path.join(directory,'outside');
    mkdirSync(dir,{mode:0o700});writeFileSync(outside,data,{mode:0o600});
    symlinkSync(outside,path.join(dir,'task-1-asset1.png'));
    const client=createMuseEngineClient({key,directory,fetchImpl:async()=>{calls++;return new Response(data);}});
    await assert.rejects(()=>client.downloadImage(output(data),1),/image_storage_unsafe/);
    for(const patch of [{bytes:-1},{bytes:100*1024*1024+1},{sha256:'bad'},{mime:'text/html'}]){
      const job=output(data);Object.assign(job.outputs[0],patch);
      await assert.rejects(()=>client.downloadImage(job,2),/image_output_invalid/);
    }
    await assert.rejects(()=>client.downloadImage(output(data),'../bad'),/image_output_invalid/);
    assert.equal(calls,0);
    assert.deepEqual(readFileSync(outside),data);
  }finally{rmSync(directory,{recursive:true,force:true});}
});
test('concurrent identical downloads publish a single file without overwrite',async()=>{
  const directory=mkdtempSync(path.join(tmpdir(),'pack-engine-race-'));
  const data=Buffer.from('original');let calls=0;
  let release;const gate=new Promise(resolve=>{release=resolve;});
  try{
    const client=createMuseEngineClient({key,directory,fetchImpl:async()=>{
      calls++;if(calls===2)release();await gate;return new Response(data);
    }});
    const [first,second]=await Promise.all([client.downloadImage(output(data),1),client.downloadImage(output(data),1)]);
    assert.equal(first,second);assert.equal(calls,2);
    assert.deepEqual(readdirSync(path.join(directory,'images')),['task-1-asset1.png']);
    assert.deepEqual(readFileSync(path.join(directory,'images/task-1-asset1.png')),data);
  }finally{rmSync(directory,{recursive:true,force:true});}
});

test('noncanonical asset names are distinct from legacy sanitized and ordinary IDs',async()=>{
  const directory=mkdtempSync(path.join(tmpdir(),'pack-engine-asset-name-'));
  const data=Buffer.from('original');
  try{
    const client=createMuseEngineClient({key,directory,fetchImpl:async()=>new Response(data)});
    const a=await client.downloadImage(output(data,'a/b'),1);
    const b=await client.downloadImage(output(data,'a_b'),1);
    const c=await client.downloadImage(output(data,'a?b'),1);
    assert.equal(new Set([a,b,c]).size,3);
    assert.ok(a.includes('task-1-~hashed-'));
    assert.ok(b.endsWith('task-1-a_b.png'));
    assert.equal(readdirSync(path.join(directory,'images')).length,3);
  }finally{rmSync(directory,{recursive:true,force:true});}
});
