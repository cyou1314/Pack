import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PassThrough,Writable} from 'node:stream';
import {createNativeTaskPipe} from '../browser-driver/native-task-pipe.mjs';

function frame(data){const header=Buffer.alloc(4);header.writeUInt32BE(data.length);return Buffer.concat([header,data]);}
test('private channel handles split headers and payload without retry',async()=>{
  const input=new PassThrough(),calls=[];
  const output=new Writable({write(chunk,_,done){
    calls.push(Buffer.from(chunk));done();
    const response=frame(Buffer.from('{"fixture":true}'));
    input.write(response.subarray(0,2));input.write(response.subarray(2,7));input.write(response.subarray(7));
  }});
  const pipe=createNativeTaskPipe({input,output});
  try{
    const request=Buffer.from('{}');
    assert.equal((await pipe.exchange(request)).toString(),'{"fixture":true}');
    assert.deepEqual(calls,[frame(request)]);
  }finally{await pipe.close();}
});
test('oversize header and partial EOF permanently close the channel',async()=>{
  for(const response of [Buffer.from([0,2,0,0]),Buffer.from([0,0,0,4,65])]){
    const input=new PassThrough();let calls=0;
    const output=new Writable({write(_,encoding,done){calls++;done();input.end(response);}});
    const pipe=createNativeTaskPipe({input,output});
    await assert.rejects(pipe.exchange(Buffer.from('{}')),/unconfirmed/);
    await assert.rejects(pipe.exchange(Buffer.from('{}')),/invalid_request/);
    assert.equal(calls,1);
    await pipe.close();
  }
});
