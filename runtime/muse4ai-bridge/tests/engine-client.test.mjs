import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,readFileSync} from 'node:fs';
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
