// Trusted child-side client for the single-task Swift broker.
// exchange is the private byte-frame round trip, never a network URL or
// token loader. Reject malformed responses; never retry a failed frame.
export function createNativeHubTaskClient({taskId,exchange}={}){
  if(!Number.isSafeInteger(taskId)||taskId<=0||typeof exchange!=='function')
    throw Error('invalid_native_hub_client');
  const tools=new Set(['task_claim','collaboration_context_get','run_start','task_complete','task_block','native_egress_authorize','native_send_authorize']);
  let sequence=0,busy=false,terminal=false;
  return async function hubCall(tool,args){
    if(terminal||busy||sequence>=16||!tools.has(tool)||args?.task_id!==taskId)
      throw Error('native_hub_call_unavailable');
    const seq=++sequence;
    const frame=Buffer.from(JSON.stringify({version:1,sequence:seq,tool,arguments:args}));
    if(frame.length>32*1024)throw Error('native_hub_frame_too_large');
    busy=true;
    if(['task_complete','task_block'].includes(tool))terminal=true;
    try{
      const response=await exchange(frame);
      if(!Buffer.isBuffer(response)||response.length>64*1024)
        throw Error('invalid_native_hub_response');
      const value=JSON.parse(response.toString('utf8'));
      if(Object.keys(value??{}).sort().join(',')!=='result,sequence,version'||
         value.version!==1||value.sequence!==seq||!value.result||
         typeof value.result!=='object'||Array.isArray(value.result))
        throw Error('invalid_native_hub_response');
      if(tool==='task_claim'&&value.result.status==='unavailable')terminal=true;
      return value.result;
    }catch{
      if(tool==='task_claim')terminal=true;
      // The same task can report a known claim blocked after a failed
      // context/start, but the client never automatically repeats a call.
      throw Error('native_hub_outcome_unconfirmed');
    }finally{busy=false;}
  };
}

// These are local approval frames, never forwarded as Hub tools.
export function nativeTaskApprovals(hubCall,taskId){
  if(typeof hubCall!=='function'||!Number.isSafeInteger(taskId)||taskId<=0)
    throw Error('invalid_native_approval_client');
  return Object.freeze({
    authorizeEgress:async ({request})=>{
      if(request?.task_id!==taskId)return false;
      return (await hubCall('native_egress_authorize',{task_id:taskId}))?.approved===true;
    },
    authorizeNativeSend:async value=>{
      if(value?.taskID!==taskId)return false;
      return (await hubCall('native_send_authorize',{task_id:taskId,origin:value.origin,
        conversation_id:value.conversationID,text:value.text,sha256:value.textSHA256,
        deadline:value.deadline}))?.approved===true;
    }
  });
}
