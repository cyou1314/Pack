import {readFileSync} from "node:fs";
import path from "node:path";
import {runBoundedProcess} from "./process-supervisor.mjs";

/**
 * Compatibility adapter for the existing Muse/ego runtime. No new login,
 * IPC, browser process, session migration or credentials. This is not
 * the verified native-account collaboration adapter.
 */
export function createMuseLegacyAdapter({
  directory, profileId, spaceName, egoBin="ego-browser",
  timeoutMs=360000,maxOutputBytes=8388608,maxInputBytes=8388608,
  runProcess=runBoundedProcess, readSource=readFileSync
}={}) {
  if(typeof directory!=="string"||!path.isAbsolute(directory)||
     typeof runProcess!=="function"||typeof readSource!=="function")
    throw new TypeError("invalid_muse_adapter_configuration");
  const newline=String.fromCharCode(10);
  async function invoke(file, message, signal, extra={}) {
    const primitives=readSource(path.join(directory,"driver-primitives.js"),"utf8");
    const core=readSource(path.join(directory,file),"utf8");
    const task={profileId,spaceName,message,...extra};
    const source="globalThis.PACK_TASK = "+JSON.stringify(task)+";"+newline+primitives+newline+core;
    const result=await runProcess(egoBin||"ego-browser",["nodejs"],{
      input:source,signal,cwd:directory,timeoutMs,maxOutputBytes,maxInputBytes
    });
    if(result.code!==0) throw Error("driver_exit_"+(result.code??result.signal??"unknown"));
    const line=(result.stdout+newline+result.stderr).split(newline)
      .filter(value=>value.startsWith("DRIVER-RESULT")).pop();
    if(!line) throw Error("driver_no_result");
    try{return JSON.parse(line.slice("DRIVER-RESULT".length).trim());}
    catch{throw Error("driver_result_not_json");}
  }
  return Object.freeze({
    providerId:"muse",
    executeTask(message,{signal}={}) {
      return invoke("driver-core.js",message,signal);
    },
    seedConversation(message) {
      return invoke("chat-driver.js",message,null,{action:"seed"});
    },
    sendConversation(message,chatUrl) {
      return invoke("chat-driver.js",message,null,{action:"send",chatUrl});
    },
    readQuota() {
      return invoke("quota-driver.js","",null,{action:"quota"});
    },
    // Legacy Muse does not expose reliable native account proof, structured
    // tool permissions or per-egress enforcement. Never infer those features.
    capabilities:Object.freeze(["legacy.task","legacy.chat","legacy.quota"])
  });
}
