import test from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import {createMuseLegacyAdapter} from "../muse-legacy-adapter.mjs";

function fixture(result={code:0,stdout:'DRIVER-RESULT {"ok":true}',stderr:""}) {
  const directory=path.resolve("fixture-runtime");
  const calls=[],reads=[];
  const adapter=createMuseLegacyAdapter({
    directory,profileId:"Profile 3",spaceName:"pack-space",egoBin:"fixture-ego",
    timeoutMs:31000,maxOutputBytes:70000,maxInputBytes:80000,
    readSource:(file,encoding)=>{reads.push([file,encoding]);return path.basename(file);},
    runProcess:async(command,args,opts)=>{calls.push({command,args,opts});return result;}
  });
  return {adapter,calls,reads,directory};
}
test("legacy Muse task chat quota preserve exact driver composition and options",async()=>{
  const f=fixture(),controller=new AbortController();
  await f.adapter.executeTask("任务",{signal:controller.signal});
  await f.adapter.seedConversation("seed");
  await f.adapter.sendConversation("reply","https://muse.ai/thread/fixture");
  await f.adapter.readQuota();
  const expected=[
    ["driver-core.js",{message:"任务"},controller.signal],
    ["chat-driver.js",{message:"seed",action:"seed"},null],
    ["chat-driver.js",{message:"reply",action:"send",chatUrl:"https://muse.ai/thread/fixture"},null],
    ["quota-driver.js",{message:"",action:"quota"},null]
  ];
  for(let i=0;i<expected.length;i++){
    const [file,extra,signal]=expected[i],call=f.calls[i];
    const source="globalThis.PACK_TASK = "+JSON.stringify({profileId:"Profile 3",spaceName:"pack-space",...extra})+
      ";"+String.fromCharCode(10)+"driver-primitives.js"+String.fromCharCode(10)+file;
    assert.deepEqual(call,{command:"fixture-ego",args:["nodejs"],opts:{
      input:source,signal,cwd:f.directory,timeoutMs:31000,maxOutputBytes:70000,maxInputBytes:80000}});
    assert.deepEqual(f.reads.slice(i*2,i*2+2),[
      [path.join(f.directory,"driver-primitives.js"),"utf8"],[path.join(f.directory,file),"utf8"]]);
  }
  assert.equal(f.adapter.providerId,"muse");
  assert.deepEqual(f.adapter.capabilities,["legacy.task","legacy.chat","legacy.quota"]);
});
test("legacy Muse retains existing response parsing and no implicit retry",async()=>{
  const f=fixture({code:0,stdout:'debug'+String.fromCharCode(10)+'DRIVER-RESULT {"ok":false}',stderr:'DRIVER-RESULT {"ok":true,"reply":"answer"}'});
  assert.deepEqual(await f.adapter.executeTask("fixture"),{ok:true,reply:"answer"});
  for(const [result,error] of [
    [{code:2,stdout:"",stderr:""},/driver_exit_2/],
    [{code:0,stdout:"",stderr:""},/driver_no_result/],
    [{code:0,stdout:"DRIVER-RESULT invalid",stderr:""},/driver_result_not_json/]
  ]){
    const current=fixture(result);
    await assert.rejects(()=>current.adapter.executeTask("fixture"),error);
    assert.equal(current.calls.length,1);
  }
});
test("subprocess failure is not retried by Muse adapter",async()=>{
  let calls=0;
  const adapter=createMuseLegacyAdapter({directory:path.resolve("fixture"),readSource:()=>"",
    runProcess:async()=>{calls++;throw Error("driver_aborted");}});
  await assert.rejects(()=>adapter.executeTask("fixture"),/driver_aborted/);
  assert.equal(calls,1);
});
